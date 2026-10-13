"""check_conventions.py: passes on the fixture, and catches each convention it checks."""
from helpers import ToolkitCase


class ConventionsTest(ToolkitCase):
    def check(self):
        return self.tool("check_conventions.py")

    def test_fixture_passes(self):
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("conventions ok", r.stdout)

    def test_missing_examples(self):
        (self.home / "plugins/tools/skills/do-thing/references/examples.md").unlink()
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertIn("plugins/tools/skills/do-thing: missing references/examples.md", r.stdout)

    def test_base_role_without_assist_level(self):
        self.edit("plugins/tools/roles/helper.md", "Assist level:", "Assist:")
        r = self.check()
        self.assertIn("helper.md: no 'Assist level", r.stdout)
        self.assertIn("targets/demo/roles/dev.md: composed role has no", r.stdout)

    def test_assist_level_after_first_section_does_not_count(self):
        f = self.home / "plugins/tools/roles/helper.md"
        text = f.read_text()
        line = next(x for x in text.splitlines() if x.startswith("Assist level:"))
        f.write_text(text.replace(line, "", 1) + "\n## Later\n\n" + line + "\n")
        self.assertIn("helper.md: no 'Assist level", self.check().stdout)

    def test_a_base_role_from_a_dependency_counts(self):
        self.assertNotIn("lead.md", self.check().stdout)
        self.edit("plugins/upstream/roles/base-lead.md", "Assist level:", "Assist:", root=self.dep)
        self.assertIn("targets/demo/roles/lead.md: composed role has no", self.check().stdout)

    def test_readme_row_missing_or_mismatched(self):
        self.edit("plugins/tools/README.md", "| `helper` | L2", "| `helper` | L3")
        self.assertIn("`helper` is L3 in the table but L2", self.check().stdout)
        self.edit("plugins/tools/README.md", "| `helper` |", "| helper |")
        self.assertIn("no roles-table row for `helper` (roles)", self.check().stdout)

    def test_skill_upload_limits(self):
        skill = "plugins/tools/skills/do-thing/SKILL.md"
        cases = (("name: do-thing", "name: other-thing", "isn't the directory name"),
                 ("name: do-thing", "name: Do_Thing", "won't upload"),
                 ("description: Do the thing", "description: Do <the> thing", "has < or >"),
                 ("description: Do the thing when asked to do the thing. Not for other things.",
                  "description: " + "x" * 1025, "over 1024"))
        for old, new, msg in cases:
            with self.subTest(msg=msg):
                f = self.home / skill
                orig = f.read_text()
                self.edit(skill, old, new)
                r = self.check()
                self.assertEqual(r.returncode, 1)
                self.assertIn(msg, r.stdout)
                f.write_text(orig)
