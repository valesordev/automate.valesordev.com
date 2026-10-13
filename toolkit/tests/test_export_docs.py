"""export_docs.py: docs/ into a site's content/<name>/, links outside docs/ pinned to GitHub."""
import json

from helpers import ToolkitCase, git


class ExportDocsTest(ToolkitCase):
    def setUp(self):
        super().setUp()
        git(self.home, "remote", "add", "origin", "git@github.com:owner/home.git")
        self.sha = git(self.home, "rev-parse", "HEAD").strip()
        self.site = self.home.parent / "site"
        self.out = self.site / "content/home"

    def export(self, *flags):
        return self.tool("export_docs.py", *flags, self.site)

    def test_exports_and_pins_outside_links(self):
        r = self.export()
        self.assertEqual(r.returncode, 0, r.stderr)
        index = (self.out / "README.md").read_text()
        base = f"https://github.com/owner/home"
        self.assertIn("](guide/using.md#start)", index)
        self.assertIn(f"]({base}/blob/{self.sha}/plugins/tools/README.md#roles)", index)
        self.assertIn(f"]({base}/tree/{self.sha}/plugins/tools)", index)
        self.assertIn("](https://example.com/x)", index)
        self.assertIn("](#home-docs)", index)
        using = (self.out / "guide/using.md").read_text()
        self.assertIn("](../README.md)", using)
        self.assertIn(f"]({base}/blob/{self.sha}/plugins/tools/roles/helper.md)", using)
        self.assertIn("[kept](../../nowhere)", (self.out / "guide/notes.txt").read_text())
        record = json.loads((self.out / ".export.json").read_text())
        self.assertEqual(record["commit"], self.sha)
        self.assertEqual(record["files"], ["README.md", "guide/notes.txt", "guide/using.md"])

    def test_reexport_removes_what_the_source_dropped(self):
        self.assertEqual(self.export().returncode, 0)
        git(self.home, "rm", "-q", "docs/guide/notes.txt")
        git(self.home, "commit", "-qm", "drop")
        r = self.export()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("removed  content/home/guide/notes.txt", r.stdout)
        self.assertFalse((self.out / "guide/notes.txt").exists())

    def test_dry_run_writes_nothing(self):
        r = self.export("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would write  content/home/README.md", r.stdout)
        self.assertFalse(self.out.exists())

    def test_refuses_a_directory_it_does_not_own(self):
        self.out.mkdir(parents=True)
        (self.out / "hand.md").write_text("mine\n")
        r = self.export()
        self.assertEqual(r.returncode, 1)
        self.assertIn("has no .export.json", r.stderr)
        self.assertEqual((self.out / "hand.md").read_text(), "mine\n")

    def test_refuses_uncommitted_docs(self):
        self.edit("docs/guide/using.md", "Using it", "Using it, edited")
        r = self.export()
        self.assertEqual(r.returncode, 1)
        self.assertIn("uncommitted", r.stderr)

    def test_refuses_a_broken_link(self):
        self.edit("docs/guide/using.md", "../README.md", "../MISSING.md")
        git(self.home, "commit", "-qam", "break")
        r = self.export()
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/guide/using.md: broken link ../MISSING.md", r.stderr)
        self.assertFalse(self.out.exists())

    def test_non_github_origin_needs_repo(self):
        git(self.home, "remote", "set-url", "origin", "/srv/git/home.git")
        self.assertIn("pass --repo", self.export().stderr)
        r = self.export("--repo", "other/home")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("https://github.com/other/home/blob/", (self.out / "README.md").read_text())
