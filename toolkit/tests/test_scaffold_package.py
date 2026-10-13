"""scaffold.py (new repo, --into) and package.py (changed skills only)."""
import zipfile

from helpers import ToolkitCase


class ScaffoldTest(ToolkitCase):
    def test_new_repo(self):
        new = self.home.parent / "new"
        r = self.tool("scaffold.py", "starter", new)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((new / "docs/notes.md").is_file())
        self.assertTrue((new / ".git").is_dir())
        r = self.tool("scaffold.py", "starter", new)
        self.assertEqual(r.returncode, 1)
        self.assertIn("isn't empty", r.stderr)

    def test_into_an_existing_repo(self):
        r = self.tool("scaffold.py", "--into", "starter", self.dest)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.tool("scaffold.py", "--into", "starter", self.dest)
        self.assertEqual(r.returncode, 1)
        self.assertIn("already has", r.stderr)

    def test_unknown_scaffold(self):
        r = self.tool("scaffold.py", "nope", self.home.parent / "x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("have: starter", r.stderr)


class PackageTest(ToolkitCase):
    def test_packages_changed_skills_only(self):
        r = self.tool("package.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("packaging everything", r.stdout)
        z = self.home / "dist/skills/tools--do-thing.skill"
        self.assertIn("do-thing/SKILL.md", zipfile.ZipFile(z).namelist())
        self.assertEqual(self.tool("package.py", "--done").returncode, 0)
        self.assertIn("Skills: none changed.", self.tool("package.py").stdout)
        self.edit("plugins/tools/skills/do-thing/SKILL.md", "Steps.", "More steps.")
        out = self.tool("package.py").stdout
        self.assertIn("dist/skills/tools--do-thing.skill", out)
        self.assertIn("Skills to upload (1)", out)
