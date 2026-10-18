"""tool_perms.py: tool definitions, their snapshots and generated permissions, and installing them."""
import json
import os
import sys
import unittest

from helpers import ToolkitCase, TOOLKIT

sys.path.insert(0, str(TOOLKIT))
import tool_perms  # noqa: E402

FAKE = """#!/bin/sh
case "$1" in
  --version) echo "fakecli 2.0 (build abc)";;
  commands) echo '{"commands": [{"full_path": "fakecli"}, {"full_path": "fakecli things"},
            {"full_path": "fakecli things list"}, {"full_path": "fakecli things purge"},
            {"full_path": "fakecli raw"}]}';;
  *) exit 2;;
esac
"""


class ParseTest(unittest.TestCase):
    def test_the_subset(self):
        meta, body = tool_perms.parse(
            "---\n"
            "tool: x   # the name\n"
            "verbs: [list, 'get, all', \"q\"]\n"
            "check: x --version\n"
            "inst:\n"
            "  commands: [a, b]\n"
            "  bash:\n"
            "    - '\\bcurl\\b.*it''s # not a comment'\n"
            "  url: []\n"
            "items:\n"
            "  - one\n"
            "  - two\n"
            "---\nBody.\n")
        self.assertEqual(meta["tool"], "x")
        self.assertEqual(meta["verbs"], ["list", "get, all", "q"])
        self.assertEqual(meta["check"], "x --version")
        self.assertEqual(meta["inst"], {"commands": ["a", "b"], "bash": ["\\bcurl\\b.*it's # not a comment"], "url": []})
        self.assertEqual(meta["items"], ["one", "two"])
        self.assertEqual(body, "Body.\n")

    def test_no_frontmatter_and_bad_lines(self):
        self.assertEqual(tool_perms.parse("just text"), ({}, "just text"))
        with self.assertRaises(ValueError):
            tool_perms.parse("---\n  indented: x\n---\n")
        with self.assertRaises(ValueError):
            tool_perms.parse("---\nno colon here\n---\n")


class CatalogTest(unittest.TestCase):
    def test_json_at_any_depth_keeps_leaves(self):
        doc = {"commands": [{"full_path": "c", "subcommands": [{"full_path": "c a"}, {"full_path": "c b",
               "subcommands": [{"full_path": "c b  list"}]}]}], "other": {"full_path": "c z"}}
        self.assertEqual(tool_perms.catalog_paths(json.dumps(doc)), ["c a", "c b list", "c z"])

    def test_text_lines(self):
        self.assertEqual(tool_perms.catalog_paths("# v1\nc x\nc\n\nc y get\n"), ["c x", "c y get"])

    def test_classify(self):
        meta = {"read_verbs": ["list", "get-*"], "always_ask": ["c raw"]}
        allow, ask = tool_perms.classify(meta, ["c a list", "c a get-one", "c a get", "c a delete", "c raw list"])
        self.assertEqual(allow, ["c a list", "c a get-one"])
        self.assertEqual(ask, ["c a get", "c a delete", "c raw list"])


class ToolPermsTest(ToolkitCase):
    def setUp(self):
        super().setUp()
        self.defn = self.home / "plugins/tools/tools/fakecli.md"
        self.defn.parent.mkdir()
        self.defn.write_text((self.dep / "plugins/upstream/tools/fakecli.md").read_text())

    def test_snapshot_runs_the_catalog_and_builds(self):
        bin_ = self.home.parent / "bin"
        bin_.mkdir()
        (bin_ / "fakecli").write_text(FAKE)
        (bin_ / "fakecli").chmod(0o755)
        env = {**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"}
        r = self.tool("tool_perms.py", "snapshot", "fakecli", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        snap = self.defn.with_name("fakecli.commands.txt").read_text()
        self.assertEqual(snap, "# fakecli 2.0 (build abc)\nfakecli raw\nfakecli things list\nfakecli things purge\n")
        perms = json.loads(self.defn.with_name("fakecli.permissions.json").read_text())
        self.assertEqual(perms["allow"], ["Bash(fakecli things list:*)"])
        self.assertEqual(perms["ask"], ["Bash(fakecli raw:*)", "Bash(fakecli things purge:*)"])
        self.assertEqual(perms["version"], "fakecli 2.0 (build abc)")

    def test_snapshot_without_a_catalog_or_a_cli(self):
        r = self.tool("tool_perms.py", "snapshot", str(self.defn), env={**os.environ, "PATH": "/nonexistent"})
        self.assertNotEqual(r.returncode, 0)
        self.edit("plugins/tools/tools/fakecli.md", "catalog: fakecli commands\n", "")
        r = self.tool("tool_perms.py", "snapshot", str(self.defn))
        self.assertIn("write fakecli.commands.txt by hand", r.stderr)

    def test_check_catches_a_stale_or_orphaned_permissions_file(self):
        snap = self.defn.with_name("fakecli.commands.txt")
        snap.write_text("# v1\nfakecli things list\n")
        self.assertEqual(self.tool("tool_perms.py", "--check").returncode, 1)
        self.assertEqual(self.tool("tool_perms.py", "--build").returncode, 0)
        self.assertEqual(self.tool("tool_perms.py", "--check").returncode, 0)
        self.edit("plugins/tools/tools/fakecli.md", "read_verbs: [list, get-*]", "read_verbs: [get-*]")
        r = self.tool("tool_perms.py", "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("plugins/tools/tools/fakecli.permissions.json", r.stderr)
        self.tool("tool_perms.py", "--build")
        snap.unlink()
        self.assertEqual(self.tool("tool_perms.py", "--check").returncode, 1)
        self.tool("tool_perms.py", "--build")
        self.assertFalse(self.defn.with_name("fakecli.permissions.json").exists())


class InstallToolsTest(ToolkitCase):
    def test_a_target_definition_extends_a_plugins(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        t = json.loads((self.dest / ".claude/tools/fakecli.json").read_text())
        self.assertEqual((t["tool"], t["domain"], t["check"], t["version"]),
                         ("fakecli", "Fake Cloud", "fakecli --version", "fakecli 1.0"))
        self.assertEqual(t["instead_of"], {"commands": ["rawcli"], "bash": ["\\bcurl\\b.*\\.fake\\.test"],
                                           "url": ["^https://[^/]+\\.fake\\.test"]})
        self.assertEqual(t["allow"], ["Bash(fakecli things get-one:*)", "Bash(fakecli things list:*)"])
        self.assertEqual(t["ask"], ["Bash(fakecli raw list:*)", "Bash(fakecli things delete:*)"])
        notes = (self.dest / ".claude/tools/fakecli.md").read_text()
        self.assertLess(notes.index("Use fakecli for Fake Cloud."), notes.index("Use the `demo` context."))
        perms = json.loads((self.dest / ".claude/settings.json").read_text())["permissions"]
        self.assertEqual(perms["allow"], ["Bash(make check)", *t["allow"]])
        self.assertEqual(perms["ask"], ["Bash(make deploy)", *t["ask"]])
        self.assertEqual(self.install("--check").returncode, 0)

    def test_dropping_a_tool_removes_its_files_and_rules(self):
        self.assertEqual(self.install().returncode, 0)
        self.edit("targets/demo/roles/_repo.md", "tools: [fakecli]\n", "")
        (self.home / "targets/demo/tools/fakecli.md").unlink()
        self.assertEqual(self.install().returncode, 0)
        self.assertFalse((self.dest / ".claude/tools").exists())
        perms = json.loads((self.dest / ".claude/settings.json").read_text())["permissions"]
        self.assertEqual(perms, {"allow": ["Bash(make check)"], "ask": ["Bash(make deploy)"]})

    def test_errors(self):
        self.edit("targets/demo/roles/dev.md", "writes: [src/]", "writes: [src/]\ntools: [nope]")
        r = self.install()
        self.assertIn("tool 'nope' is in neither", r.stderr)
        self.edit("targets/demo/roles/dev.md", "tools: [nope]", "tools: []")
        self.edit("targets/demo/tools/fakecli.md", "extends: fakecli", "extends: fakecli\nread_verbs: [delete]")
        r = self.install()
        self.assertIn("which only the definition with the snapshot may", r.stderr)


class ToolConventionsTest(ToolkitCase):
    def test_fixture_passes(self):
        r = self.tool("check_conventions.py")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_each_problem(self):
        self.edit("targets/demo/tools/fakecli.md", "url: ['^https://[^/]+\\.fake\\.test']", "url: ['(unclosed']")
        (self.home / "targets/demo/tools/spare.md").write_text("---\ntool: other\nkind: gui\n---\n")
        defn = self.home / "plugins/tools/tools/withcat.md"
        defn.parent.mkdir()
        defn.write_text("---\ntool: withcat\nkind: cli\ndomain: X\ncatalog: withcat list\n---\n")
        r = self.tool("check_conventions.py")
        self.assertEqual(r.returncode, 1)
        for want in ("targets/demo/tools/fakecli.md: instead_of.url '(unclosed' doesn't compile",
                     "targets/demo/tools/spare.md: no role or _repo.md in demo names it",
                     "targets/demo/tools/spare.md: `tool: other` isn't the file name",
                     "targets/demo/tools/spare.md: `kind` must be one of cli",
                     "targets/demo/tools/spare.md: no `domain`",
                     "plugins/tools/tools/withcat.md: has a `catalog` but no withcat.commands.txt"):
            self.assertIn(want, r.stdout)


if __name__ == "__main__":
    unittest.main()
