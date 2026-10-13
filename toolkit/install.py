#!/usr/bin/env python3
"""Vendor a target's agent config into a repo's .claude/ directory.

    install.py [--root DIR] <target> <dest-repo>          write/update
    install.py [--root DIR] --check <target> <dest-repo>  report drift, exit 1 if any

DIR is the source repo (default: the git toplevel of the working directory).
Targets come from DIR/targets/. Plugins come from DIR/plugins/ or, failing
that, from a dependency pinned in DIR/deps.json (see sources.py), so a target
can vendor a plugin another repo ships, and `extends` resolves against it.

Cloud sessions (Claude Code on the web) don't load plugins, only what's
committed under the repo's .claude/. So for Claude Code target repos, plugins
are vendored rather than installed:

    plugins/<p>/skills/*      -> .claude/skills/*
    plugins/<p>/bin/*         -> .claude/bin/*        (executable)
    plugins/<p>/workflows/*   -> .github/workflows/*  (GitHub Actions; install refuses to
                                 replace a workflow it doesn't manage)
    plugins/<p>/hooks/hooks.json -> merged into .claude/settings.json
                                 (${CLAUDE_PLUGIN_ROOT} -> ${CLAUDE_PROJECT_DIR}/.claude)
    targets/<t>/roles/*.md    -> .claude/roles/*   (`extends: <base>` is resolved
                                 against plugins/<p>/roles/<base>.md: base body
                                 first, then the target's body)
    targets/<t>/settings.json -> merged into .claude/settings.json: entries of
                                 permissions.allow/ask/deny (nothing else)

.claude/.automate.json records the source repo and commit, the source's
`repo` (`owner/name` on GitHub, else its remote URL) and `ref` (the tag at a clean
HEAD, if any: what `update-agents` reads), each dependency's commit (`sources`), every managed file's hash,
the managed hook commands, and the permission entries install added (an entry
that was already there stays the repo's own, and is never removed). Re-running replaces managed content and leaves
everything else in settings.json and .claude/ alone. A managed file edited in
the target is not overwritten; install stops and reports it.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources import Sources, origin, repo_root, tag_at_head  # noqa: E402

MANIFEST = ".claude/.automate.json"


def sha(p: Path | bytes) -> str:
    return hashlib.sha256(p if isinstance(p, bytes) else p.read_bytes()).hexdigest()


EXTENDS = re.compile(r"^extends:\s*(\S+)\s*$", re.M)


def compose_role(src: Path, plugins: list[str], sources: Sources) -> bytes:
    """Resolve `extends: <base>` in a role file against the target's plugins."""
    text = src.read_text()
    if not text.startswith("---\n"):
        return text.encode()
    end = text.index("\n---\n", 4)
    fm, body = text[4:end], text[end + 5 :]
    m = EXTENDS.search(fm)
    if not m:
        return text.encode()
    base_name = m.group(1)
    for p in plugins:
        base = sources.plugin(p) / "roles" / f"{base_name}.md"
        if base.is_file():
            btext = base.read_text()
            bbody = btext[btext.index("\n---\n", 4) + 5 :] if btext.startswith("---\n") else btext
            fm = EXTENDS.sub("", fm).strip("\n")
            return f"---\n{fm}\n---\n{bbody.rstrip()}\n\n{body.lstrip()}".encode()
    raise SystemExit(f"install: {src.name} extends '{base_name}', not found in plugins {plugins}")


def plan(sources: Sources, target: str) -> tuple[dict[str, bytes], dict]:
    """Return {dest_rel_path: content} and the hooks block to merge."""
    tdir = sources.root / "targets" / target
    if not (tdir / "target.json").is_file():
        raise SystemExit(f"install: no target {target!r} in {sources.root / 'targets'}")
    cfg = json.loads((tdir / "target.json").read_text())
    files: dict[str, bytes] = {}
    plugins = cfg.get("plugins", [])
    hooks: dict[str, list] = {}
    for p in plugins:
        pdir = sources.plugin(p)
        for sub, dest in (("skills", ".claude/skills"), ("bin", ".claude/bin"),
                          ("workflows", ".github/workflows")):
            base = pdir / sub
            if base.is_dir():
                for f in sorted(base.rglob("*")):
                    if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                        files[f"{dest}/{f.relative_to(base).as_posix()}"] = f.read_bytes()
        hj = pdir / "hooks" / "hooks.json"
        if hj.is_file():
            raw = hj.read_text().replace("${CLAUDE_PLUGIN_ROOT}", "${CLAUDE_PROJECT_DIR}/.claude")
            for event, groups in json.loads(raw).get("hooks", {}).items():
                hooks.setdefault(event, []).extend(groups)
    rdir = tdir / cfg.get("roles", "roles")
    if rdir.is_dir():
        for f in sorted(rdir.glob("*.md")):
            files[f".claude/roles/{f.name}"] = compose_role(f, plugins, sources)
    return files, hooks


PERMISSION_LISTS = ("allow", "ask", "deny")


def settings_fragment(sources: Sources, target: str) -> dict[str, list[str]]:
    """targets/<t>/settings.json as {"permissions.allow": [...], ...}; only permission lists."""
    f = sources.root / "targets" / target / "settings.json"
    if not f.is_file():
        return {}
    frag = json.loads(f.read_text())
    perms = frag.get("permissions", {})
    bad = [k for k in frag if k != "permissions"] + \
          [f"permissions.{k}" for k in perms if k not in PERMISSION_LISTS] + \
          [f"permissions.{k}" for k, v in perms.items() if k in PERMISSION_LISTS and
           not (isinstance(v, list) and all(isinstance(e, str) for e in v))]
    if bad:
        raise SystemExit(f"install: targets/{target}/settings.json may only hold lists of rules in "
                         f"permissions.allow/ask/deny; fix: {', '.join(bad)}")
    return {f"permissions.{k}": list(dict.fromkeys(v)) for k, v in perms.items() if v}


def merge_permissions(settings: dict, old: dict[str, list[str]],
                      new: dict[str, list[str]]) -> tuple[dict, dict[str, list[str]]]:
    """Drop entries install added that the source no longer has; add the source's new ones.

    Returns the settings and the entries install now owns. An entry the repo
    already had is left alone and never becomes managed."""
    perms = settings.get("permissions", {})
    owned: dict[str, list[str]] = {}
    for key in sorted(set(old) | set(new)):
        name = key.split(".", 1)[1]
        want, had = new.get(key, []), set(old.get(key, []))
        cur = [e for e in perms.get(name, []) if e not in had or e in want]
        mine = [e for e in want if e in had and e in cur]
        for e in want:
            if e not in cur:
                cur.append(e)
                mine.append(e)
        if cur:
            perms[name] = cur
        else:
            perms.pop(name, None)
        if mine:
            owned[key] = [e for e in want if e in mine]
    if perms:
        settings["permissions"] = perms
    else:
        settings.pop("permissions", None)
    return settings, owned


def hook_commands(hooks: dict) -> set[str]:
    return {h["command"] for groups in hooks.values() for g in groups for h in g.get("hooks", [])}


def merge_settings(settings: dict, old_cmds: set[str], new_hooks: dict) -> dict:
    cur = settings.get("hooks", {})
    out: dict[str, list] = {}
    for event, groups in cur.items():
        kept = [g for g in groups
                if not all(h.get("command") in old_cmds for h in g.get("hooks", []))]
        if kept:
            out[event] = kept
    for event, groups in new_hooks.items():
        out.setdefault(event, []).extend(groups)
    if out:
        settings["hooks"] = out
    else:
        settings.pop("hooks", None)
    return settings


def main(argv: list[str]) -> int:
    argv = list(argv)
    sources = Sources(repo_root(argv))
    check = "--check" in argv
    args = [a for a in argv if a != "--check"]
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    target, dest = args[0], Path(args[1]).expanduser().resolve()
    if not (dest / ".git").exists():
        print(f"install: {dest} is not a git repository root", file=sys.stderr)
        return 1

    files, hooks = plan(sources, target)
    fragment = settings_fragment(sources, target)
    mpath = dest / MANIFEST
    old = json.loads(mpath.read_text()) if mpath.is_file() else {"files": {}, "hook_commands": []}

    # Local edits to managed files in the target: never clobber silently.
    edited = [rel for rel, h in old["files"].items()
              if (dest / rel).is_file() and sha(dest / rel) != h]
    outdated = [rel for rel, src in files.items()
                if not (dest / rel).is_file() or sha(dest / rel) != sha(src)]
    stale = [rel for rel in old["files"] if rel not in files]
    # A workflow the repo already has under the same name isn't ours to replace.
    clash = [rel for rel, src in files.items() if rel.startswith(".github/")
             and rel not in old["files"] and (dest / rel).is_file() and sha(dest / rel) != sha(src)]

    spath = dest / ".claude" / "settings.json"
    settings = json.loads(spath.read_text()) if spath.is_file() else {}
    old_settings = old.get("settings", {})
    have = settings.get("permissions", {})
    missing = [f"{k}: {e}" for k, v in fragment.items() for e in v
               if e not in have.get(k.split(".", 1)[1], [])]
    dropped = [f"{k}: {e}" for k, v in old_settings.items() for e in v
               if e not in fragment.get(k, []) and e in have.get(k.split(".", 1)[1], [])]

    if check:
        for label, items in (("edited in target", edited), ("outdated/missing", outdated),
                             ("stale (removed at source)", stale),
                             ("settings missing", missing), ("settings stale", dropped)):
            for rel in items:
                print(f"{label:28} {rel}")
        drift = bool(edited or outdated or stale or missing or dropped)
        print("drift" if drift else f"in sync with {old.get('source', '?')}")
        return 1 if drift else 0

    if clash:
        print("install: these files exist in the target but aren't managed by it. Rename or "
              "remove them, then re-run:", file=sys.stderr)
        for rel in clash:
            print(f"  {rel}", file=sys.stderr)
        return 1

    if edited:
        print("install: these managed files were edited in the target. Move the change "
              f"into {next(iter(sources.provenance()))}, or delete the file, then re-run:", file=sys.stderr)
        for rel in edited:
            print(f"  {rel}", file=sys.stderr)
        return 1

    for rel in stale:
        f = dest / rel
        f.unlink(missing_ok=True)
        print(f"  removed  {rel}")
        d = f.parent
        while d not in (dest / ".claude", dest / ".github", dest) and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    for rel, content in files.items():
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
        if rel.startswith(".claude/bin/"):
            out.chmod(0o755)
        print(f"  {'wrote' if rel in outdated else 'same '}    {rel}")

    settings = merge_settings(settings, set(old.get("hook_commands", [])), hooks)
    settings, owned = merge_permissions(settings, old_settings, fragment)
    spath.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"  merged   .claude/settings.json (hooks{', permissions' if fragment or old_settings else ''})")

    prov = sources.provenance()
    name, rev = next(iter(prov.items()))
    repo, ref = origin(sources.root), tag_at_head(sources.root)
    manifest = {
        "source": f"{name}@{rev}",
        "target": target,
        **({"repo": repo} if repo else {}),
        **({"ref": ref} if ref else {}),
        "files": {rel: sha(dest / rel) for rel in files},
        "hook_commands": sorted(hook_commands(hooks)),
    }
    if len(prov) > 1:
        manifest["sources"] = prov
    if owned:
        manifest["settings"] = owned
    mpath.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"installed {target} -> {dest} ({manifest['source']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
