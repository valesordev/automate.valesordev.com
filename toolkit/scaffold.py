#!/usr/bin/env python3
"""Create a new repo from scaffolds/<name>/.

    scaffold.py [--root DIR] <name> <dest>          new repo: <dest> must not exist or be empty
    scaffold.py [--root DIR] --into <name> <dest>   existing git repo: adds the scaffold's files,
                                                    refusing if any of them already exists

Copies DIR/scaffolds/<name>/ (DIR: the git toplevel of the working directory) into <dest>, then (new repo only) runs
`git init -b main` there. After that, the repo owns its copy; this scaffold is
only the starting point.
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources import repo_root  # noqa: E402


def main(argv: list[str]) -> int:
    argv = list(argv)
    root = repo_root(argv)
    into = bool(argv) and argv[0] == "--into"
    if into:
        argv = argv[1:]
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    src = root / "scaffolds" / argv[0]
    dest = Path(argv[1]).expanduser().resolve()
    if not src.is_dir():
        print(f"scaffold: no scaffold named {argv[0]!r} (have: "
              f"{', '.join(p.name for p in (root / 'scaffolds').iterdir())})", file=sys.stderr)
        return 1
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    if into:
        if not (dest / ".git").exists():
            print(f"scaffold: --into needs an existing git repo; {dest} isn't one", file=sys.stderr)
            return 1
        clash = sorted(str(f.relative_to(src)) for f in src.rglob("*")
                       if f.is_file() and "__pycache__" not in f.parts
                       and (dest / f.relative_to(src)).exists())
        if clash:
            print(f"scaffold: {dest} already has: {', '.join(clash)}", file=sys.stderr)
            return 1
    elif dest.exists() and any(dest.iterdir()):
        print(f"scaffold: {dest} exists and isn't empty (use INTO=1 for an existing repo)", file=sys.stderr)
        return 1
    shutil.copytree(src, dest, dirs_exist_ok=True, ignore=ignore)
    if not into:
        subprocess.run(["git", "init", "-q", "-b", "main", str(dest)], check=True)
    print(f"scaffolded {argv[0]} -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
