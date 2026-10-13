"""valesor-dev visual-designer: the base charter and its skills (art, audit, QA, sprint lane)."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins/valesor-dev"
SKILLS = ("art-direction", "art-slot", "brand-audit", "design-start-sprint")


def run(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def fm(text):
    end = text.index("\n---\n", 4)
    return dict(l.split(":", 1) for l in text[4:end].splitlines() if ":" in l)


class StructureTest(unittest.TestCase):
    def test_base_role_is_pair_and_allows_generated_art_with_provenance(self):
        t = (PLUGIN / "roles/visual-designer.md").read_text()
        self.assertRegex(t, r"(?m)^Assist level: L2 ")
        self.assertIn("Everything may be generated, marks included", t)
        self.assertIn("**Provenance on every asset.**", t)
        self.assertIn("**Judge it treated.**", t)
        self.assertNotIn("replace the owner's artwork with generated imagery", t)

    def test_base_role_is_in_repo_with_the_sites(self):
        t = (PLUGIN / "roles/visual-designer.md").read_text()
        self.assertIn("brands/<slug>/", t)
        self.assertIn("references/visual-audit.md", t)
        self.assertIn("story comment <ID> --to visual-designer", t)
        self.assertIn("## Code never decides branding", t)
        for gone in ("handoff brief", "REV line", "positioning.md"):
            self.assertNotIn(gone, t)

    def test_skills_require_the_role_and_name_non_triggers(self):
        for s in SKILLS:
            text = (PLUGIN / f"skills/{s}/SKILL.md").read_text()
            meta = fm(text)
            self.assertEqual(meta["name"].strip(), s)
            if s != "design-start-sprint":  # user-invoked only; routing doesn't apply
                self.assertIn("Not for", meta["description"])
            self.assertIn("role require visual-designer", text)
            self.assertTrue((PLUGIN / f"skills/{s}/references/examples.md").is_file(), s)

    def test_brand_qa_accepts_designer_or_implementation(self):
        text = (PLUGIN / "skills/brand-qa/SKILL.md").read_text()
        self.assertEqual(fm(text)["name"].strip(), "brand-qa")
        self.assertIn("Not for", fm(text)["description"])
        self.assertIn("role show ${CLAUDE_SESSION_ID}", text)
        self.assertIn("`visual-designer` or `implementation`", text)
        self.assertIn("Never QA against rules nobody approved", text)
        self.assertTrue((PLUGIN / "skills/brand-qa/references/qa-template.md").is_file())

    def test_skills_use_the_monorepo_layout(self):
        for s in ("art-direction", "art-slot"):
            d = PLUGIN / f"skills/{s}"
            for f in [d / "SKILL.md", *sorted((d / "references").glob("*.md"))]:
                t = f.read_text()
                for gone in ("art/<imprint>/", "art/valesordev/", "art/treat/", "identity/<imprint>/",
                             "positioning.md", "handoff/"):
                    self.assertNotIn(gone, t, (f.name, gone))
        self.assertIn("illustration-system.md", (PLUGIN / "skills/art-direction/SKILL.md").read_text())

    def test_audit_is_proposed_until_owner_decides(self):
        t = (PLUGIN / "skills/brand-audit/SKILL.md").read_text()
        for c in ("core identity", "candidate motif", "hero-specific", "[PROPOSED]"):
            self.assertIn(c, t)
        self.assertIn("The owner\ndecides at PR review", t)
        self.assertIn("Never write `tokens/`", t)
        self.assertIn("references/target-homepage.png", t)

    def test_illustration_rules_are_separate_from_prompts(self):
        t = (PLUGIN / "skills/art-direction/SKILL.md").read_text()
        self.assertIn("**Rules, not prompts.**", t)
        tpl = (PLUGIN / "skills/art-direction/references/direction-template.md").read_text()
        self.assertIn("## Composition types", tpl)
        self.assertIn("hero-wide | 2.4:1", tpl)

    def test_design_lane_answers_brand_questions_first(self):
        t = (PLUGIN / "skills/design-start-sprint/SKILL.md").read_text()
        self.assertIn("disable-model-invocation: true", t)
        self.assertIn("`stories: github` and `sprints: project`", t)
        self.assertIn("stories-on-github.md", t)
        self.assertLess(t.index("## Brand questions first"), t.index("## Then the backlog"))
        self.assertIn("`role:visual-designer`", t)

    def test_slot_reviews_treated_candidates_and_never_commits_them(self):
        t = (PLUGIN / "skills/art-slot/SKILL.md").read_text()
        self.assertIn("**Treat before judging.**", t)
        self.assertIn("Candidates are never committed", t)
        self.assertIn("references/marks.md", t)
        self.assertIn("Never write `dist/` by hand", t)

    def test_hand_swap_rebuilds_from_the_editable_source(self):
        # PR #42 Codex review: a .kra/.svg swap had no export step or rule change.
        t = (PLUGIN / "skills/art-slot/SKILL.md").read_text()
        self.assertIn("point the slot's Makefile rule at\nthe new source", t)
        b = (PLUGIN / "skills/art-slot/references/build.md").read_text()
        for src in ("hero.kra", "hero.png"):
            self.assertIn(src, b)
        self.assertIn("krita --export --export-filename $@ $<", b)

    def test_mark_provenance_has_a_basis_without_art_direction(self):
        # PR #42 Codex review: marks may precede an AD version, yet provenance required one.
        for f in ("roles/visual-designer.md", "skills/art-slot/references/slot-template.md",
                  "skills/art-slot/references/marks.md"):
            self.assertIn("brand/foundation.md", (PLUGIN / f).read_text(), f)
        self.assertNotIn("Date: <YYYY-MM-DD> · AD v<n>",
                         (PLUGIN / "skills/art-slot/references/slot-template.md").read_text())

    def test_export_only_from_an_approved_direction(self):
        t = (PLUGIN / "skills/art-direction/SKILL.md").read_text()
        self.assertIn("Never render from a draft", t)
        self.assertLessEqual(len((PLUGIN / "skills/art-direction/references/chatgpt-project.md")
                                 .read_text().split("```text\n")[1].split("```")[0]), 1500)


class StoryMergeWorkflowTest(unittest.TestCase):
    """brand-kit#2 Codex review: the vendored workflow failed in repos with no Project."""

    def test_token_and_move_are_gated_on_stories_github(self):
        wf = (PLUGIN / "workflows/story-merge.yml").read_text()
        steps = wf.split("      - ")
        for marker in ("uses: actions/create-github-app-token", "name: Move stories"):
            step = next(s for s in steps if marker in s)
            self.assertIn("if: steps.cfg.outputs.stories == 'github'", step, marker)
        gate = next(s for s in steps if s.startswith("id: cfg"))
        pattern = gate.split("grep -qE '")[1].split("'")[0]
        with tempfile.TemporaryDirectory() as tmp:
            for body, expect in (("---\nproject: Demo\nstories: github\n---\n", 0), ("---\nproject: Demo\n---\n", 1)):
                f = Path(tmp) / "_repo.md"
                f.write_text(body)
                r = run("grep", "-qE", pattern, str(f))
                self.assertEqual(r.returncode, expect, body)


if __name__ == "__main__":
    unittest.main()
