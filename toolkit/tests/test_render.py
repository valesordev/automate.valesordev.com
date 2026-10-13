"""render.py: {{include}} from the repo and from a pinned dependency, the marker, and --check."""
from helpers import ToolkitCase

OUT = "plugins/tools/skills/do-thing/references/rules.md"


class RenderTest(ToolkitCase):
    def test_tree_renders_includes_from_both_sources(self):
        r = self.tool("render.py", "--tree", self.home / "plugins/tools")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.home / OUT).read_text()
        self.assertTrue(text.startswith("<!-- GENERATED from plugins/tools/src/skills/do-thing/references/rules.md"))
        self.assertIn("One skill, one job.", text)
        self.assertIn("Pinned, never edited in place.", text)
        self.assertNotIn("{{", text)

    def test_check_catches_a_stale_render(self):
        r = self.tool("render.py", "--check", self.home / "plugins/tools")
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"stale render: {OUT}", r.stderr)
        self.tool("render.py", "--tree", self.home / "plugins/tools")
        self.assertEqual(self.tool("render.py", "--check", self.home / "plugins/tools").returncode, 0)
        self.edit("shared/upstream.md", "Pinned", "Moved", root=self.dep)   # the pin moved
        self.assertEqual(self.tool("render.py", "--check", self.home / "plugins/tools").returncode, 1)

    def test_the_repo_shadows_a_dependency_include(self):
        (self.home / "shared/upstream.md").write_text("Local copy.\n")
        self.tool("render.py", "--tree", self.home / "plugins/tools")
        text = (self.home / OUT).read_text()
        self.assertIn("Local copy.", text)
        self.assertNotIn("Pinned", text)

    def test_single_file_and_missing_include(self):
        src, dst = self.home / "instructions.md", self.home / "dist/out.md"
        src.write_text("Top\n{{include shared/rules.md}}\n")
        self.assertEqual(self.tool("render.py", src, dst).returncode, 0)
        self.assertIn("One skill, one job.", dst.read_text())
        src.write_text("{{include shared/nowhere.md}}\n")
        r = self.tool("render.py", src, dst)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("include 'shared/nowhere.md'", r.stderr)

    def test_include_cycle(self):
        (self.home / "shared/a.md").write_text("{{include shared/b.md}}\n")
        (self.home / "shared/b.md").write_text("{{include shared/a.md}}\n")
        src = self.home / "x.md"
        src.write_text("{{include shared/a.md}}\n")
        r = self.tool("render.py", src, self.home / "dist/x.md")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("include cycle", r.stderr)
