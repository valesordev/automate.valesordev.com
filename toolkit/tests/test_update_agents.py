"""toolkit/update-agents (vendored as .claude/bin/update-agents): install a repo's agents from a signed tag of its agent-config repo, then branch, commit, PR."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

TOOLKIT = Path(__file__).resolve().parent.parent
TOOL = TOOLKIT / "update-agents"
FIXTURES = TOOLKIT / "tests/fixtures"

# The agent-config repo's Makefile, as the real ones behave: deps, install, check-target.
MAKEFILE = f"""\
deps:
\t@rm -rf .deps/upstream && mkdir -p .deps && cp -r {FIXTURES}/upstream .deps/upstream
install:
\t@python3 {TOOLKIT}/install.py --root $(CURDIR) $(TARGET) $(DEST)
check-target:
\t@python3 {TOOLKIT}/install.py --root $(CURDIR) --check $(TARGET) $(DEST)
"""


@unittest.skipUnless(shutil.which("ssh-keygen") and shutil.which("make"), "needs ssh-keygen and make")
class UpdateAgentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        key = base / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "t@t", "-f", str(key)], check=True)
        (base / "allowed").write_text(f"t@t namespaces=\"git\" {(base / 'key.pub').read_text()}")
        (base / "gitconfig").write_text(
            "[user]\n\tname = t\n\temail = t@t\n\tsigningkey = " + str(key) + "\n"
            "[gpg]\n\tformat = ssh\n[gpg \"ssh\"]\n\tallowedSignersFile = " + str(base / "allowed") + "\n"
            "[commit]\n\tgpgsign = false\n[tag]\n\tgpgSign = false\n[init]\n\tdefaultBranch = main\n"
            "[gc]\n\tauto = 0\n[maintenance]\n\tauto = false\n")
        bindir = base / "bin"
        bindir.mkdir()
        self.gh_log = base / "gh.log"
        (bindir / "gh").write_text(f"#!/bin/sh\necho \"$@\" >> {self.gh_log}\necho https://gh/pull/1\n")
        (bindir / "gh").chmod(0o755)
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": str(base / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
                    "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
        self.env.pop("GIT_DIR", None)

        self.src = base / "automate.acme.example"           # the agent-config repo
        shutil.copytree(FIXTURES / "home", self.src)
        (self.src / ".gitignore").write_text(".deps/\n")
        (self.src / "Makefile").write_text(MAKEFILE)
        self.git(self.src, "init", "-q")
        self.commit(self.src, "v1")
        self.tag("v1")

        self.origin = base / "origin.git"                    # where the repo pushes
        self.git(base, "init", "-q", "--bare", str(self.origin))
        self.repo = base / "repo"                            # the repo that runs the agents
        self.repo.mkdir()
        (self.repo / "README.md").write_text("repo\n")
        self.git(self.repo, "init", "-q")
        self.commit(self.repo, "init")
        self.git(self.repo, "remote", "add", "origin", str(self.origin))
        self.git(self.repo, "push", "-q", "-u", "origin", "main")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, cwd, *args):
        r = subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, cwd, msg):
        self.git(cwd, "add", "-A")
        self.git(cwd, "commit", "-qm", msg)

    def tag(self, name, signed=True):
        self.git(self.src, "tag", "-s" if signed else "-a", name, "-m", name)

    def run_tool(self, *args):
        return subprocess.run(["python3", str(TOOL), *args], cwd=self.repo, env=self.env,
                              capture_output=True, text=True)

    def first(self, *extra):
        return self.run_tool("v1", "--repo", str(self.src), "--target", "demo", *extra)

    def manifest(self):
        return json.loads((self.repo / ".claude/.automate.json").read_text())

    def test_first_install_branches_commits_pushes_and_opens_a_pr(self):
        r = self.first()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "chore/update-agents-v1")
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "")
        self.assertIn(f"chore: update agents to {self.src}@v1", self.git(self.repo, "log", "-1", "--format=%s"))
        self.assertTrue((self.repo / ".claude/skills/do-thing/SKILL.md").is_file())
        self.assertIn("chore/update-agents-v1", self.git(self.origin, "branch"))
        gh = self.gh_log.read_text()
        self.assertIn("pr create --base main --head chore/update-agents-v1", gh)
        m = self.manifest()
        self.assertEqual((m["repo"], m["ref"], m["target"]), (str(self.src), "v1", "demo"))

    def test_a_later_tag_reads_repo_and_target_from_the_install(self):
        self.assertEqual(self.first().returncode, 0)
        self.git(self.repo, "switch", "-q", "main")
        self.git(self.repo, "merge", "-q", "--ff-only", "chore/update-agents-v1")
        self.git(self.repo, "push", "-q", "origin", "main")
        skill = self.src / "plugins/tools/skills/do-thing/SKILL.md"
        skill.write_text(skill.read_text() + "\nA v2 step.\n")
        self.commit(self.src, "v2")
        self.tag("v2")
        r = self.run_tool("v2", "--no-pr")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("A v2 step.", (self.repo / ".claude/skills/do-thing/SKILL.md").read_text())
        self.assertEqual(self.manifest()["ref"], "v2")
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "chore/update-agents-v2")
        self.assertNotIn("v2", self.git(self.origin, "branch"))      # --no-pr: nothing pushed
        self.assertNotIn("update-agents-v2", self.gh_log.read_text())

    def test_the_same_tag_again_commits_nothing(self):
        self.assertEqual(self.first().returncode, 0)
        self.git(self.repo, "switch", "-q", "main")
        self.git(self.repo, "merge", "-q", "--ff-only", "chore/update-agents-v1")
        self.git(self.repo, "push", "-q", "origin", "main")
        self.git(self.repo, "branch", "-q", "-D", "chore/update-agents-v1")
        r = self.run_tool("v1")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("already at", r.stdout)
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "main")

    def test_an_unsigned_tag_is_refused_and_nothing_changes(self):
        self.tag("v1-unsigned", signed=False)
        r = self.run_tool("v1-unsigned", "--repo", str(self.src), "--target", "demo")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("signature doesn't verify", r.stderr)
        self.assertFalse((self.repo / ".claude").exists())
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "main")

    def test_a_lightweight_tag_is_refused(self):
        self.git(self.src, "tag", "v1-light")
        r = self.run_tool("v1-light", "--repo", str(self.src), "--target", "demo")
        self.assertEqual(r.returncode, 1)
        self.assertIn("lightweight tag", r.stderr)

    def test_a_tag_signed_by_an_untrusted_key_is_refused(self):
        (Path(self.tmp.name) / "allowed").write_text("")
        r = self.first()
        self.assertEqual(r.returncode, 1)
        self.assertIn("allowedSignersFile", r.stderr)

    def test_preconditions(self):
        r = self.run_tool()
        self.assertEqual(r.returncode, 1)
        self.assertIn("usage", r.stderr)
        r = self.run_tool("v1", "--target", "demo")
        self.assertIn("names no repo", r.stderr)
        r = self.run_tool("v9", "--repo", str(self.src), "--target", "demo")
        self.assertIn("has no tag v9", r.stderr)
        (self.repo / "README.md").write_text("edited\n")
        self.assertIn("working tree has changes", self.first().stderr)
        self.git(self.repo, "checkout", "-q", "--", "README.md")
        self.git(self.repo, "switch", "-q", "-c", "work")
        self.assertIn("switch to main first", self.first().stderr)
        self.git(self.repo, "switch", "-q", "main")
        (self.repo / "NEW.md").write_text("x\n")
        self.commit(self.repo, "ahead")
        self.assertIn("differs from origin/main", self.first().stderr)

    def test_a_failed_install_commits_nothing(self):
        (self.repo / ".claude/skills/do-thing").mkdir(parents=True)
        self.assertEqual(self.first().returncode, 0)
        self.git(self.repo, "switch", "-q", "main")
        self.git(self.repo, "merge", "-q", "--ff-only", "chore/update-agents-v1")
        skill = self.repo / ".claude/skills/do-thing/SKILL.md"
        skill.write_text("edited in the repo\n")                  # a managed file edited here
        self.commit(self.repo, "local edit")
        self.git(self.repo, "push", "-q", "origin", "main")
        self.git(self.src, "commit", "-q", "--allow-empty", "-m", "v2")
        self.tag("v2")
        r = self.run_tool("v2")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("make install", r.stderr)
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "main")
        self.assertEqual(skill.read_text(), "edited in the repo\n")


if __name__ == "__main__":
    unittest.main()
