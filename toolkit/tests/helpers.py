"""Scratch repos built from fixtures/: `home` (the source repo) with `upstream` pinned in .deps/."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLKIT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run(*args, cwd=None):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, cwd=cwd)


def git(cwd, *args):
    r = run("git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args, cwd=cwd)
    assert r.returncode == 0, r.stderr
    return r.stdout


def init(d: Path):
    git(d, "init", "-q", "-b", "main")
    git(d, "add", ".")
    git(d, "commit", "-qm", "fixture")


class ToolkitCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        shutil.copytree(FIXTURES / "home", self.home)
        (self.home / ".gitignore").write_text(".deps/\n")
        init(self.home)
        self.dep = self.home / ".deps/upstream"
        shutil.copytree(FIXTURES / "upstream", self.dep)
        init(self.dep)
        self.dest = base / "dest"
        self.dest.mkdir()
        git(self.dest, "init", "-q")

    def tearDown(self):
        self.tmp.cleanup()

    def tool(self, script, *args, cwd=None):
        return run(sys.executable, TOOLKIT / script, "--root", self.home, *args, cwd=cwd)

    def install(self, *flags):
        return self.tool("install.py", *flags, "demo", self.dest)

    def edit(self, rel, old, new, root=None):
        f = (root or self.home) / rel
        text = f.read_text()
        self.assertIn(old, text, rel)
        f.write_text(text.replace(old, new, 1))
