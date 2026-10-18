#!/usr/bin/env python3
"""Tool definitions: snapshot a CLI's commands, and derive the permission rules a role gets for it.

    tool_perms.py [--root DIR] snapshot <tool>|<path/to/tool.md>   run the definition's `catalog` (needs the CLI)
    tool_perms.py [--root DIR] --build                             write every <tool>.permissions.json
    tool_perms.py [--root DIR] --check                             exit 1 if any is stale

A tool definition is plugins/<p>/tools/<tool>.md or targets/<t>/tools/<tool>.md:
frontmatter (see `parse`) and a body of usage notes. Next to it:

    <tool>.commands.txt       the snapshot: `# <version>`, then one command path per line. Written by
                              `snapshot` from the definition's `catalog`, or by hand for a CLI without one.
    <tool>.permissions.json   generated from the snapshot by --build: a command path whose last word
                              matches `read_verbs` (fnmatch) becomes an `allow` rule, everything else an
                              `ask` rule, and so does any path under `always_ask`. Never hand-edit it.

Permission rules match a command's start, and some CLIs put the verb last (`gcx dashboards list`), so
the rules name every command path. `Bash(<path>:*)` matches on a word boundary (`… list` doesn't
match `… list-versions`), and an ask rule wins over an allow rule that also matches.
"""
from __future__ import annotations

import fnmatch
import json
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources import repo_root  # noqa: E402

KINDS = ("cli",)
# Keys a definition with `extends:` may not set: the permissions belong to the definition that
# holds the snapshot.
BASE_ONLY = ("catalog", "read_verbs", "always_ask")


# ------------------------------------------------------------------ frontmatter

def _scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1]
    return v


def inline_list(v: str) -> list:
    """`[a, 'b c', d]`: items split on commas outside quotes."""
    items, cur, quote = [], "", None
    for c in v.strip()[1:-1]:
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == ",":
            items.append(cur); cur = ""; continue
        cur += c
    items.append(cur)
    return [_scalar(i) for i in items if i.strip()]


def _value(v: str):
    v = v.strip()
    return inline_list(v) if v.startswith("[") and v.endswith("]") else _scalar(v)


def parse(text: str) -> tuple[dict, str]:
    """Frontmatter and body. The frontmatter is a YAML subset: `key: value`, `key: [a, b]`, and a
    `key:` followed by more-indented `- item` lines (a list) or `sub: value` lines (a map whose
    values are scalars, inline lists, or `- item` lines indented further). ` #` starts a comment
    outside quotes."""
    if not text.startswith("---\n") or "\n---\n" not in text[3:]:
        return {}, text
    end = text.index("\n---\n", 3)
    lines = [_uncomment(l) for l in text[4:end].splitlines()]
    lines = [l for l in lines if l.strip()]
    meta: dict = {}
    i = 0

    def block(i: int, indent: int):
        """Parse the lines more indented than `indent` starting at i: a list or a map."""
        if i >= len(lines) or _indent(lines[i]) <= indent:
            return "", i
        own = _indent(lines[i])
        if lines[i].strip().startswith("- "):
            out = []
            while i < len(lines) and _indent(lines[i]) == own and lines[i].strip().startswith("- "):
                out.append(_scalar(lines[i].strip()[2:])); i += 1
            return out, i
        out = {}
        while i < len(lines) and _indent(lines[i]) == own:
            k, _, v = lines[i].strip().partition(":")
            i += 1
            if v.strip():
                out[k.strip()] = _value(v)
            else:
                out[k.strip()], i = block(i, own)
        return out, i

    while i < len(lines):
        line = lines[i]
        if _indent(line):
            raise ValueError(f"frontmatter: unexpected indented line {line.strip()!r}")
        k, sep, v = line.partition(":")
        if not sep:
            raise ValueError(f"frontmatter: no `key:` in {line.strip()!r}")
        i += 1
        if v.strip():
            meta[k.strip()] = _value(v)
        else:
            meta[k.strip()], i = block(i, 0)
    return meta, text[end + 5:]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _uncomment(line: str) -> str:
    quote = None
    for j, c in enumerate(line):
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == "#" and (j == 0 or line[j - 1] == " "):
            return line[:j].rstrip()
    return line.rstrip()


def listed(v) -> list[str]:
    return [v] if isinstance(v, str) and v else list(v or [])


# ------------------------------------------------------------------- snapshot

def catalog_paths(output: str) -> list[str]:
    """Leaf command paths from a catalog's output: JSON (every object's `full_path`, at any depth)
    or plain text (one path per line). A leaf is a path no other path extends."""
    try:
        doc = json.loads(output)
    except json.JSONDecodeError:
        paths = [l.strip() for l in output.splitlines() if l.strip() and not l.startswith("#")]
    else:
        paths = []

        def walk(o):
            if isinstance(o, dict):
                if isinstance(o.get("full_path"), str):
                    paths.append(o["full_path"].strip())
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(doc)
    paths = sorted(set(" ".join(p.split()) for p in paths))
    parents = {" ".join(p.split()[:n]) for p in paths for n in range(1, len(p.split()))}
    return [p for p in paths if p not in parents]


def snapshot(defn: Path) -> Path:
    meta, _ = parse(defn.read_text())
    if not meta.get("catalog"):
        raise SystemExit(f"{defn}: no `catalog:`; write {defn.stem}.commands.txt by hand")
    def run(cmd: str) -> str:
        r = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise SystemExit(f"{defn}: `{cmd}` failed ({r.returncode}): {r.stderr.strip()[:400]}")
        return r.stdout
    version = run(meta["check"]).strip().splitlines()[0] if meta.get("check") else "unknown version"
    paths = catalog_paths(run(meta["catalog"]))
    if not paths:
        raise SystemExit(f"{defn}: `{meta['catalog']}` listed no commands")
    out = defn.with_name(f"{defn.stem}.commands.txt")
    out.write_text(f"# {version}\n" + "".join(p + "\n" for p in paths))
    return out


# --------------------------------------------------------------------- derive

def read_snapshot(f: Path) -> tuple[str, list[str]]:
    lines = f.read_text().splitlines()
    version = lines[0][1:].strip() if lines and lines[0].startswith("#") else ""
    return version, [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def classify(meta: dict, paths: list[str]) -> tuple[list[str], list[str]]:
    """(allow, ask) command paths."""
    verbs = listed(meta.get("read_verbs"))
    always = [" ".join(a.split()) for a in listed(meta.get("always_ask"))]
    allow, ask = [], []
    for p in paths:
        forced = any(p == a or p.startswith(a + " ") for a in always)
        read = any(fnmatch.fnmatchcase(p.split()[-1], v) for v in verbs)
        (allow if read and not forced else ask).append(p)
    return allow, ask


def permissions(defn: Path) -> dict | None:
    """The generated permissions for a definition with a snapshot, or None without one."""
    snap = defn.with_name(f"{defn.stem}.commands.txt")
    if not snap.is_file():
        return None
    meta, _ = parse(defn.read_text())
    version, paths = read_snapshot(snap)
    allow, ask = classify(meta, paths)
    return {
        "_generated": f"from {snap.name} by `make build`; edit {defn.name} or re-snapshot, not this file",
        "version": version,
        "allow": [f"Bash({p}:*)" for p in allow],
        "ask": [f"Bash({p}:*)" for p in ask],
    }


def definitions(root: Path) -> list[Path]:
    return sorted([*root.glob("plugins/*/tools/*.md"), *root.glob("targets/*/tools/*.md")])


def render(perms: dict) -> str:
    return json.dumps(perms, indent=1) + "\n"


def build(root: Path, check: bool) -> int:
    stale = []
    for d in definitions(root):
        out = d.with_name(f"{d.stem}.permissions.json")
        perms = permissions(d)
        if perms is None:
            if out.is_file():
                stale.append(f"{out.relative_to(root)} (no {d.stem}.commands.txt beside it)")
                if not check:
                    out.unlink()
            continue
        text = render(perms)
        if check:
            if not out.is_file() or out.read_text() != text:
                stale.append(str(out.relative_to(root)))
        elif not out.is_file() or out.read_text() != text:
            out.write_text(text)
            print(f"  {out.relative_to(root)}")
    for s in stale:
        print(f"stale tool permissions: {s} (run `make build`)", file=sys.stderr)
    return 1 if check and stale else 0


def find(root: Path, name: str) -> Path:
    p = Path(name)
    if p.suffix == ".md":
        return p if p.is_absolute() else (Path.cwd() / p).resolve()
    hits = [d for d in definitions(root) if d.stem == name and not parse(d.read_text())[0].get("extends")]
    if len(hits) != 1:
        where = ", ".join(str(h.relative_to(root)) for h in hits) or "none"
        raise SystemExit(f"tool {name!r}: {len(hits)} definitions ({where}); pass the path to one")
    return hits[0]


def main(argv: list[str]) -> int:
    argv = list(argv)
    root = repo_root(argv)
    if argv[:1] == ["snapshot"] and len(argv) == 2:
        out = snapshot(find(root, argv[1]))
        print(f"  {out.relative_to(root) if out.is_relative_to(root) else out}")
        return build(root, check=False)
    if argv in (["--build"], ["--check"]):
        return build(root, check=argv[0] == "--check")
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
