#!/usr/bin/env python3
"""The public-readiness gate: nothing private in a public repo.

    check_public.py [--root DIR] [--denylist FILE | --no-denylist] [--commits RANGE] [--no-gitleaks]

Checks every tracked file (symlinks and binaries skipped) for:

- a private Notion link (`notion.so|com/…<32 hex>`, title-slug URLs included);
- a denylist entry: personal names, private page titles, hostnames, home paths.
  The denylist lives outside every repo: FILE, or $AUTOMATE_DENYLIST. One entry
  per line, case-insensitive, matched as a whole word or phrase; `re:<regex>`
  for a pattern; `#` comments. A hit names the entry's line number, never the
  entry, so CI logs don't publish it. No denylist is an error unless
  --no-denylist says so.

--commits RANGE (`origin/main..HEAD`; `HEAD` for all history) also checks those
commits' messages and every file version they introduced. Then `gitleaks git`
scans the whole history for secrets, redacted; `make tools` fetches the pinned
gitleaks into .tools/.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

NOTION = re.compile(r"notion\.(so|com)/(p/)?([\w-]*-)?[0-9a-f]{32}")


def git(root: Path, *args: str, binary=False):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode(errors="replace")


def load_denylist(path: Path) -> list[tuple[int, re.Pattern]]:
    entries = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        term = line.strip()
        if not term or term.startswith("#"):
            continue
        if term.startswith("re:"):
            entries.append((n, re.compile(term[3:], re.I)))
        else:
            words = r"\s+".join(map(re.escape, term.split()))
            entries.append((n, re.compile(rf"(?<!\w){words}(?!\w)", re.I)))
    if not entries:
        raise SystemExit(f"check_public: {path} has no entries")
    return entries


def scan(text: str, where: str, deny, out: list[str]):
    for i, line in enumerate(text.splitlines(), 1):
        if NOTION.search(line):
            out.append(f"{where}:{i}: private Notion link")
        out.extend(f"{where}:{i}: denylist entry {n}" for n, pat in deny if pat.search(line))


def text_of(data: bytes) -> str | None:
    return None if b"\0" in data[:8192] else data.decode(errors="replace")


def check_tree(root: Path, deny, out):
    for f in git(root, "ls-files", "-z").split("\0"):
        p = root / f
        if f and not p.is_symlink() and p.is_file() and (text := text_of(p.read_bytes())) is not None:
            scan(text, f, deny, out)


def check_commits(root: Path, rng: str, deny, out):
    for sha in git(root, "rev-list", rng).split():
        scan(git(root, "log", "-1", "--format=%B", sha), f"commit {sha[:12]} message", deny, out)
    paths = {}
    for line in git(root, "rev-list", "--objects", rng).splitlines():
        sha, _, path = line.partition(" ")
        paths.setdefault(sha, path)
    kinds = subprocess.run(["git", "-C", str(root), "cat-file", "--batch-check=%(objecttype) %(objectname)"],
                           input="\n".join(paths), capture_output=True, text=True, check=True).stdout
    for kind, sha in (line.split() for line in kinds.splitlines()):
        if kind == "blob" and (text := text_of(git(root, "cat-file", "blob", sha, binary=True))) is not None:
            scan(text, f"{paths[sha]} (blob {sha[:12]})", deny, out)


def gitleaks(root: Path) -> str:
    exe = os.environ.get("GITLEAKS") or shutil.which("gitleaks") or str(root / ".tools/gitleaks")
    if not Path(exe).exists() and not shutil.which(exe):
        raise SystemExit("check_public: gitleaks not found; run `make tools` (or pass --no-gitleaks)")
    r = subprocess.run([exe, "git", "--redact", "--no-banner", "--exit-code", "1", str(root)],
                       capture_output=True, text=True)
    return "" if r.returncode == 0 else (r.stdout + r.stderr).strip() or "gitleaks failed"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path.cwd())
    ap.add_argument("--denylist", type=Path, default=os.environ.get("AUTOMATE_DENYLIST") or None)
    ap.add_argument("--no-denylist", action="store_true")
    ap.add_argument("--commits")
    ap.add_argument("--no-gitleaks", action="store_true")
    a = ap.parse_args()
    root = a.root.resolve()
    if a.no_denylist:
        deny = []
    elif not a.denylist:
        raise SystemExit("check_public: no denylist; set AUTOMATE_DENYLIST to a file outside the repo "
                         "(or pass --no-denylist)")
    elif not a.denylist.is_file():
        raise SystemExit(f"check_public: denylist {a.denylist} not found")
    else:
        deny = load_denylist(a.denylist)
        if a.denylist.resolve().is_relative_to(root):
            raise SystemExit("check_public: the denylist must live outside the repo")
    out: list[str] = []
    check_tree(root, deny, out)
    if a.commits:
        check_commits(root, a.commits, deny, out)
    leaks = "" if a.no_gitleaks else gitleaks(root)
    for line in sorted(set(out)):
        print(line)
    if leaks:
        print(leaks)
    checked = ["Notion links", f"{len(deny)} denylist entries" if deny else "no denylist",
               *([f"commits {a.commits}"] if a.commits else []), *([] if a.no_gitleaks else ["gitleaks"])]
    print(f"check_public: {'FAIL' if out or leaks else 'ok'} ({', '.join(checked)})", file=sys.stderr)
    return 1 if out or leaks else 0


if __name__ == "__main__":
    sys.exit(main())
