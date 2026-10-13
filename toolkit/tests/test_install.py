"""install.py: vendoring a target, with plugins and base roles from the repo and from a pinned dependency."""
import json
import os
import shutil

from helpers import ToolkitCase, git, run, sys, TOOLKIT


class InstallTest(ToolkitCase):
    def setUp(self):
        super().setUp()
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.manifest = json.loads((self.dest / ".claude/.automate.json").read_text())

    def settings(self):
        return json.loads((self.dest / ".claude/settings.json").read_text())

    def test_layout_from_both_sources(self):
        for rel in (".claude/skills/do-thing/SKILL.md", ".claude/skills/up-skill/SKILL.md",
                    ".claude/roles/_repo.md", ".claude/roles/dev.md", ".claude/roles/lead.md",
                    ".github/workflows/flow.yml"):
            self.assertTrue((self.dest / rel).is_file(), rel)
        for rel in (".claude/bin/tool", ".claude/bin/up"):
            self.assertTrue(os.access(self.dest / rel, os.X_OK), rel)
        self.assertFalse((self.dest / ".claude/skills/do-thing/src").exists())
        cmd = self.settings()["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertEqual(cmd, 'python3 "${CLAUDE_PROJECT_DIR}/.claude/bin/tool" pre-bash')

    def test_extends_resolves_in_the_repo_and_in_a_dependency(self):
        dev = (self.dest / ".claude/roles/dev.md").read_text()
        self.assertNotIn("extends:", dev)
        self.assertLess(dev.index("Helper base body."), dev.index("Dev in the demo repo."))
        lead = (self.dest / ".claude/roles/lead.md").read_text()
        self.assertLess(lead.index("Lead base body."), lead.index("Lead in the demo repo."))
        self.assertIn("Assist level: L3", lead)

    def test_manifest_records_each_source(self):
        home_rev = git(self.home, "rev-parse", "--short", "HEAD").strip()
        dep_rev = git(self.dep, "rev-parse", "--short", "HEAD").strip()
        self.assertEqual(self.manifest["source"], f"home@{home_rev}")
        self.assertEqual(self.manifest["sources"], {"home": home_rev, "automate.upstream.example": dep_rev})
        self.assertEqual(self.manifest["target"], "demo")
        self.assertIn(".claude/bin/up", self.manifest["files"])

    def test_the_repo_shadows_a_dependency(self):
        shutil.copytree(self.dep / "plugins/upstream", self.home / "plugins/upstream")
        self.edit("plugins/upstream/bin/up", "echo up", "echo mine")
        self.assertEqual(self.install().returncode, 0)
        self.assertIn("echo mine", (self.dest / ".claude/bin/up").read_text())

    def test_reinstall_is_idempotent_and_check_is_clean(self):
        sp = self.dest / ".claude/settings.json"
        s = self.settings()
        s["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "echo mine"}]}]
        sp.write_text(json.dumps(s))
        self.assertEqual(self.install().returncode, 0)
        s = self.settings()
        self.assertEqual(len(s["hooks"]["PreToolUse"]), 1)   # no duplicate
        self.assertIn("Stop", s["hooks"])
        r = self.install("--check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("in sync with home@", r.stdout)

    def test_check_reports_a_dependency_moving(self):
        self.edit("plugins/upstream/bin/up", "echo up", "echo up2", root=self.dep)
        r = self.install("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn(".claude/bin/up", r.stdout)

    def test_bytecode_is_skipped(self):
        cache = self.home / "plugins/tools/bin/__pycache__"
        cache.mkdir()
        (cache / "tool.cpython-0.pyc").write_bytes(b"")
        self.assertEqual(self.install().returncode, 0)
        self.assertFalse((self.dest / ".claude/bin/__pycache__").exists())

    def test_refuses_to_clobber_target_edits(self):
        (self.dest / ".claude/roles/dev.md").write_text("hand edit\n")
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn(".claude/roles/dev.md", r.stderr)
        self.assertIn("into home", r.stderr)

    def test_refuses_an_unmanaged_workflow_of_the_same_name(self):
        m = self.dest / ".claude/.automate.json"
        data = json.loads(m.read_text())
        del data["files"][".github/workflows/flow.yml"]
        m.write_text(json.dumps(data))
        (self.dest / ".github/workflows/flow.yml").write_text("name: theirs\n")
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("aren't managed", r.stderr)

    def test_files_dropped_at_source_are_removed(self):
        shutil.rmtree(self.dep / "plugins/upstream/skills/up-skill")
        self.assertEqual(self.install().returncode, 0)
        self.assertFalse((self.dest / ".claude/skills/up-skill").exists())
        self.assertTrue((self.dest / ".claude/skills/do-thing/SKILL.md").is_file())

    def test_permissions_merge_and_belong_to_install_only_when_it_added_them(self):
        sp = self.dest / ".claude/settings.json"
        self.assertEqual(self.settings()["permissions"], {"allow": ["Bash(make check)"], "ask": ["Bash(make deploy)"]})
        s = self.settings()
        s["permissions"] = {"allow": ["Bash(mine:*)", "Bash(make test)"], "defaultMode": "plan"}
        sp.write_text(json.dumps(s))
        frag = self.home / "targets/demo/settings.json"
        frag.write_text(json.dumps({"permissions": {"allow": ["Bash(make test)", "Bash(make lint)"]}}))
        self.assertEqual(self.install().returncode, 0)
        perms = self.settings()["permissions"]
        self.assertEqual(perms["allow"], ["Bash(mine:*)", "Bash(make test)", "Bash(make lint)"])
        self.assertNotIn("ask", perms)
        self.assertEqual(perms["defaultMode"], "plan")
        m = json.loads((self.dest / ".claude/.automate.json").read_text())
        self.assertEqual(m["settings"], {"permissions.allow": ["Bash(make lint)"]})   # make test was the repo's

        s = self.settings()
        s["permissions"]["allow"].remove("Bash(make lint)")
        sp.write_text(json.dumps(s))
        r = self.install("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("permissions.allow: Bash(make lint)", r.stdout)

        frag.unlink()
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.settings()["permissions"],
                         {"allow": ["Bash(mine:*)", "Bash(make test)"], "defaultMode": "plan"})

    def test_settings_fragment_is_validated(self):
        frag = self.home / "targets/demo/settings.json"
        for bad in ({"env": {"X": "1"}}, {"permissions": {"defaultMode": "auto"}},
                    {"permissions": {"allow": "Bash(make:*)"}}, {"permissions": {"allow": [1]}}):
            frag.write_text(json.dumps(bad))
            r = self.install()
            self.assertEqual(r.returncode, 1, bad)
            self.assertIn("settings.json", r.stderr, bad)


class InstallErrorsTest(ToolkitCase):
    def test_a_dependency_that_isnt_checked_out(self):
        shutil.rmtree(self.dep)
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("run `make deps`", r.stderr)

    def test_a_plugin_no_source_has(self):
        self.edit("targets/demo/target.json", '"upstream"]', '"upstream", "nowhere"]')
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("plugin 'nowhere'", r.stderr)

    def test_an_unknown_target(self):
        r = self.tool("install.py", "nope", self.dest)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no target 'nope'", r.stderr)

    def test_root_defaults_to_the_working_directory_repo(self):
        r = run(sys.executable, TOOLKIT / "install.py", "demo", self.dest, cwd=self.home / "targets")
        self.assertEqual(r.returncode, 0, r.stderr)
