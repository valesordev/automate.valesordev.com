"""solo7-media target: the sites monorepo's roles, ownership, and story config."""
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests/fixtures/consumer"   # game-server, game-content, and solo7-media
TOOLKIT = ROOT / "toolkit"
ROLES = ("product", "pm", "architecture", "visual-designer", "implementation", "sre")


def run(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


class TargetTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "solo7.media"
        self.repo.mkdir()
        run("git", "init", "-q", cwd=self.repo)
        r = run(sys.executable, str(TOOLKIT / "install.py"), "--root", str(FIXTURE), "solo7-media", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_roles_compose(self):
        for role in ROLES:
            text = (self.repo / f".claude/roles/{role}.md").read_text()
            self.assertRegex(text, r"(?m)^Assist level: L[0-3]", role)
        self.assertIn("# Role: PRODUCT", (self.repo / ".claude/roles/product.md").read_text())
        self.assertIn("## solo7.media sites", (self.repo / ".claude/roles/sre.md").read_text())
        vd = (self.repo / ".claude/roles/visual-designer.md").read_text()
        self.assertIn("## solo7.media brand kits", vd)
        self.assertIn("start from scratch", vd)

    def test_the_coordinator_runs_the_visual_designer_as_a_story_lane_with_an_inbox(self):
        sys.dont_write_bytecode = True
        loader = importlib.machinery.SourceFileLoader(
            "coordinator_t", str(self.repo / ".claude/bin/coordinator"))
        coord = importlib.util.module_from_spec(importlib.util.spec_from_loader("coordinator_t", loader))
        sys.modules["coordinator_t"] = coord
        loader.exec_module(coord)
        ctx = coord.Ctx(self.repo, coord.Shell())
        cfg = ctx.settings("visual-designer")
        self.assertEqual((cfg["sprint_run"], cfg["start_skill"], cfg["inbox"]),
                         ("story", "design-start-sprint", True))
        self.assertIn("selects every candidate", cfg["approval"])
        self.assertTrue((self.repo / ".claude/skills/design-start-sprint/SKILL.md").is_file())
        self.assertEqual(ctx.lane_roles(), ["implementation", "visual-designer", "architecture", "product", "sre", "pm"])
        prod = ctx.settings("product")
        self.assertEqual((prod["sprint_run"], prod["start_skill"], prod["watch_label"]),
                         ("session", "product-triage", "product"))
        self.assertTrue((self.repo / ".claude/skills/product-triage/SKILL.md").is_file())
        for r in ctx.lane_roles():
            ctx.check_lane_role(r)

    def test_story_config(self):
        repo = (self.repo / ".claude/roles/_repo.md").read_text()
        for line in ("story_prefix: S7M", "stories: github", "sprints: project",
                     "github_repo: valesordev/solo7.media"):
            self.assertIn(line, repo)
        self.assertTrue((self.repo / ".github/workflows/story-merge.yml").is_file())

    def test_ownership(self):
        paths = {
            "docs/product/releases.md": "product",
            "docs/roadmap.md": "pm",
            "docs/adr/0001-workspaces.md": "architecture",
            "docs/specs/slo/valesordev.md": "sre",
            "package.json": "architecture",
            "brands/valesordev/brand/foundation.md": "visual-designer",
            "brands/valesordev/tokens/tokens.json": "visual-designer",
            "brands/valesordev/references/visual-audit.md": "visual-designer",
            "brands/valesordev/qa.md": "visual-designer",
            "brands/valesordev/components/ProjectRow.astro": "implementation",
            "brands/valesordev/components/hero/Hero.astro": "implementation",
            "sites/valesordev.com/src/pages/index.astro": "implementation",
            "sites/valesordev.com/package.json": "implementation",
            "packages/site-core/src/privacy.astro": "implementation",
            "packages/site-core/instrumentation/faro.ts": "sre",
            "sites/valesordev.com/wrangler.jsonc": "sre",
            ".github/workflows/deploy.yml": "sre",
            "Makefile": "sre",
            "brands/valesordev/dist/tokens.css": "generated",
            "sites/valesordev.com/dist/index.html": "generated",
            "package-lock.json": "generated",
            "CLAUDE.md": "unowned",
        }
        r = run(str(self.repo / ".claude/bin/role"), "owners", *paths, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        for path, owner in paths.items():
            self.assertRegex(r.stdout, rf"(?m)^{path}\s+{owner}$", path)

    def test_lanes_start_own_stories_and_only_architecture_readies(self):
        def fm(role):
            text = (ROOT / f"targets/solo7-media/roles/{role}.md").read_text()
            return text[4:text.index("\n---\n", 4)]
        for role in ("visual-designer", "implementation", "sre", "architecture"):
            self.assertIn("ready>in-progress@lane", fm(role), role)
            self.assertIn("on_merge: [in-progress>review@lane]", fm(role), role)
        def transitions(role):
            return next((l for l in fm(role).splitlines() if l.startswith("transitions:")), "")
        self.assertIn("draft>ready", transitions("architecture"))
        for role in ("pm", "visual-designer", "implementation", "sre", "product"):
            self.assertNotIn("draft>ready", transitions(role), role)
        self.assertIn("records: [draft>ready: Observability review, review>done: §8 instrumentation]", fm("sre"))
        self.assertIn("records: [review>done: §8]", fm("architecture"))
        self.assertIn("ready>blocked", transitions("pm"))   # the readiness scan's marks
        self.assertIn("blocked>ready", transitions("architecture"))
        self.assertIn("readiness-scan", fm("pm"))

    def test_brand_citation_gate(self):
        repo = (ROOT / "targets/solo7-media/roles/_repo.md").read_text()
        self.assertIn("Branding is decided in brand docs, never in code", repo)
        arch = (ROOT / "targets/solo7-media/roles/architecture.md").read_text()
        self.assertIn("**Brand citation.**", arch)

    def test_permissions_merged(self):
        s = json.loads((self.repo / ".claude/settings.json").read_text())
        self.assertIn("Bash(make check)", s["permissions"]["allow"])
        self.assertIn("Bash(npx wrangler deploy:*)", s["permissions"]["ask"])


if __name__ == "__main__":
    unittest.main()
