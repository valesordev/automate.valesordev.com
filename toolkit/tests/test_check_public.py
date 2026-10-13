"""check_public.py: Notion links, the denylist (reported by line, never by term), commit history, gitleaks."""
import os
import random
import shutil
import string
from pathlib import Path

from helpers import TOOLKIT, ToolkitCase, git


class CheckPublicTest(ToolkitCase):
    def setUp(self):
        super().setUp()
        self.deny = Path(self.tmp.name) / "denylist"
        self.deny.write_text("# names\nQuill\n\nGrey Harbor\nre:/home/[a-z]+\n")

    def check(self, *flags, env=None):
        return self.tool("check_public.py", "--denylist", self.deny, "--no-gitleaks", *flags, env=env)

    def commit(self, rel, text, msg="add"):
        (self.home / rel).write_text(text)
        git(self.home, "add", rel)
        git(self.home, "commit", "-qm", msg)

    def test_clean_repo_passes(self):
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("3 denylist entries", r.stderr)

    def test_hits_name_the_entry_line_never_the_term(self):
        self.commit("docs/a.md", "Quillon is fine.\nAsk quill.\n\nthe grey\n  harbor gate\nsee /home/someone/x\n")
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines(), ["docs/a.md:2: denylist entry 2", "docs/a.md:6: denylist entry 5"])
        self.assertNotIn("quill", (r.stdout + r.stderr).lower())

    def test_phrase_matches_across_spaces_on_one_line(self):
        self.commit("docs/a.md", "Grey   harbor\n")
        self.assertIn("docs/a.md:1: denylist entry 4", self.check().stdout)

    def test_private_notion_links_including_title_slugs(self):
        self.commit("docs/a.md", "x https://app.notion.com/p/Some-Page-" + "a" * 32 + "\n")
        (self.home / "docs/link.md").symlink_to(self.home / "docs/a.md")
        git(self.home, "add", "docs/link.md")
        git(self.home, "commit", "-qm", "link")
        r = self.check()
        self.assertEqual(r.stdout.splitlines(), ["docs/a.md:1: private Notion link"])

    def test_commits_cover_messages_and_removed_files(self):
        self.commit("docs/a.md", "Quill\n")
        git(self.home, "rm", "-q", "docs/a.md")
        git(self.home, "commit", "-qm", "drop\n\nthanks Quill")
        self.assertEqual(self.check().returncode, 0)
        r = self.check("--commits", "HEAD~2..HEAD")
        self.assertEqual(r.returncode, 1)
        self.assertIn("denylist entry 2", r.stdout)
        self.assertRegex(r.stdout, r"commit [0-9a-f]{12} message:3: denylist entry 2")
        self.assertRegex(r.stdout, r"docs/a\.md \(blob [0-9a-f]{12}\):1: denylist entry 2")
        self.assertEqual(self.check("--commits", "HEAD~2").returncode, 0)

    def test_denylist_is_required_and_lives_outside_the_repo(self):
        env = {k: v for k, v in os.environ.items() if k != "AUTOMATE_DENYLIST"}
        r = self.tool("check_public.py", "--no-gitleaks", env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no denylist", r.stderr)
        self.assertEqual(self.tool("check_public.py", "--no-gitleaks", "--no-denylist", env=env).returncode, 0)
        inside = self.home / "deny.txt"
        shutil.copy(self.deny, inside)
        r = self.tool("check_public.py", "--no-gitleaks", "--denylist", inside)
        self.assertIn("outside the repo", r.stderr)

    def test_missing_gitleaks_is_an_error(self):
        env = {**os.environ, "GITLEAKS": str(Path(self.tmp.name) / "nope/gitleaks")}
        r = self.tool("check_public.py", "--denylist", self.deny, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("make tools", r.stderr)

    def test_gitleaks_finds_a_secret_in_history_redacted(self):
        exe = os.environ.get("GITLEAKS") or shutil.which("gitleaks") or str(TOOLKIT.parent / ".tools/gitleaks")
        if not Path(exe).exists():
            self.skipTest("gitleaks not installed (make tools)")
        token = "ghp_" + "".join(random.Random(7).choices(string.ascii_letters + string.digits, k=36))
        self.commit("docs/a.md", f"token = {token}\n")
        git(self.home, "rm", "-q", "docs/a.md")
        git(self.home, "commit", "-qm", "drop")
        r = self.tool("check_public.py", "--denylist", self.deny, env={**os.environ, "GITLEAKS": exe})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn(token, r.stdout + r.stderr)
        self.assertIn("gitleaks", r.stderr)
