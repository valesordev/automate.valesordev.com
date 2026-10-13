#!/usr/bin/env python3
"""Package claude.ai uploads: only the skills and Project instructions that changed.

    package.py [--root DIR] [--all]   zip changed skills to dist/skills/, list changed project.md files
    package.py [--root DIR] --done    record the current state as uploaded

DIR is the repo (default: the git toplevel of the working directory).

State lives in .claude-ai-uploads.json (gitignored, survives `make clean`). With no
state file, everything counts as changed.
"""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources import repo_root  # noqa: E402

ROOT = DIST = OUT = STATE = Path()   # set by main() from --root


def digest(paths, base):
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(str(p.relative_to(base)).encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def current():
    items = {}
    for d in sorted(ROOT.glob("plugins/*/skills/*/")):
        files = [p for p in d.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
        items[f"skill:{d.parent.parent.name}--{d.name}"] = (digest(files, d), d)
    for f in sorted(DIST.glob("*/project.md")):
        items[f"project:{f.parent.name}"] = (digest([f], f.parent), f)
    return items


def main(argv):
    global ROOT, DIST, OUT, STATE
    argv = list(argv)
    ROOT = repo_root(argv)
    DIST, STATE = ROOT / "dist", ROOT / ".claude-ai-uploads.json"
    OUT = DIST / "skills"
    items = current()
    if "--done" in argv:
        STATE.write_text(json.dumps({k: h for k, (h, _) in items.items()}, indent=2) + "\n")
        print(f"Recorded {len(items)} items as uploaded ({STATE.name}).")
        return 0

    seen = {} if "--all" in argv or not STATE.exists() else json.loads(STATE.read_text())
    changed = [k for k, (h, _) in items.items() if seen.get(k) != h]
    removed = [k for k in seen if k not in items]

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.skill"):
        old.unlink()
    skills = [k for k in changed if k.startswith("skill:")]
    projects = [k for k in changed if k.startswith("project:")]
    for k in skills:
        d = items[k][1]
        with zipfile.ZipFile(OUT / f"{k[6:]}.skill", "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(d.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts:
                    z.write(p, Path(d.name) / p.relative_to(d))

    if not STATE.exists():
        print("No upload record yet: packaging everything.")
    print(f"Skills to upload ({len(skills)}):" if skills else "Skills: none changed.")
    for k in skills:
        print(f"  {OUT.relative_to(ROOT)}/{k[6:]}.skill")
    if projects:
        print(f"Project instructions to re-paste ({len(projects)}):")
        for k in projects:
            print(f"  {items[k][1].relative_to(ROOT)}")
    if removed:
        print("Gone from the repo (delete on claude.ai):")
        for k in removed:
            print(f"  {k.split(':', 1)[1]}")
    if skills or projects or removed:
        print("After uploading: make package-done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
