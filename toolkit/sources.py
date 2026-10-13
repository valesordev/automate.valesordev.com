"""Where a repo's plugins, includes, and base roles come from: the repo itself, then its pinned deps.

A repo lists its dependencies in deps.json:

    {"<name>": {"repo": "<owner>/<repo> or a git URL", "ref": "<tag or commit>"}}

`make deps` checks each one out at its ref into .deps/<name>/. Lookups try the
repo first, then each dependency in deps.json order, so a repo can always
shadow what a dependency ships.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


def repo_root(argv: list[str]) -> Path:
    """`--root DIR` if given (and removed from argv), else the git toplevel of the working directory."""
    if "--root" in argv:
        i = argv.index("--root")
        root = argv[i + 1]
        del argv[i:i + 2]
        return Path(root).expanduser().resolve()
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             check=True).stdout.strip()
        return Path(top)
    except (subprocess.CalledProcessError, OSError):
        return Path.cwd().resolve()


def _rev(d: Path) -> str:
    try:
        rev = subprocess.run(["git", "-C", str(d), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(d), "status", "--porcelain"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return rev + ("-dirty" if dirty else "")
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


def _name(d: Path, repo: str = "") -> str:
    """A source's name: the last part of its repo (`owner/automate.example.com` -> `automate.example.com`)."""
    if not repo:
        try:
            repo = subprocess.run(["git", "-C", str(d), "remote", "get-url", "origin"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        except (subprocess.CalledProcessError, OSError):
            repo = ""
    name = repo.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1].removesuffix(".git")
    return name or d.name



def _git(d: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True)
    except OSError:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def origin(d: Path) -> str:
    """Where `d` comes from: `owner/name` for a GitHub remote, else the remote URL ("" without one)."""
    url = _git(d, "remote", "get-url", "origin")
    m = re.match(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else url


def tag_at_head(d: Path) -> str:
    """The tag on a clean HEAD, if any: what a release install was made from."""
    if _git(d, "status", "--porcelain"):
        return ""
    return _git(d, "describe", "--tags", "--exact-match", "HEAD")

class Sources:
    def __init__(self, root: Path):
        self.root = root
        f = root / "deps.json"
        deps = json.loads(f.read_text()) if f.is_file() else {}
        self.deps = [(n, root / ".deps" / n, spec) for n, spec in deps.items()]
        missing = [n for n, d, _ in self.deps if not d.is_dir()]
        if missing:
            raise SystemExit(f"{root}: dependencies not checked out: {', '.join(missing)} (run `make deps`)")

    def roots(self) -> list[Path]:
        return [self.root] + [d for _, d, _ in self.deps]

    def plugin(self, name: str) -> Path:
        for r in self.roots():
            if (r / "plugins" / name).is_dir():
                return r / "plugins" / name
        raise SystemExit(f"plugin {name!r} is in none of: {', '.join(map(str, self.roots()))}")

    def include(self, rel: str) -> Path:
        for r in self.roots():
            if (r / rel).is_file():
                return r / rel
        raise SystemExit(f"include {rel!r} is in none of: {', '.join(map(str, self.roots()))}")

    def provenance(self) -> dict[str, str]:
        """{source name: commit} for the repo and each dependency, in lookup order."""
        out = {_name(self.root): _rev(self.root)}
        for _, d, spec in self.deps:
            out[_name(d, spec.get("repo", ""))] = _rev(d)
        return out
