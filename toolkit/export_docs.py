#!/usr/bin/env python3
"""Export a repo's docs/ as content for a docs site.

    export_docs.py [--root DIR] [--repo OWNER/NAME] [--dry-run] <site>

Copies DIR/docs/** to <site>/content/<name>/, where <name> is the repo's name
(`automate.valesordev.com`). The site repo renders and deploys it; commit it there.

- A link to a path outside docs/ (`../plugins/x/README.md`) becomes a GitHub URL
  at the exported commit, so it still resolves on the site. A link to a path
  that doesn't exist fails the export.
- docs/ must have no uncommitted changes: what's exported is a commit.
- <site>/content/<name>/.export.json records the source, the commit, and the
  files written. A re-export removes files the source no longer has. A
  directory without that record is never written to.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources import _name, repo_root  # noqa: E402

MARKER = ".export.json"
LINK = re.compile(r"(\]\()([^)\s]+)(\))")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def slug(root: Path) -> str:
    url = git(root, "remote", "get-url", "origin")
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?/?$", url)
    if not m:
        raise SystemExit(f"export_docs: origin {url!r} isn't on GitHub; pass --repo OWNER/NAME")
    return m.group(1)


def rewrite(text: str, src: Path, root: Path, base: str, commit: str, errors: list[str]) -> str:
    docs = root / "docs"

    def fix(m: re.Match) -> str:
        target = m.group(2)
        if re.match(r"[a-z][a-z0-9+.-]*:|#|/", target):
            return m.group(0)
        path, _, frag = target.partition("#")
        dest = (src.parent / path).resolve()
        if not dest.exists():
            errors.append(f"{src.relative_to(root)}: broken link {target}")
            return m.group(0)
        if dest.is_relative_to(docs):
            return m.group(0)
        if not dest.is_relative_to(root):
            errors.append(f"{src.relative_to(root)}: link leaves the repo: {target}")
            return m.group(0)
        kind = "tree" if dest.is_dir() else "blob"
        url = f"{base}/{kind}/{commit}/{dest.relative_to(root).as_posix()}"
        return f"{m.group(1)}{url}{'#' + frag if frag else ''}{m.group(3)}"

    return LINK.sub(fix, text)


def main(argv: list[str]) -> int:
    argv = list(argv)
    root = repo_root(argv)
    repo = None
    if "--repo" in argv:
        i = argv.index("--repo")
        repo = argv[i + 1]
        del argv[i:i + 2]
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    docs = root / "docs"
    if not docs.is_dir():
        print(f"export_docs: {root} has no docs/", file=sys.stderr)
        return 1
    if git(root, "status", "--porcelain", "--", "docs"):
        print("export_docs: docs/ has uncommitted changes; commit them first", file=sys.stderr)
        return 1
    name = _name(root)
    commit = git(root, "rev-parse", "HEAD")
    base = f"https://github.com/{repo or slug(root)}"
    out = Path(argv[0]).expanduser().resolve() / "content" / name

    errors: list[str] = []
    files: dict[str, bytes] = {}
    for f in sorted(p for p in docs.rglob("*") if p.is_file()):
        rel = f.relative_to(docs).as_posix()
        if f.suffix == ".md":
            files[rel] = rewrite(f.read_text(), f, root, base, commit, errors).encode()
        else:
            files[rel] = f.read_bytes()
    if errors:
        print("export_docs: " + "\n  ".join(["refusing:"] + errors), file=sys.stderr)
        return 1

    marker = out / MARKER
    old: list[str] = []
    if out.exists() and any(out.iterdir()):
        if not marker.is_file():
            print(f"export_docs: {out} exists and has no {MARKER}; refusing to write over it", file=sys.stderr)
            return 1
        old = json.loads(marker.read_text()).get("files", [])
    stale = sorted(set(old) - set(files))

    for rel in files:
        print(f"  {'would write' if dry else 'wrote'}  content/{name}/{rel}")
    for rel in stale:
        print(f"  {'would remove' if dry else 'removed'}  content/{name}/{rel}")
    if dry:
        return 0
    for rel in stale:
        (out / rel).unlink(missing_ok=True)
    for rel, data in files.items():
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(data)
    marker.write_text(json.dumps({"source": f"{name}@{commit[:7]}", "commit": commit,
                                  "files": sorted(files)}, indent=2) + "\n")
    print(f"exported docs/ -> {out} ({name}@{commit[:7]}); commit it in the site repo")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
