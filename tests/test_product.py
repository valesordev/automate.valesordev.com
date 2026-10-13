"""valesor-dev product role: the base charter, its skills, and the game-server fixture target."""
import importlib.machinery
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests/fixtures/consumer"   # game-server, game-content, and solo7-media
TOOLKIT = ROOT / "toolkit"
PLUGIN = ROOT / "plugins/valesor-dev"
SKILLS = ("feature-brief", "release-plan", "release-review", "product-triage")


def run(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def fm(text):
    end = text.index("\n---\n", 4)
    return dict(l.split(":", 1) for l in text[4:end].splitlines() if ":" in l)


class StructureTest(unittest.TestCase):
    def test_base_role_is_delegate_and_owns_product_docs(self):
        t = (PLUGIN / "roles/product.md").read_text()
        self.assertRegex(t, r"(?m)^Assist level: L3 ")
        self.assertIn("`docs/product/`", t)
        self.assertIn("release-change", t)

    def test_skills_require_the_role_and_name_non_triggers(self):
        for s in SKILLS:
            text = (PLUGIN / f"skills/{s}/SKILL.md").read_text()
            meta = fm(text)
            self.assertEqual(meta["name"].strip(), s)
            self.assertIn("Not for", meta["description"])
            self.assertIn("role require product", text)
            self.assertTrue((PLUGIN / f"skills/{s}/references/examples.md").is_file(), s)

    def test_handoffs_wait_for_merge(self):
        base = (PLUGIN / "roles/product.md").read_text()
        self.assertIn("Release changes (after merge)", base)
        self.assertIn("Open nothing while the PR is unmerged", base)
        plan = (PLUGIN / "skills/release-plan/SKILL.md").read_text()
        self.assertIn("Open\n   no issues now", plan)

    def test_review_finds_committed_release_and_never_overwrites(self):
        t = (PLUGIN / "skills/release-review/SKILL.md").read_text()
        self.assertIn("still `committed`", t)
        self.assertIn("R<n>-<YYYY-MM-DD>-<NN>.md", t)
        self.assertIn("product/review-r<n>-<date>-<NN>", t)


class TargetTest(unittest.TestCase):
    def install(self, target):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name) / target
        repo.mkdir()
        run("git", "init", "-q", cwd=repo)
        r = run(sys.executable, str(TOOLKIT / "install.py"), "--root", str(FIXTURE), target, str(repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return repo

    def test_server_role_composes_base_and_owns_only_product_docs(self):
        repo = self.install("game-server")
        role = (repo / ".claude/roles/product.md").read_text()
        meta = fm(role)
        self.assertEqual(meta["writes"].strip(), "[docs/product/]")
        self.assertNotIn("transitions", meta)
        self.assertIn("Assist level: L3", role)
        self.assertIn("## Demo Game server", role)
        r = run(str(repo / ".claude/bin/role"), "owners", "docs/product/releases.md", "docs/roadmap.md",
                cwd=repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"docs/product/releases.md\s+product")
        self.assertRegex(r.stdout, r"docs/roadmap.md\s+pm")
        for s in SKILLS:
            self.assertTrue((repo / f".claude/skills/{s}/SKILL.md").is_file(), s)

    def test_the_coordinator_runs_the_server_product_role_on_its_queue(self):
        repo = self.install("game-server")
        sys.dont_write_bytecode = True
        loader = importlib.machinery.SourceFileLoader("coordinator_p", str(repo / ".claude/bin/coordinator"))
        coord = importlib.util.module_from_spec(importlib.util.spec_from_loader("coordinator_p", loader))
        sys.modules["coordinator_p"] = coord
        loader.exec_module(coord)
        ctx = coord.Ctx(repo, coord.Shell())
        cfg = ctx.settings("product")
        self.assertEqual((cfg["sprint_run"], cfg["start_skill"], cfg["watch_label"], cfg["watch_pr_section"]),
                         ("session", "product-triage", "product", "Release changes (after merge)"))
        self.assertIn("vetoes or approves", cfg["approval"])
        ctx.check_lane_role("product")
        self.assertIn("product", ctx.lane_roles())


if __name__ == "__main__":
    unittest.main()
