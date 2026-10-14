"""bin/coordinator: lane selection, and a full run against a scratch repo with fake claude/gh."""
import base64
import contextlib
import fcntl
import importlib.machinery
import importlib.util
import io
import json
import re
import os
import signal
import subprocess
import sys
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests/fixtures/consumer"   # game-server, game-content, and solo7-media
TOOLKIT = ROOT / "toolkit"
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("STORY_FIND_DELAY", "0")
import test_story  # noqa: E402  (FakeGH: an in-memory GitHub Project)

BIN = ROOT / "plugins/valesor-dev/bin"
loader = importlib.machinery.SourceFileLoader("coordinator_bin", str(BIN / "coordinator"))
coord = importlib.util.module_from_spec(importlib.util.spec_from_loader("coordinator_bin", loader))
sys.modules["coordinator_bin"] = coord
sys.dont_write_bytecode = True
loader.exec_module(coord)
coord.HANDOFF_RETRY_DELAY = 0


def st(id, status="ready", lane="implementation", sprint="SPRINT-05", rank=1, comp="server", blockers=(),
       comments=(), labels=()):
    s = coord.Story({"id": id, "number": 1, "title": id,
                     "fields": {"Status": status, "Lane": lane, "Sprint": sprint, "Rank": rank,
                                "Component": comp}}, list(blockers))
    s.comments = list(comments)
    s.labels = list(labels)
    return s


ACTIVE = {"state": "active", "current": "SPRINT-05"}


def note(to, frm=None, text="x"):
    """A handoff comment as `story comment --to <to>` writes it, from `frm` (None: an old comment)."""
    return f"**For {to}:** {text}" + (f"\n\n<!-- from: {frm} -->" if frm else "")


def pick(stories, state=ACTIVE, roles=("implementation", "sre", "pm"), running=(), max_lanes=3, **kw):
    lanes, waiting = coord.select(stories, state, list(roles), set(running), max_lanes, **kw)
    return [(l["role"], l["story"]) for l in lanes], {w["story"]: w for w in waiting}


VD = {"implementation": "story", "visual-designer": "story", "sre": "session", "pm": "session"}


class LaneConfigTest(unittest.TestCase):
    def test_starting_lanes_is_always_put_to_the_person(self):
        for t in ("game-server", "game-content", "solo7-media"):   # every target that vendors the coordinator
            perms = json.loads((FIXTURE / "targets" / t / "settings.json").read_text())["permissions"]
            self.assertIn("Bash(.claude/bin/coordinator run:*)", perms["ask"], t)
            self.assertIn("Bash(.claude/bin/coordinator clean:*)", perms["ask"], t)
            self.assertFalse([r for r in perms.get("allow", []) if r.startswith("Bash(.claude/bin/coordinator run")], t)
        self.assertIn("Bash(.claude/bin/coordinator:*)", coord.DENY)

    def test_a_role_without_settings_uses_the_defaults_for_its_name(self):
        cfg = coord.role_settings({}, "implementation")
        self.assertEqual((cfg["sprint_run"], cfg["start_skill"]), ("story", "impl-start-sprint"))
        self.assertEqual(coord.role_settings({}, "sre")["sprint_run"], "session")
        self.assertEqual(coord.role_settings({}, "architecture")["sprint_run"], "session")
        self.assertIsNone(coord.role_settings({}, "game-designer")["sprint_run"])

    def test_a_charter_can_make_any_role_a_lane(self):
        cfg = coord.role_settings({"sprint_run": "story", "start_skill": "design-start-sprint",
                                   "inbox": "true", "approval": "the owner picks every candidate"},
                                  "visual-designer")
        self.assertEqual((cfg["sprint_run"], cfg["start_skill"], cfg["inbox"], cfg["approval"]),
                         ("story", "design-start-sprint", True, "the owner picks every candidate"))

    def test_a_charter_overrides_a_default(self):
        self.assertEqual(coord.role_settings({"start_skill": "my-skill"}, "sre")["start_skill"], "my-skill")

    def test_a_question_is_pending_until_the_role_replies_to_the_asker(self):
        q = "visual-designer"
        asked = st("A-1", comments=[note(q, "implementation")])
        answered = st("A-2", comments=[note(q, "implementation"), note("implementation", q)])
        masked = st("A-3", comments=[note(q, "implementation"), note("architecture", "implementation")])
        again = st("A-5", comments=[note(q, "implementation"), note("implementation", q),
                                    note(q, "implementation")])
        other = st("A-6", comments=[note("architecture", "implementation")])
        done = st("A-7", status="done", comments=[note(q, "implementation")])
        punted = st("A-8", comments=[note(q, "implementation"), note("owner", q, "your call")])
        escalated = st("A-9", comments=[note(q, "implementation"), note("architecture", q, "not mine")])
        someone_else = st("A-11", comments=[note(q, "implementation"), note("implementation", "architecture")])
        verified = st("A-10", comments=[note(q, "implementation"),
                                        f"**Verification ({q}):** done\n\n<!-- from: {q} -->"])
        self.assertEqual(coord.questions_for(q, [asked, answered, masked, again, other, done, punted,
                                                 escalated, someone_else, verified]),
                         ["A-1", "A-3", "A-5", "A-8", "A-9", "A-11", "A-10"])

    def test_a_question_from_the_coordinator_is_answered_to_the_owner(self):
        q = "sre"
        asked = st("A-1", comments=[note(q, "coordinator")])
        answered = st("A-2", comments=[note(q, "coordinator"), note("owner", q, "keys added in #12")])
        to_coordinator = st("A-3", comments=[note(q, "coordinator"), note("coordinator", q)])
        elsewhere = st("A-4", comments=[note(q, "coordinator"), note("architecture", q)])
        self.assertEqual(coord.questions_for(q, [asked, answered, to_coordinator, elsewhere]), ["A-1", "A-3", "A-4"])

    def test_comments_without_an_author_marker_fall_back_to_the_last_handoff(self):
        q = "visual-designer"
        old_open = st("A-1", comments=[note(q)])
        old_answered = st("A-2", comments=[note(q), note("implementation")])
        self.assertEqual(coord.questions_for(q, [old_open, old_answered]), ["A-1"])

    def test_a_watch_label_makes_a_session_role_runnable(self):
        cfg = coord.role_settings({"sprint_run": "session", "watch_label": "product",
                                   "watch_pr_section": "Release changes (after merge)"}, "product")
        self.assertEqual((cfg["watch_label"], cfg["watch_pr_section"]),
                         ("product", "Release changes (after merge)"))
        self.assertIsNone(coord.role_settings({}, "sre")["watch_label"])

    def test_allow_entries_cannot_smuggle_a_second_rule(self):
        for bad in ("Bash(a b),Bash(rm -rf /:*)", "Bash(make x),Bash(curl y:*)", "Bash(make x) Bash(y z:*)"):
            self.assertIsNotNone(coord.check_allow_entry(bad), bad)
        for ok in ("Bash(make build-brand-valesordev:*)", "Bash(npm run build:*)", "Bash(npm test:*)",
                   "Bash(go test:*)", "Bash(cargo test:*)", "Bash(uv sync:*)"):
            self.assertIsNone(coord.check_allow_entry(ok), ok)
        for bad in ("Bash(ksh build.sh:*)", "Bash(mksh x:*)", "Bash(ash x:*)", "Bash(csh x:*)", "Bash(tcsh x:*)",
                    "Bash(pwsh build.ps1:*)", "Bash(powershell x:*)", "Bash(nu x:*)", "Bash(go run:*)",
                    "Bash(cargo run:*)", "Bash(uv run:*)", "Bash(pipx run:*)", "Bash(pip install:*)",
                    "Bash(pip3 install:*)", "Bash(uv pip:*)", "Bash(cargo install:*)", "Bash(go generate:*)"):
            self.assertIsNotNone(coord.check_allow_entry(bad), bad)

    def test_a_role_can_opt_out_and_values_are_not_case_sensitive(self):
        for v in ("none", "false", "no", "off", "None"):
            self.assertIsNone(coord.role_settings({"sprint_run": v}, "x")["sprint_run"], v)
        self.assertEqual(coord.role_settings({"sprint_run": "Story"}, "x")["sprint_run"], "story")

    def test_settings_are_forgiving_about_how_they_are_written(self):
        cfg = coord.role_settings({"inbox": "yes", "approval": ["a", "b"]}, "visual-designer")
        self.assertTrue(cfg["inbox"])
        self.assertEqual(cfg["approval"], "a, b")
        self.assertFalse(coord.role_settings({"inbox": "no"}, "visual-designer")["inbox"])

    def test_handoffs_owed_counts_planner_bullets_and_none_as_zero(self):
        h = "## Release changes (after merge)"
        roles = {"product", "pm", "visual-designer"}
        for section, owed in (("None. Releases are planned in the next PR.", 0),
                              ("None new. The roadmap request to pm is open as #7.", 0),
                              ("**None.**", 0),
                              ("Nothing here, just prose.", 0),
                              ("- pm: add Release: R1 to M0\n- producer: key C1 to R1", 2),
                              ("- **visual-designer:** routed through FEAT-02 Dependencies\n- **pm:** cut S-12", 1),
                              ("- `pm` — cut S-12", 1),
                              ("- add Release: R1 to M0", 1),       # unaddressed: still owed
                              ("- pm: x\n\n## Notes\n- y", 1)):
            self.assertEqual(coord.handoffs_owed(f"x\n\n{h}\n{section}", h, roles), owed, section)
        self.assertEqual(coord.handoffs_owed("no section", h, roles), 0)

SESS = {"implementation": "story", "sre": "session", "pm": "session", "product": "session"}


class SelectTest(unittest.TestCase):
    def test_a_watching_role_starts_one_lane_when_its_queue_has_items(self):
        lanes, _ = pick([], state={"state": "closed"}, roles=["product"], kinds=SESS,
                        watch={"product": ["#12", "#13"]})
        self.assertEqual(lanes, [("product", None)])
        self.assertEqual(pick([], roles=["product"], kinds=SESS, watch={})[0], [])

    def test_a_watching_role_is_not_started_twice(self):
        w = {"product": ["#12"]}
        self.assertEqual(pick([], roles=["product"], kinds=SESS, watch=w, running=["product"])[0], [])

    def test_a_watching_role_respects_the_lane_cap(self):
        lanes, waiting = pick([], roles=["product"], kinds=SESS, watch={"product": ["#12"]},
                              running=["A-1"], max_lanes=1)
        self.assertEqual(lanes, [])
        self.assertEqual(waiting["-"]["why"], "no lane free")


    def test_visual_designer_stories_get_their_own_lanes_and_components(self):
        s = [st("A-1", comp="valesordev"), st("V-1", lane="visual-designer", comp="valesordev"),
             st("V-2", lane="visual-designer", comp="valesordev", rank=2)]
        lanes, waiting = pick(s, roles=["implementation", "visual-designer"], kinds=VD)
        self.assertEqual(lanes, [("implementation", "A-1"), ("visual-designer", "V-1")])
        self.assertIn("component valesordev", waiting["V-2"]["why"])

    def test_visual_designer_drafts_wait_on_architecture_when_that_lane_runs(self):
        lanes, waiting = pick([st("V-1", lane="visual-designer", status="draft")],
                              roles=["visual-designer"], kinds=VD)
        self.assertEqual((lanes, waiting["V-1"]["on"]), ([], "architecture"))

    def test_visual_designer_stories_are_only_listed_when_that_lane_is_not_run(self):
        lanes, waiting = pick([st("V-1", lane="visual-designer")], roles=["implementation"], kinds=VD)
        self.assertEqual(lanes, [])
        self.assertIn("lane visual-designer", waiting["V-1"]["why"])

    def test_an_implementation_story_waits_for_the_brand_story_that_blocks_it(self):
        s = [st("A-1", comp="valesordev", blockers=[("V-1", "in-progress")]),
             st("V-1", lane="visual-designer", status="in-progress", comp="valesordev")]
        lanes, waiting = pick(s, roles=["implementation", "visual-designer"], kinds=VD)
        self.assertEqual(lanes, [("visual-designer", "V-1")])
        self.assertEqual(waiting["A-1"]["on"], "blocker")

    def test_brand_questions_start_an_inbox_lane_ahead_of_the_backlog(self):
        s = [st("V-1", lane="visual-designer", comp="valesordev")]
        lanes, waiting = pick(s, roles=["visual-designer"], kinds=VD, inbox={"visual-designer": ["A-9"]})
        self.assertEqual(lanes, [("visual-designer", None)])

    def test_an_inbox_lane_that_is_running_is_not_started_again(self):
        lanes, _ = pick([], roles=["visual-designer"], kinds=VD, inbox={"visual-designer": ["A-9"]},
                        running=["visual-designer:inbox"])
        self.assertEqual(lanes, [])

    def test_an_inbox_lane_counts_against_the_lane_cap(self):
        s = [st("A-1", comp="cli")]
        lanes, waiting = pick(s, roles=["implementation", "visual-designer"], kinds=VD, max_lanes=1,
                              inbox={"visual-designer": ["A-9"]})
        self.assertEqual(lanes, [("visual-designer", None)])
        self.assertEqual(waiting["A-1"]["why"], "no lane free")

    def test_builders_with_an_open_question_wait_for_the_answer(self):
        s = [st("A-1", comp="valesordev"), st("A-2", comp="cli")]
        lanes, waiting = pick(s, roles=["implementation", "visual-designer"], kinds=VD,
                              inbox={"visual-designer": ["A-1"]})
        self.assertEqual(lanes, [("visual-designer", None), ("implementation", "A-2")])
        self.assertEqual(waiting["A-1"]["on"], "visual-designer")
        self.assertIn("question", waiting["A-1"]["why"])

    def test_the_role_waits_on_its_own_inbox_lane_before_its_backlog(self):
        s = [st("V-1", lane="visual-designer", comp="valesordev"), st("A-9", comp="server")]
        for running in ((), ("visual-designer:inbox",)):
            lanes, waiting = pick(s, roles=["implementation", "visual-designer"], kinds=VD,
                                  inbox={"visual-designer": ["A-9"]}, running=running)
            self.assertNotIn(("visual-designer", "V-1"), lanes)
            self.assertIn("answering questions first", waiting["V-1"]["why"])

    def test_no_inbox_lane_when_the_cap_is_already_used_by_running_lanes(self):
        lanes, _ = pick([], roles=["visual-designer"], kinds=VD, max_lanes=1, running=["A-1"],
                        inbox={"visual-designer": ["A-9"]})
        self.assertEqual(lanes, [])

    def test_a_live_designer_lane_does_not_hold_the_implementation_components(self):
        s = [st("V-1", lane="visual-designer", status="in-progress", comp="valesordev"),
             st("A-1", comp="valesordev")]
        lanes, _ = pick(s, roles=["implementation", "visual-designer"], kinds=VD, running=["V-1"])
        self.assertEqual(lanes, [("implementation", "A-1")])

    def test_no_inbox_lane_for_a_role_the_run_does_not_include(self):
        lanes, _ = pick([], roles=["implementation"], kinds=VD, inbox={"visual-designer": ["A-9"]})
        self.assertEqual(lanes, [])


    def test_ready_stories_in_rank_order_one_lane_per_component(self):
        lanes, waiting = pick([st("A-1", rank=2, comp="cli"), st("A-2", rank=1, comp="server"),
                               st("A-3", rank=3, comp="server")], roles=["implementation"])
        self.assertEqual(lanes, [("implementation", "A-2"), ("implementation", "A-1")])
        self.assertIn("component server", waiting["A-3"]["why"])

    def test_blockers_must_be_at_review_or_later(self):
        lanes, waiting = pick([st("A-1", blockers=[("A-9", "in-progress")]),
                               st("A-2", comp="cli", blockers=[("A-8", "review"), ("#4", "done")])],
                              roles=["implementation"])
        self.assertEqual(lanes, [("implementation", "A-2")])
        self.assertEqual(waiting["A-1"]["on"], "blocker")

    def test_drafts_wait_on_architecture_and_other_lanes_are_not_ours(self):
        lanes, waiting = pick([st("A-1", status="draft"), st("A-2", lane="architecture", status="ready"),
                               st("A-3", lane="content", status="review")], roles=["implementation"])
        self.assertEqual(lanes, [])
        self.assertEqual(waiting["A-1"]["on"], "architecture")
        self.assertEqual(waiting["A-2"]["on"], "architecture")
        self.assertEqual(waiting["A-3"]["on"], "§8 review / merge")

    def test_an_implementation_story_in_review_is_reported_as_waiting(self):
        lanes, waiting = pick([st("A-1", status="review"), st("A-2", status="done", comp="cli")],
                              roles=["implementation"])
        self.assertEqual(lanes, [])
        self.assertEqual(waiting["A-1"]["on"], "§8 review / merge")
        self.assertNotIn("A-2", waiting)

    def test_a_component_held_by_a_live_lane_is_not_given_to_another_story(self):
        lanes, waiting = pick([st("A-1", status="in-progress", comp="server"), st("A-2", comp="server"),
                               st("A-3", comp="cli")], roles=["implementation"], running=["A-1"])
        self.assertEqual(lanes, [("implementation", "A-3")])
        self.assertIn("component server", waiting["A-2"]["why"])

    def test_a_pm_owned_story_is_reported_even_when_the_pm_lane_runs(self):
        # the PM lane handles sprint transitions, not individual stories
        lanes, waiting = pick([st("A-1", lane="pm", status="blocked")])
        self.assertEqual(waiting["A-1"]["on"], "pm")

    def test_pm_close_respects_the_lane_cap(self):
        s = [st("A-1", status="done")]
        lanes, waiting = pick(s, roles=["pm"], pm_close=True, running=["sre"], max_lanes=1)
        self.assertEqual(lanes, [])
        self.assertEqual(pick(s, roles=["pm"], pm_close=True, max_lanes=1)[0], [("pm", None)])

    def test_carryover_first_and_not_when_a_pr_is_open(self):
        s = [st("A-1", rank=1), st("A-2", status="in-progress", rank=5, comp="cli"),
             st("A-3", status="in-progress", rank=6, comp="infra")]
        lanes, waiting = pick(s, roles=["implementation"], max_lanes=2, open_pr={"A-3"})
        self.assertEqual(lanes, [("implementation", "A-2"), ("implementation", "A-1")])
        self.assertEqual(waiting["A-3"]["why"], "PR open")

    def test_lane_cap_counts_running_lanes(self):
        s = [st("A-1", comp="a"), st("A-2", comp="b"), st("A-3", comp="c")]
        lanes, waiting = pick(s, roles=["implementation"], running={"A-9", "sre"}, max_lanes=3)
        self.assertEqual(lanes, [("implementation", "A-1")])
        self.assertEqual(waiting["A-2"]["why"], "no lane free")

    def test_a_role_with_a_live_session_lane_is_not_started_again(self):
        lanes, waiting = pick([st("A-1", status="draft")], roles=["sre"], running={"sre"})
        self.assertEqual(lanes, [])

    def test_sprint_numbers(self):
        self.assertEqual([coord.sprint_number(v) for v in (5, 5.0, "SPRINT-05", "5", None, "")],
                         [5, 5, 5, 5, None, None])

    def test_a_story_with_a_live_lane_is_not_started_twice(self):
        lanes, waiting = pick([st("A-1")], roles=["implementation"], running={"A-1"})
        self.assertEqual(lanes, [])
        self.assertEqual(waiting["A-1"]["why"], "lane running")

    def test_no_sprint_no_story_lanes(self):
        lanes, _ = pick([st("A-1")], state={"state": "closed", "current": "SPRINT-04"},
                        roles=["implementation", "sre"])
        self.assertEqual(lanes, [])

    def test_stories_of_another_sprint_are_ignored(self):
        lanes, _ = pick([st("A-1", sprint="SPRINT-04")], roles=["implementation"])
        self.assertEqual(lanes, [])

    def test_person_only_stories_are_never_planned_for_a_lane(self):
        po = ("person-only",)
        lanes, waiting = pick([st("A-1", labels=po), st("A-2", comp="cli")], roles=["implementation"])
        self.assertEqual(lanes, [("implementation", "A-2")])
        self.assertEqual((waiting["A-1"]["on"], waiting["A-1"]["why"]), ("the owner", "person-only: run it in your own session"))
        # an SRE story that is person-only doesn't start the SRE lane, and is listed for the owner
        lanes, waiting = pick([st("I-1", lane="sre", status="in-progress", labels=po)], roles=["sre"])
        self.assertEqual(lanes, [])
        self.assertEqual(waiting["I-1"]["on"], "the owner")
        lanes, _ = pick([st("I-1", lane="sre", labels=po), st("I-2", lane="sre")], roles=["sre"])
        self.assertEqual(lanes, [("sre", None)])          # other SRE work still runs the lane

    def test_sre_runs_when_there_are_drafts_or_reviews_unless_unchanged(self):
        s = [st("A-1", status="draft")]
        self.assertEqual(pick(s, roles=["sre"])[0], [("sre", None)])
        self.assertEqual(pick([st("A-2", status="review")], roles=["sre"])[0], [("sre", None)])
        self.assertEqual(pick([st("A-3", status="done")], roles=["sre"])[0], [])
        lanes, waiting = pick(s, roles=["sre"], unchanged={"sre"})
        self.assertEqual((lanes, waiting["-"]["on"]), ([], "a board change"))

    def test_architecture_runs_for_reviews_its_own_stories_and_questions_unless_unchanged(self):
        r = ["architecture"]
        for s in ([st("A-1", status="draft")], [st("A-2", status="review")],
                  [st("A-3", lane="architecture")], [st("A-4", lane="architecture", status="in-progress")],
                  [st("A-5", status="ready", comments=[note("architecture", "implementation")])]):
            self.assertEqual(pick(s, roles=r)[0], [("architecture", None)], s[0].id)
        for s in ([st("A-6", status="ready")], [st("A-7", status="done")],
                  [st("A-8", status="draft", sprint="SPRINT-04")]):
            self.assertEqual(pick(s, roles=r)[0], [], s[0].id)
        self.assertEqual(pick([st("A-9", lane="architecture", status="in-progress")], roles=r,
                              open_pr={"A-9"})[0], [])          # its PR is open: the merge moves it
        self.assertEqual(pick([st("A-1", status="draft")], roles=r, running={"architecture"})[0], [])
        self.assertEqual(pick([st("A-1", status="draft")], state={"state": "closed"}, roles=r)[0], [])
        lanes, waiting = pick([st("A-1", status="draft")], roles=r, unchanged={"architecture"})
        self.assertEqual((lanes, waiting["-"]["on"]), ([], "a board change"))

    def test_architecture_is_a_lane_by_default_and_waits_for_sres_reviews(self):
        cfg = coord.role_settings({}, "architecture")
        self.assertEqual((cfg["sprint_run"], cfg["start_skill"]), ("session", "arch-start-sprint"))
        # it readies or closes a story only after SRE's review of it: never alongside an SRE lane, or it
        # could finish after SRE commented and save a board it never acted on
        for kw in ({}, {"running": {"sre"}}):
            lanes, waiting = pick([st("A-1", status="draft")], roles=["sre", "architecture"], **kw)
            self.assertNotIn(("architecture", None), lanes, kw)
            self.assertEqual(waiting["-"]["on"], "sre", kw)
        lanes, _ = pick([st("A-1", status="draft")], roles=["sre", "architecture"], unchanged={"sre"})
        self.assertEqual(lanes, [("architecture", None)])     # SRE has had its turn on this board
        lanes, _ = pick([st("A-1", status="draft")], roles=["pm", "architecture"], max_lanes=1)
        self.assertEqual(lanes, [("architecture", None)])

    def test_a_session_lane_that_recorded_board_work_is_done(self):
        lane = {"role": "architecture", "story": None}
        base = {"tree_clean": True, "pr_unknown": False, "board_changed": 2}
        self.assertEqual(coord.verdict(lane, dict(base, result_line="recorded 4")), ("recorded 4", True))
        self.assertEqual(coord.verdict(lane, dict(base, result_line="Recorded 1.")), ("Recorded 1", True))
        for bad in ("recorded 0", "recorded", "recorded some"):
            self.assertFalse(coord.verdict(lane, dict(base, result_line=bad))[1], bad)
        self.assertFalse(coord.verdict(lane, dict(base, tree_clean=False, result_line="recorded 2"))[1])
        for changed, why in ((0, "no sprint story changed"), (None, "couldn't be re-read")):   # its words aren't enough
            v = coord.verdict(lane, dict(base, board_changed=changed, result_line="recorded 2"))
            self.assertFalse(v[1], changed)
            self.assertIn(why, v[0])
        self.assertFalse(coord.verdict({"role": "implementation", "story": "A-1"},
                                       dict(base, result_line="recorded 2"))[1])   # a story lane's work is its PR

    def test_contract_reviews_are_listed_unless_an_architecture_lane_finished(self):
        w = [{"story": "A-1", "status": "draft", "lane": "implementation", "why": "contract review pending",
              "on": "architecture"}]
        ran = ["implementation", "architecture"]
        self.assertIn("A-1: contract review pending", coord.render_report("r", [], w, ACTIVE))
        self.assertIn("A-1: contract review pending", coord.render_report("r", [], w, ACTIVE, ran=ran))  # no lane free
        failed = {"lane": "architecture", "verdict": "failed: x", "ok": False, "log": "-"}
        self.assertIn("A-1: contract review pending", coord.render_report("r", [failed], w, ACTIVE, ran=ran))
        done = dict(failed, role="architecture", lane="architecture-r1", verdict="recorded 3", ok=True)
        self.assertNotIn("A-1: contract", coord.render_report("r", [done], w, ACTIVE, ran=ran, now_status={"A-1": "ready"}))
        # a successful lane that left this draft where it was (a ruling for the owner) doesn't hide it
        self.assertIn("A-1: contract", coord.render_report("r", [done], w, ACTIVE, ran=ran, now_status={"A-1": "draft"}))
        self.assertIn("A-1: contract", coord.render_report("r", [done], w, ACTIVE, ran=ran))   # not re-read

    def test_pm_starts_a_sprint_only_when_none_is_active(self):
        for state in ("first", "closed", "planned"):
            self.assertEqual(pick([], state={"state": state}, roles=["pm"])[0], [("pm", None)], state)
        self.assertEqual(pick([st("A-1")], roles=["pm"])[0], [])

    def test_pm_reports_a_finished_sprint_and_closes_only_when_told(self):
        s = [st("A-1", status="done")]
        lanes, waiting = pick(s, roles=["pm"])
        self.assertEqual((lanes, waiting["-"]["on"]), ([], "the owner"))
        lanes, _ = pick(s, roles=["pm"], pm_close=True)
        self.assertEqual(lanes, [("pm", None)])

    def test_the_report_has_no_empty_needs_attention_section(self):
        ok = {"lane": "sre", "verdict": "nothing to do", "ok": True, "log": "-"}
        self.assertNotIn("Needs attention", coord.render_report("r", [ok], [], ACTIVE))
        bad = dict(ok, verdict="failed: x", ok=False)
        self.assertIn("Needs attention", coord.render_report("r", [bad], [], ACTIVE))

    def test_signature_changes_with_board_changes(self):
        a = coord.board_signature([st("A-1")], ACTIVE)
        self.assertEqual(a, coord.board_signature([st("A-1")], ACTIVE))
        self.assertNotEqual(a, coord.board_signature([st("A-1", status="review")], ACTIVE))
        self.assertNotEqual(a, coord.board_signature([st("A-1", blockers=[("A-0", "ready")])], ACTIVE))


class FakeShell(coord.Shell):
    """Real git; canned sprint-state, gh and codex-review; a fake claude that 'opens a PR'."""

    def __init__(self, sprint_out, codex="completed", findings=0, labelled=True, claude_json=None, handled=0,
                 protection="ruleset", last_push=True, bypass=None,
                 exit_code=0, hang=False, on_start=None, start_error=None, make_pr=True,
                 stubborn=False, no_fetch=False, wt_add_error_on=None, head_error=False,
                 hang_only=None, orphan_child=False, gh_fail=False, gh_garbage=False,
                 fetch_fail=False, head_gh_fail=False, status_fail=False, signal_in_start=False,
                 issues=None, merged_prs=None, issues_fail=False, handoffs_opened=None,
                 search_fail=False, handoff_lag=0, lane_user=None):
        self.sprint_out, self.codex, self.findings = sprint_out, codex, findings
        self.handled, self.protection, self.last_push = handled, protection, last_push
        self.bypass = [{"actor_id": None, "actor_type": "OrganizationAdmin", "bypass_mode": "pull_request"}] \
            if bypass is None else bypass
        self.labelled, self.claude_json = labelled, claude_json
        self.exit_code, self.hang, self.on_start, self.start_error = exit_code, hang, on_start, start_error
        self.make_pr = make_pr
        self.stubborn, self.no_fetch, self.wt_add_error_on, self.head_error = stubborn, no_fetch, wt_add_error_on, head_error
        self.hang_only, self.orphan_child, self.gh_fail, self.gh_garbage = hang_only, orphan_child, gh_fail, gh_garbage
        self.fetch_fail, self.head_gh_fail, self.status_fail = fetch_fail, head_gh_fail, status_fail
        self.signal_in_start = signal_in_start
        self.issues = issues if issues is not None else []
        self.merged_prs, self.issues_fail = merged_prs or [], issues_fail
        self.handoffs_opened = {n: 1 for n in (handoffs_opened or ())}      # PR number -> issues opened
        self.search_fail = search_fail
        self.handoff_lag = handoff_lag
        self.wt_adds = 0
        # A fake lane OS user: `sudo -n -u <user> -- cmd` runs cmd as you, but the permission probes
        # answer as a separate account would (its home only; runs/ read-only).
        self.lane_user, self.sudo_cmds = lane_user, []
        self.pr_comments = []   # `gh pr comment` the coordinator posted, as you
        self.procs = []
        self.started, self.prs, self.open_prs = [], {}, []

    def run(self, cmd, cwd=None, check=False, env=None):
        def done(out="", code=0):
            return subprocess.CompletedProcess(cmd, code, out, "")
        if cmd[:2] == ["sudo", "-n"]:
            self.sudo_cmds.append(cmd)
            lu = self.lane_user or {}
            if not lu.get("sudo_ok", True):
                return done("", 1)
            if cmd[2] == "-i":
                if "agento11y" in cmd:
                    return done("/lanes/bin/agento11y\n" if lu.get("o11y", True) else "", 0 if lu.get("o11y", True) else 1)
                return done("motd noise\n/lanes/bin:/usr/bin\n")
            cmd = cmd[cmd.index("--") + 1:]
            if cmd == ["true"]:
                return done()
            if cmd[0] == "grep" and cmd[-1].endswith("agento11y/config.env"):
                key = "token" if "AUTH_TOKEN" in cmd[2] else "local"
                return done("", 0 if lu.get(f"o11y_{key}", key == "token") else 1)
            if cmd[:2] == ["kill", "-TERM"]:
                return done()    # recorded in sudo_cmds: the pids are a test's fiction, not this machine's
            if cmd[:2] == ["python3", "-I"] and cmd[3:4] == ["trust"]:
                return done()    # recorded in sudo_cmds; run for real it would write *your* ~/.claude.json
            if cmd[0] == "cat" and cmd[1].endswith(".claude/settings.json"):
                return done(json.dumps({"enabledPlugins": {"agento11y-claude-code@agento11y": lu.get("o11y_plugin", True)}}))
            if cmd[:2] == ["sudo", "-n"]:
                return done("", 0 if lu.get("can_sudo") else 1)
            if cmd[0] == "test" and cmd[1] in ("-r", "-w"):
                path, home = Path(cmd[2]), lu["home"]
                if cmd[1] == "-w":
                    ok = path.is_relative_to(home) and not path.is_relative_to(home / "runs") \
                        and not lu.get("no_write")
                else:
                    ok = (path.is_relative_to(home) or lu.get("reads_outside")) \
                        and not (lu.get("no_read_runs") and path.is_relative_to(home / "runs"))
                return done("", 0 if ok else 1)
            cwd = None
        if len(cmd) > 1 and cmd[1].endswith("sprint-state"):
            return done(self.sprint_out)
        if len(cmd) > 1 and cmd[1].endswith("codex-review"):
            return done(json.dumps({"state": self.codex, "findings": [{}] * self.findings, "handled": self.handled}))
        if cmd[:2] == ["gh", "api"] and "/rules/branches/" in cmd[2]:
            n = {"ruleset": 1, "zero": 0}.get(self.protection)
            return done(json.dumps([{"type": "pull_request", "ruleset_id": 7, "parameters": {
                "required_approving_review_count": n, "require_last_push_approval": self.last_push,
                "dismiss_stale_reviews_on_push": False}}] if n is not None else []))
        if cmd[:2] == ["gh", "api"] and cmd[2].endswith("/rulesets/7"):
            return done(json.dumps({"id": 7, "bypass_actors": self.bypass}))
        if cmd[:2] == ["gh", "api"] and cmd[2].endswith("/protection"):
            if self.protection == "classic":
                return done(json.dumps({"required_pull_request_reviews": {
                    "required_approving_review_count": 1, "require_last_push_approval": self.last_push}}))
            return done("", 1)
        if cmd[:3] == ["gh", "api", "user"]:
            return done("solo-seven\n")
        if cmd[0] == "gh" and cmd[1:3] == ["pr", "comment"]:
            self.pr_comments.append(cmd[3:])
            return done()
        if cmd[0] == "gh" and cmd[1:3] == ["search", "issues"]:
            if self.search_fail:
                return done("", 1)
            hit = [n for n in self.handoffs_opened if cmd[-1] == f"https://gh/pull/{n}"]  # the url, only
            if hit and self.handoff_lag:
                self.handoff_lag -= 1
                hit = []
            return done(json.dumps([{"number": 900 + i} for i in range(self.handoffs_opened[hit[0]])] if hit else []))
        if cmd[0] == "gh" and cmd[1:3] == ["issue", "list"]:
            if self.issues_fail:
                return done("", 1)
            return done(json.dumps(self.only([i for i in self.issues if not i.get("closed")], cmd)))
        if cmd[0] == "gh" and cmd[1:3] == ["pr", "list"] and "--state" in cmd and \
                cmd[cmd.index("--state") + 1] == "merged":
            return done(json.dumps(self.only(self.merged_prs, cmd)))
        if cmd[0] == "gh" and cmd[1:3] == ["pr", "view"]:
            hit = [p for p in self.prs.values() if str(p["number"]) == cmd[3]]
            return done(json.dumps({"state": hit[0]["state"]}) if hit else "", 0 if hit else 1)
        if cmd[0] == "gh" and cmd[1:3] == ["pr", "list"]:
            if "--head" in cmd:
                if self.head_gh_fail:
                    return done("", 1)
                pr = self.prs.get(cmd[cmd.index("--head") + 1])
                return done(json.dumps([pr] if pr else []))
            if self.gh_fail:
                return done("", 1)
            if self.gh_garbage:
                return done("<html>502</html>")
            return done(json.dumps(self.open_prs))
        if self.status_fail and cmd[:3] == ["git", "status", "--porcelain"] and ".claude/worktrees" in str(cwd):
            return done("", 124)
        if self.fetch_fail and cmd[:2] == ["git", "fetch"]:
            return done("", 128)
        if self.no_fetch and cmd[:2] == ["git", "fetch"]:
            return done()
        if cmd[:3] == ["git", "worktree", "add"]:
            self.wt_adds += 1
            if self.wt_add_error_on == self.wt_adds:
                raise OSError("disk full")
        if self.head_error and cmd[:2] == ["git", "rev-parse"] and cmd[-1] == "HEAD" and cwd and ".claude/worktrees" in str(cwd):
            raise FileNotFoundError("git")
        return super().run(cmd, cwd, check, env)

    def open_handoff(self, n):
        self.handoffs_opened[n] = self.handoffs_opened.get(n, 0) + 1

    @staticmethod
    def only(rows, cmd):
        """What `gh ... --json a,b` returns: only the fields asked for."""
        want = cmd[cmd.index("--json") + 1].split(",") if "--json" in cmd else None
        return [{k: v for k, v in r.items() if k in want} for r in rows] if want else rows

    def start(self, cmd, cwd, env, log):
        wrap = None
        if cmd[:2] == ["sudo", "-n"]:   # sudo -n -u <user> -- python3 -I lanectl exec <envfile> <cwd> -- <cmd>
            self.sudo_cmds.append(cmd)
            inner = cmd[cmd.index("--") + 1:]
            wrap, cmd = inner[:inner.index("--") + 1], inner[inner.index("--") + 1:]
            env, cwd = json.loads(Path(wrap[4]).read_text()), wrap[5]
        if self.start_error:
            raise self.start_error
        sid0 = cmd[cmd.index("--session-id") + 1]
        seen = subprocess.run([str(Path(cwd) / ".claude/bin/role"), "show", sid0], capture_output=True,
                              text=True, cwd=cwd).stdout.strip()  # the role the session starts with
        self.started.append({"cmd": cmd, "cwd": cwd, "env": env, "role": seen})
        sid = cmd[cmd.index("--session-id") + 1]
        branch = f"impl/{env['COORDINATOR_LANE']}"
        subprocess.run(["git", "switch", "-q", "-C", branch], cwd=cwd, check=True)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True).stdout.strip()
        n = len(self.started)
        role_name = cmd[cmd.index("-p") + 1].split("`")[1]
        labels = [{"name": f"role:{role_name}"}] if self.labelled else []
        if self.make_pr:
            self.prs[branch] = {"number": n, "state": "OPEN", "url": f"https://gh/pull/{n}",
                                "headRefOid": head, "labels": labels, "mergedAt": None}
        if self.on_start:
            self.on_start(self, cwd, cmd, env)
        log.write_text(json.dumps(self.claude_json or {
            "result": f"done.\nLANE RESULT: https://gh/pull/{n}", "is_error": False,
            "total_cost_usd": 1.25, "num_turns": 30, "session_id": sid}))
        cmdline = ["sleep", "30"] if self.hang else ["sh", "-c", f"exit {self.exit_code}"]
        if self.hang_only and self.hang_only in env["COORDINATOR_LANE"]:
            cmdline = ["sleep", "30"]
        if self.orphan_child:  # the leader obeys SIGTERM; its child doesn't
            cmdline = ["sh", "-c", "(trap '' TERM; while :; do sleep 1; done) & wait"]
        if self.stubborn:
            cmdline = ["sh", "-c", "trap '' TERM; while :; do sleep 1; done"]
        self.procs.append(subprocess.Popen((wrap or []) + cmdline, cwd=cwd, start_new_session=True))
        if self.signal_in_start:  # Ctrl-C lands after the process exists, before the caller has it
            os.kill(os.getpid(), signal.SIGINT)
        return self.procs[-1]


SPRINT = "state:   active\ncurrent: SPRINT-05\nnext:    SPRINT-06\n"
REAL_UID_IN_USE, REAL_STRAYS = coord.uid_in_use, coord.strays   # RunTest stubs them: the machine's
                                                                # own UIDs are none of a test's business
_KEYS = tempfile.TemporaryDirectory()   # one RSA key for every test: openssl genrsa is the slow part


def lane_git_config(env: dict, key: str) -> list[str]:
    """key's values as git reads them in a lane, run the way buf runs git: empty variables dropped,
    and none of your own config."""
    env = {k: v for k, v in env.items() if v} | {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    r = subprocess.run(["git", "config", "--get-all", key], capture_output=True, text=True, env=env,
                       cwd="/", check=True)
    return r.stdout.splitlines()


def app_key() -> Path:
    key = Path(_KEYS.name) / "app.pem"
    if not key.exists():
        subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
        key.chmod(0o600)
    return key


def jwt_claims(jwt: str) -> dict:
    body = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


class FakeApp:
    """GitHub's endpoints for the lanes' app: its installation on the repo, and tokens."""

    def __init__(self, perms=None, installed=True, life=3600, fail_mint=False, refuse_review=False):
        self.perms = dict(coord.LANE_APP_PERMS) if perms is None else perms
        self.installed, self.life, self.fail_mint = installed, life, fail_mint
        self.refuse_review, self.review_requests = refuse_review, []
        self.calls, self.minted = [], 0

    def __call__(self, method, path, auth, body=None):
        if method == "POST" and path.endswith("/requested_reviewers"):   # with the installation token
            assert auth == f"ghs_{self.minted}", auth
            self.review_requests.append((path, body))
            if self.refuse_review:
                raise coord.CoordError(f"GitHub answered 422 to POST {path}")
            return {}
        self.calls.append((method, path, auth, body))
        assert jwt_claims(auth)["iss"] == "123", auth      # the app's login, never a token
        if (method, path) == ("GET", "repos/example/game-server/installation"):
            if not self.installed:
                raise coord.CoordError(f"GitHub answered 404 to GET {path}")
            return {"id": 42, "app_slug": "valesordev-lanes", "permissions": self.perms}
        if (method, path) == ("POST", "app/installations/42/access_tokens"):
            if self.fail_mint:
                raise coord.CoordError(f"GitHub answered 500 to POST {path}")
            self.minted += 1
            exp = datetime.fromtimestamp(time.time() + self.life, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return {"token": f"ghs_{self.minted}", "expires_at": exp}
        raise AssertionError((method, path))


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.origin = base / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        self.repo = base / "game-server"
        self.repo.mkdir()
        g = lambda *a: subprocess.run(["git", *a], cwd=self.repo, check=True, capture_output=True)  # noqa: E731
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        g("config", "commit.gpgsign", "false")
        r = subprocess.run([sys.executable, str(TOOLKIT / "install.py"), "--root", str(FIXTURE), "game-server", str(self.repo)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        md = self.repo / ".claude/roles/_repo.md"
        md.write_text(md.read_text().replace("story_prefix: GS\n", "story_prefix: GS\nstories: github\nsprints: project\n"))
        g("add", "-A")
        g("commit", "-q", "-m", "init")
        g("remote", "add", "origin", str(self.origin))
        g("push", "-q", "origin", "main")
        self.old = os.getcwd()
        os.chdir(self.repo)
        self.app_cfg = base / "app" / "lane-app.json"
        self.app_cfg.parent.mkdir()
        shutil.copyfile(app_key(), self.app_cfg.parent / "lane-app.pem")
        (self.app_cfg.parent / "lane-app.pem").chmod(0o600)
        self.app_cfg.write_text(json.dumps({"app_id": 123}))
        self.app = FakeApp()
        self.same_user = True
        self.o11y_cfg = base / "agento11y-config.env"
        for p in (mock.patch.dict(os.environ, {"COORDINATOR_LANE_USER_CONFIG": str(base / "no-lane-user.json")}),
                  mock.patch.object(coord, "AGENTO11Y_CONFIG", self.o11y_cfg),
                  mock.patch.object(coord, "strays", lambda uid, proc=None: self.left),
                  mock.patch.object(coord, "uid_in_use", lambda uid: self.uid_taken)):
            p.start()
            self.addCleanup(p.stop)
        self.left, self.uid_taken = [], None
        for k in [k for k in os.environ if k.startswith(("AGENTO11Y_", "SIGIL_"))]:   # yours: not the tests'
            del os.environ[k]
        for p in (mock.patch.dict(os.environ, {"COORDINATOR_APP_CONFIG": str(self.app_cfg)}),
                  mock.patch.object(coord, "app_api", lambda *a, **k: self.app(*a, **k))):
            p.start()
            self.addCleanup(p.stop)
        self.gh = test_story.FakeGH()
        self.a = self.gh.add("GS-SRV-001", "server thing", Status="ready", Lane="implementation", Rank=1,
                             Sprint="SPRINT-05", Component="server")
        self.gh.add("GS-CLI-001", "cli thing", Status="ready", Lane="implementation", Rank=2,
                    Sprint="SPRINT-05", Component="cli")
        self.gh.add("GS-SRV-002", "blocked", Status="ready", Lane="implementation", Rank=3,
                    Sprint="SPRINT-05", Component="infra")
        self.gh.links.append((3, "/dependencies/blocked_by", self.gh.issues[self.a]["id"]))
        self.gh.add("GS-SRV-003", "contract review pending", Status="draft", Lane="implementation",
                    Sprint="SPRINT-05", Component="cli")

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def go(self, *args, shell=None):
        shell = shell or FakeShell(SPRINT)
        if self.same_user and args and args[0] in ("run", "clean") and "--same-os-user" not in args:
            args = (*args, "--same-os-user")   # most tests are about lanes, not whose OS user runs them
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = coord.main(list(args), gh=self.gh, sh=shell, claude="claude-fake")
        return code, out.getvalue() + err.getvalue(), shell

    def comment(self, *args, env=None):
        with mock.patch.dict(os.environ, env or {}):
            for k in ("CLAUDE_SESSION_ID", "COORDINATOR_LANE"):
                if k not in (env or {}):
                    os.environ.pop(k, None)
            return self.go("comment", "GS-SRV-001", *args)

    def test_comment_posts_for_a_role_as_the_coordinator(self):
        code, out, _ = self.comment("--to", "sre", "--body", "chart keys are missing: see PR #9")
        self.assertEqual(code, 0, out)
        self.assertIn("GS-SRV-001: comment posted (**For sre:**)", out)
        self.assertEqual(self.gh.comments[1][-1],
                         "**For sre:** chart keys are missing: see PR #9\n\n<!-- from: coordinator -->")
        body = Path(self.tmp.name) / "note.md"
        body.write_text("line one\nline two\n")
        code, out, _ = self.comment("--to", "sre", "--body-file", str(body))
        self.assertEqual(code, 0, out)
        self.assertTrue(self.gh.comments[1][-1].startswith("**For sre:**\n\nline one\nline two"))

    def test_comment_works_from_a_role_free_session_and_under_role_none(self):
        code, out, _ = self.comment("--to", "sre", "--body", "x", env={"CLAUDE_SESSION_ID": "dispatcher"})
        self.assertEqual(code, 0, out)
        coord.role.put_role(self.repo, "owner-s", "none")
        code, out, _ = self.comment("--to", "sre", "--body", "x", env={"CLAUDE_SESSION_ID": "owner-s"})
        self.assertEqual(code, 0, out)

    def test_comment_is_refused_in_a_lane_and_in_a_role_session(self):
        before = len(self.gh.comments.get(1, []))
        code, out, _ = self.comment("--to", "sre", "--body", "x", env={"COORDINATOR_LANE": "implementation-1"})
        self.assertEqual(code, 2)
        self.assertIn("a lane posts with `story comment`", out)
        coord.role.put_role(self.repo, "impl-s", "implementation")
        code, out, _ = self.comment("--to", "sre", "--body", "x", env={"CLAUDE_SESSION_ID": "impl-s"})
        self.assertEqual(code, 2)
        self.assertIn("this session's role is implementation", out)
        self.assertEqual(len(self.gh.comments.get(1, [])), before)

    def test_comment_needs_a_real_role_and_a_body(self):
        code, out, _ = self.comment("--to", "nobody", "--body", "x")
        self.assertEqual(code, 2)
        self.assertIn("no role 'nobody'", out)
        code, out, _ = self.comment("--to", "none", "--body", "x")
        self.assertEqual(code, 2)
        code, out, _ = self.comment("--to", "sre", "--body", "  ")
        self.assertIn("give --body or --body-file", out)

    def test_plan_lists_lanes_and_what_everything_else_waits_on(self):
        code, out, _ = self.go("plan", "--roles", "implementation")
        self.assertEqual(code, 0, out)
        self.assertIn("implementation  GS-SRV-001", out)
        self.assertIn("implementation  GS-CLI-001", out)
        self.assertIn("blocked by GS-SRV-001 (ready)", out)
        self.assertIn("contract review pending  [waiting on architecture]", out)

    def test_plan_json(self):
        code, out, _ = self.go("plan", "--roles", "implementation", "--json")
        data = json.loads(out)
        self.assertEqual([l["story"] for l in data["lanes"]], ["GS-SRV-001", "GS-CLI-001"])

    def test_run_without_apply_starts_nothing(self):
        code, out, sh = self.go("run", "--roles", "implementation")
        self.assertEqual((code, sh.started), (0, []))
        self.assertIn("dry run", out)

    def test_run_starts_isolated_role_sessions_and_reports_live_state(self):
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                "--max-lanes", "2")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(sh.started), 2)
        for s in sh.started:
            cmd = s["cmd"]
            sid = cmd[cmd.index("--session-id") + 1]
            # the role is recorded for that session before it starts
            self.assertEqual(s["role"], "implementation")
            self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
            self.assertIn("--allowedTools", cmd)
            deny = cmd[cmd.index("--disallowedTools") + 1]
            self.assertIn("Bash(gh pr merge:*)", deny)
            self.assertIn("Bash(git push --force:*)", deny)
            prompt = cmd[cmd.index("-p") + 1]
            self.assertIn("Never merge", prompt)
            self.assertIn("exactly one story", prompt)
            self.assertTrue(Path(s["cwd"]).is_dir())
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=s["cwd"], capture_output=True, text=True).stdout
            main = subprocess.run(["git", "rev-parse", "origin/main"], cwd=self.repo, capture_output=True, text=True).stdout
            self.assertEqual(head, main)
        self.assertIn("PR open, Codex clean", out)
        self.assertIn("https://gh/pull/1", out)
        self.assertIn("$1.25", out)
        self.assertTrue(list((self.repo / ".git/claude-coordinator/runs").glob("*/report.md")))

    def hide_story(self, sid, listings=1):
        iid = next(k for k, v in self.gh.items.items() if v["fields"].get("Story ID") == sid)
        self.gh.hidden[iid] = listings

    def set_status(self, sid, status):
        next(v for v in self.gh.items.values() if v["fields"].get("Story ID") == sid)["fields"]["Status"] = status

    def wt(self, name):
        return self.repo / ".claude/worktrees" / name

    def test_report_shows_the_status_the_lane_left_not_the_one_it_started_with(self):
        sh = FakeShell(SPRINT, on_start=lambda s, cwd, cmd, env: self.set_status("GS-SRV-001", "in-progress"))
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        row = next(l for l in out.splitlines() if l.startswith("| implementation-gs-srv-001"))
        self.assertIn("| in-progress |", row)

    def test_a_lagging_listing_after_the_lane_is_waited_out(self):
        sh = FakeShell(SPRINT, on_start=lambda s, cwd, cmd, env: self.hide_story("GS-SRV-001", 1))
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        row = next(l for l in out.splitlines() if l.startswith("| implementation-gs-srv-001"))
        self.assertIn("| ready |", row)
        self.assertNotIn("unreadable", out)

    def test_resuming_a_story_reuses_the_lanes_worktree(self):
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1")
        work = self.wt("implementation-gs-srv-001")
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
                        "commit", "-q", "--allow-empty", "-m", "lane work"], cwd=work, check=True)
        self.set_status("GS-SRV-001", "in-progress")
        sh = FakeShell(SPRINT)
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertNotIn("already exists", out)
        self.assertEqual(len(sh.started), 1, out)
        self.assertEqual(Path(sh.started[0]["cwd"]), work)
        log = subprocess.run(["git", "log", "--format=%s", "-1"], cwd=work, capture_output=True, text=True).stdout
        self.assertEqual(log.strip(), "lane work")  # the earlier commit is still there

    def test_worktrees_are_ignored_by_the_repo(self):
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1")
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(status.stdout.strip(), "")
        self.assertIn(".claude/worktrees/", (self.repo / ".git/info/exclude").read_text())

    def test_clean_keeps_committed_work_that_no_pr_carries(self):
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1")
        work = self.wt("implementation-gs-srv-001")
        subprocess.run(["git", "checkout", "-q", "--detach"], cwd=work, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
                        "commit", "-q", "--allow-empty", "-m", "stranded"], cwd=work, check=True)
        code, out, _ = self.go("clean", "--apply", shell=FakeShell(SPRINT))
        self.assertIn("keep    implementation-gs-srv-001", out)
        self.assertIn("unpushed", out)
        self.assertTrue(work.is_dir())

    def test_clean_keeps_a_branch_with_commits_newer_than_its_merged_pr(self):
        sh = FakeShell(SPRINT)
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1", shell=sh)
        work = self.wt("implementation-gs-srv-001")
        branch = next(iter(sh.prs))
        sh.prs[branch]["state"] = "MERGED"          # merged at the commit the PR had...
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
                        "commit", "-q", "--allow-empty", "-m", "after merge"], cwd=work, check=True)
        code, out, _ = self.go("clean", "--apply", shell=sh)  # ...and the lane has gone on since
        self.assertIn("keep    implementation-gs-srv-001", out)

    def test_clean_keeps_untracked_files(self):
        sh = FakeShell(SPRINT)
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1", shell=sh)
        work = self.wt("implementation-gs-srv-001")
        sh.prs[next(iter(sh.prs))]["state"] = "MERGED"
        (work / "notes.txt").write_text("not committed")
        code, out, _ = self.go("clean", "--apply", shell=sh)
        self.assertIn("keep    implementation-gs-srv-001", out)
        self.assertTrue(work.is_dir())

    def test_clean_leaves_a_live_lane_alone(self):
        sh = FakeShell(SPRINT)
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1", shell=sh)
        work = self.wt("implementation-gs-srv-001")
        sh.prs[next(iter(sh.prs))]["state"] = "MERGED"
        act = self.repo / ".git/claude-coordinator/active"
        act.mkdir(parents=True, exist_ok=True)
        (act / "x.json").write_text(json.dumps({"lane": "x", "role": "implementation", "story": "GS-SRV-001",
                                                "pid": os.getppid(), "worktree": str(work)}))
        code, out, _ = self.go("clean", "--apply", shell=sh)
        self.assertNotIn("remove", out)
        self.assertTrue(work.is_dir())

    def test_a_nonzero_exit_fails_the_lane_even_with_a_clean_looking_log(self):
        sh = FakeShell(SPRINT, exit_code=3)
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1)
        self.assertIn("failed: claude exited 3", out)

    def test_a_lane_past_its_deadline_is_killed_and_reported(self):
        sh = FakeShell(SPRINT, hang=True)
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", "--timeout-min", "0.001", shell=sh)
        self.assertEqual(code, 1)
        self.assertIn("failed: timed out", out)
        self.assertIsNotNone(sh.procs[0].poll(), "the lane's process must be dead, not just reported dead")
        self.assertFalse(list((self.repo / ".git/claude-coordinator/active").glob("*.json")))

    def test_every_deny_entry_reaches_the_lane(self):
        want = ["Bash(gh pr merge:*)", "Bash(gh pr close:*)", "Bash(gh pr review:*)", "Bash(gh pr ready:*)",
                "Bash(git push --force:*)", "Bash(git push -f:*)", "Bash(git push --force-with-lease:*)",
                "Bash(git push origin main:*)", "Bash(git push origin HEAD:main:*)",
                "Bash(git reset --hard:*)"]
        for w in want:
            self.assertIn(w, coord.DENY)
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                "--max-lanes", "1")
        cmd = sh.started[0]["cmd"]
        self.assertEqual(cmd[cmd.index("--disallowedTools") + 1].split(","), coord.DENY)

    def test_a_failed_sre_lane_does_not_stop_it_being_planned_again(self):
        sh = FakeShell(SPRINT, exit_code=3)
        self.go("run", "--apply", "--roles", "sre", "--poll", "0.05", shell=sh)
        code, out, _ = self.go("plan", "--roles", "sre", shell=FakeShell(SPRINT))
        self.assertNotIn("nothing changed", out)
        self.assertIn("sre", out.split("Lanes to start:")[1])

    def test_a_lane_with_no_result_line_is_not_ok(self):
        sh = FakeShell(SPRINT, make_pr=False, claude_json={"result": "I did some things.", "is_error": False})
        code, out, _ = self.go("run", "--apply", "--roles", "sre", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 1)
        self.assertIn("no result line", out)

    def test_claude_missing_fails_that_lane_and_leaves_no_worktree_behind(self):
        sh = FakeShell(SPRINT, start_error=FileNotFoundError("claude"))
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1)
        self.assertIn("failed", out)
        self.assertFalse((self.repo / ".claude/worktrees/implementation-gs-srv-001").exists())

    def test_session_variables_do_not_leak_into_a_lane(self):
        sh = FakeShell(SPRINT)
        with mock.patch.dict(os.environ, {"CLAUDE_SESSION_ID": "parent", "CLAUDE_PROJECT_DIR": "/p",
                                          "CLAUDECODE": "1"}):
            self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                    shell=sh)
        env = sh.started[0]["env"]
        for k in ("CLAUDE_SESSION_ID", "CLAUDE_PROJECT_DIR", "CLAUDECODE"):
            self.assertNotIn(k, env)

    def test_a_pr_for_a_different_commit_than_the_lanes_head_is_not_clean(self):
        sh = FakeShell(SPRINT, on_start=lambda s, cwd, cmd, env: s.prs[next(iter(s.prs))].update(headRefOid="0" * 40))
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1)
        self.assertIn("differs from the lane's HEAD", out)

    def test_an_invalid_sprint_state_stops_the_coordinator(self):
        code, out, _ = self.go("plan", shell=FakeShell("state: invalid\nreasons: x\n"))
        self.assertEqual(code, 2)

    def test_codex_not_finished_is_reported_and_fails_the_run(self):
        sh = FakeShell(SPRINT, codex="pending")
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1, out)
        self.assertIn("PR open, Codex pending", out)
        self.assertIn("## Needs attention", out)

    def test_unhandled_codex_findings_are_reported(self):
        sh = FakeShell(SPRINT, findings=2)
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1)
        self.assertIn("(2 unhandled)", out)

    def test_a_pr_without_the_role_label_is_flagged(self):
        sh = FakeShell(SPRINT, labelled=False)
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1)
        self.assertIn("without role:implementation label", out)

    def test_a_failed_claude_is_a_failed_lane(self):
        sh = FakeShell(SPRINT, claude_json={"result": "boom", "is_error": True})
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1)
        self.assertIn("failed: boom", out)

    def hold_lock(self):
        p = self.repo / ".git/claude-coordinator/lock"
        p.parent.mkdir(parents=True, exist_ok=True)
        f = open(p, "a+")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(f.close)
        return f

    def test_a_second_run_cannot_start_while_one_holds_the_lock(self):
        self.hold_lock()
        code, out, _ = self.go("run", "--roles", "implementation")
        self.assertEqual(code, 2)
        self.assertIn("another coordinator run is active", out)

    def test_a_lock_file_nobody_holds_is_not_a_lock(self):
        lock = self.repo / ".git/claude-coordinator/lock"
        lock.parent.mkdir(parents=True)
        lock.write_text(json.dumps({"pid": os.getppid(), "since": "then"}))  # a live pid, but no flock
        code, out, _ = self.go("run", "--roles", "implementation")
        self.assertEqual(code, 0, out)

    def test_the_lock_is_free_again_after_a_run(self):
        self.go("run", "--roles", "implementation")
        f = self.hold_lock()  # would raise if the run left it locked
        self.assertTrue(f)

    def test_a_lane_that_is_running_is_not_started_again(self):
        act = self.repo / ".git/claude-coordinator/active"
        act.mkdir(parents=True)
        (act / "implementation-gs-srv-001.json").write_text(json.dumps(
            {"lane": "implementation-gs-srv-001", "role": "implementation", "story": "GS-SRV-001",
             "pid": os.getppid(), "worktree": "/x", "started": "t", "run": "r"}))
        code, out, _ = self.go("plan", "--roles", "implementation")
        self.assertIn("Running: implementation-gs-srv-001", out)
        self.assertNotIn("implementation  GS-SRV-001", out)
        self.assertIn("lane running", out)

    def test_dead_active_records_are_dropped(self):
        act = self.repo / ".git/claude-coordinator/active"
        act.mkdir(parents=True)
        f = act / "x.json"
        f.write_text(json.dumps({"lane": "x", "story": "GS-SRV-001", "role": "implementation",
                                 "pid": 2 ** 22 + 1}))
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1")
        self.assertFalse(f.exists())

    def test_story_missing_from_a_lagging_listing_is_still_found_after_the_lane(self):
        iid = next(k for k, v in self.gh.items.items() if v["fields"].get("Story ID") == "GS-SRV-001")
        self.gh.hidden[iid] = 0
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1")
        self.assertEqual(code, 0, out)

    def test_unknown_or_unchartered_role_is_refused(self):
        for roles in ("nonesuch", "none"):
            code, out, _ = self.go("plan", "--roles", roles)
            self.assertEqual(code, 2, roles)
        self.assertIn("no charter", out)

    def test_a_repo_still_on_story_files_is_refused(self):
        md = self.repo / ".claude/roles/_repo.md"
        md.write_text(md.read_text().replace("stories: github\n", ""))
        code, out, _ = self.go("plan")
        self.assertEqual(code, 2)
        self.assertIn("stories: github", out)

    def test_clean_removes_merged_lane_worktrees_only(self):
        sh = FakeShell(SPRINT)
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "2", shell=sh)
        branches = list(sh.prs)
        sh.prs[branches[0]]["state"] = "MERGED"
        code, out, _ = self.go("clean", "--apply", shell=sh)
        self.assertIn("remove  implementation-gs-srv-001", out)
        self.assertIn("keep    implementation-gs-cli-001", out)
        wts = {p.name for p in (self.repo / ".claude/worktrees").iterdir()}
        self.assertEqual(wts, {"implementation-gs-cli-001", "implementation-gs-cli-001.scratch"})   # with its worktree

    def test_sre_lane_is_skipped_when_the_board_hasnt_changed(self):
        args = ("run", "--apply", "--roles", "sre", "--poll", "0.05")
        code, out, sh = self.go(*args)
        self.assertEqual(len(sh.started), 1, out)
        # the lane ran and ended with no PR: "finished". Same board next time: skip.
        sh2 = FakeShell(SPRINT)
        code, out, _ = self.go("plan", "--roles", "sre", shell=sh2)
        self.assertIn("nothing changed since SRE's last lane", out)


    # ---- round 2 ----

    def test_a_blocked_lane_is_not_ok_whatever_words_it_ends_with(self):
        sh = FakeShell(SPRINT, make_pr=False, claude_json={
            "result": "LANE RESULT: blocked: denied push, PR not Codex clean", "is_error": False})
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1, out)
        self.assertIn("## Needs attention", out)

    def test_an_sre_lane_that_claims_nothing_was_done_is_not_ok_and_is_planned_again(self):
        sh = FakeShell(SPRINT, make_pr=False, claude_json={
            "result": "LANE RESULT: nothing was done, everything failed", "is_error": False})
        code, out, _ = self.go("run", "--apply", "--roles", "sre", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 1, out)
        code, out, _ = self.go("plan", "--roles", "sre", shell=FakeShell(SPRINT))
        self.assertNotIn("nothing changed", out)

    def test_a_lane_that_says_recorded_but_changed_no_story_is_not_ok(self):
        sh = FakeShell(SPRINT, make_pr=False, claude_json={"result": "LANE RESULT: recorded 2", "is_error": False})
        code, out, _ = self.go("run", "--apply", "--roles", "sre", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 1, out)
        self.assertIn("no sprint story changed", out)

    def test_an_sre_lane_that_says_exactly_nothing_to_do_is_ok_and_skipped_next_time(self):
        sh = FakeShell(SPRINT, make_pr=False, claude_json={
            "result": "LANE RESULT: nothing to do", "is_error": False})
        code, out, _ = self.go("run", "--apply", "--roles", "sre", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 0, out)
        code, out, _ = self.go("plan", "--roles", "sre", shell=FakeShell(SPRINT))
        self.assertIn("nothing changed since SRE's last lane", out)

    def advance_origin(self):
        g = lambda *a: subprocess.run(["git", *a], cwd=self.repo, check=True, capture_output=True)  # noqa: E731
        g("-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
          "commit", "-q", "--allow-empty", "-m", "main moves")
        g("push", "-q", "origin", "main")
        return g("rev-parse", "HEAD").stdout.decode().strip()

    def head(self, path):
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True).stdout.strip()

    def test_a_second_sre_lane_starts_from_the_new_origin_main(self):
        sre = dict(make_pr=False, claude_json={"result": "LANE RESULT: nothing to do", "is_error": False})
        self.go("run", "--apply", "--roles", "sre", "--poll", "0.05", shell=FakeShell(SPRINT, **sre))
        new = self.advance_origin()
        self.set_status("GS-SRV-003", "review")  # the board changed, so SRE is planned again
        seen = []
        code, out, sh = self.go("run", "--apply", "--roles", "sre", "--poll", "0.05",
                                shell=FakeShell(SPRINT, on_start=lambda s_, cwd, cmd, env: seen.append(self.head(cwd)),
                                                **sre))
        self.assertEqual(len(sh.started), 1, out)
        self.assertEqual(seen, [new])

    def test_a_session_lane_gets_its_own_worktree_while_an_earlier_one_holds_a_pr(self):
        # an architecture lane's ADR PR, or SRE's, keeps its commits in that lane's worktree until it merges
        sre = dict(make_pr=False, claude_json={"result": "LANE RESULT: nothing to do", "is_error": False})
        code, out, sh = self.go("run", "--apply", "--roles", "sre", "--poll", "0.05", shell=FakeShell(SPRINT, **sre))
        first = Path(sh.started[0]["cwd"])
        self.assertFalse(first.exists(), out)            # clean when it ended: removed with its scratch
        self.assertFalse(first.with_name(first.name + ".scratch").exists())
        commit = lambda s_, cwd, cmd, env: subprocess.run(  # noqa: E731
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
             "commit", "-q", "--allow-empty", "-m", "lane work"], cwd=cwd, check=True)
        self.set_status("GS-SRV-003", "review")
        code, out, sh = self.go("run", "--apply", "--roles", "sre", "--poll", "0.05",
                                shell=FakeShell(SPRINT, on_start=commit, **sre))
        held = Path(sh.started[0]["cwd"])
        self.assertTrue(held.exists(), out)              # its commits are kept
        self.set_status("GS-SRV-002", "review")
        code, out, sh = self.go("run", "--apply", "--roles", "sre", "--poll", "0.05", shell=FakeShell(SPRINT, **sre))
        self.assertEqual((code, len(sh.started)), (0, 1), out)
        self.assertNotEqual(Path(sh.started[0]["cwd"]), held)
        self.assertTrue(held.exists())

    def test_a_ready_story_with_a_clean_leftover_worktree_starts_from_origin_main(self):
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1")
        new = self.advance_origin()
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                "--max-lanes", "1", shell=FakeShell(SPRINT))
        self.assertEqual(self.head(self.wt("implementation-gs-srv-001")), new, out)

    def test_a_directory_in_the_way_that_is_not_a_worktree_is_refused(self):
        self.wt("implementation-gs-srv-001").mkdir(parents=True)
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                "--max-lanes", "1")
        self.assertEqual(code, 1)
        self.assertIn("isn't a worktree", out)

    def test_a_worktree_failure_on_the_second_lane_does_not_orphan_the_first(self):
        sh = FakeShell(SPRINT, wt_add_error_on=2)
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "2", shell=sh)
        self.assertEqual(code, 1, out)
        self.assertIn("PR open, Codex clean", out)    # lane 1 was still monitored and reported
        self.assertIn("disk full", out)               # lane 2's failure is in the report
        self.assertFalse(list((self.repo / ".git/claude-coordinator/active").glob("*.json")))

    def test_an_interrupted_run_stops_its_lanes_and_frees_the_lock(self):
        sh = FakeShell(SPRINT, hang=True)
        with self.interrupt_at_poll(0.07):
            with self.assertRaises(KeyboardInterrupt):
                self.go("run", "--apply", "--roles", "implementation", "--poll", "0.07", "--max-lanes", "2",
                        shell=sh)
        for p in sh.procs:
            p.wait(5)
            self.assertIsNotNone(p.poll())
        self.assertFalse(list((self.repo / ".git/claude-coordinator/active").glob("*.json")))
        self.hold_lock()

    def test_a_failure_while_inspecting_a_lane_keeps_the_report(self):
        sh = FakeShell(SPRINT, head_error=True)
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1, out)
        self.assertIn("inspection failed", out)

    def guard(self, sh):
        """Fail instead of hang if the coordinator never kills a lane that ignores SIGTERM."""
        def reap():
            for pr in sh.procs:
                try:
                    os.killpg(pr.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                pr.wait()
        dog = threading.Timer(15, lambda: [os.killpg(pr.pid, signal.SIGKILL)
                                           for pr in sh.procs if pr.poll() is None])
        dog.start()
        self.addCleanup(reap)
        self.addCleanup(dog.cancel)
        return dog

    def test_a_lane_that_ignores_sigterm_is_killed(self):
        sh = FakeShell(SPRINT, stubborn=True)
        dog = self.guard(sh)
        with mock.patch.object(coord, "KILL_GRACE", 0.3):
            code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                   "--max-lanes", "1", "--timeout-min", "0.001", shell=sh)
        self.assertIn("failed: timed out", out)
        self.assertIsNotNone(sh.procs[0].poll())
        self.assertFalse(dog.finished.is_set(), "needed the watchdog: the coordinator never sent SIGKILL")

    def test_a_start_failure_leaves_no_role_state_behind(self):
        sh = FakeShell(SPRINT, start_error=FileNotFoundError("claude"))
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1", shell=sh)
        state = self.repo / ".git/claude-roles"
        self.assertEqual([p.name for p in state.glob("*") if p.is_file()], [])

    def test_clean_keeps_a_worktree_when_it_cannot_tell_what_it_holds(self):
        sh = FakeShell(SPRINT, no_fetch=True)
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1", shell=sh)
        sh.prs.clear()  # no PR carries the lane's HEAD
        subprocess.run(["git", "update-ref", "-d", "refs/remotes/origin/main"], cwd=self.repo, check=True)
        code, out, _ = self.go("clean", "--apply", shell=sh)
        self.assertIn("keep    implementation-gs-srv-001", out)

    def test_clean_recognises_a_live_lane_through_a_relative_worktrees_path(self):
        sh = FakeShell(SPRINT)
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1", shell=sh)
        work = self.wt("implementation-gs-srv-001")
        sh.prs[next(iter(sh.prs))]["state"] = "MERGED"
        act = self.repo / ".git/claude-coordinator/active"
        act.mkdir(parents=True, exist_ok=True)
        (act / "x.json").write_text(json.dumps({"lane": "x", "role": "implementation", "story": "GS-SRV-001",
                                                "pid": os.getppid(), "worktree": str(work)}))
        code, out, _ = self.go("clean", "--apply", "--worktrees", ".claude/worktrees", shell=sh)
        self.assertNotIn("remove", out)
        self.assertTrue(work.is_dir())

    def test_a_log_that_is_valid_json_but_not_an_object_is_a_failed_lane(self):
        sh = FakeShell(SPRINT, claude_json=["not", "an", "object"])
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", out)
        self.assertIn("no JSON result in the log", out)
        self.assertIn("failed:", out)

    def interrupt_at_poll(self, interval, after=0):
        """Ctrl-C at the coordinator's poll sleep (the `after`+1th), leaving other sleeps alone."""
        real, n = time.sleep, [0]

        def sleep(x):
            if x != interval:
                return real(x)
            n[0] += 1
            if n[0] > after:
                raise KeyboardInterrupt
            real(0.3)
        return mock.patch.object(coord.time, "sleep", side_effect=sleep)

    def group_alive(self, sh):
        try:
            os.killpg(sh.procs[0].pid, 0)
            return True
        except ProcessLookupError:
            return False

    def test_a_timed_out_lane_loses_its_children_too(self):
        sh = FakeShell(SPRINT, orphan_child=True)
        self.guard(sh)
        with mock.patch.object(coord, "KILL_GRACE", 0.3):
            self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                    "--max-lanes", "1", "--timeout-min", "0.001", shell=sh)
        self.assertFalse(self.group_alive(sh))

    def test_an_interrupt_kills_a_lane_that_ignores_sigterm(self):
        sh = FakeShell(SPRINT, stubborn=True)
        self.guard(sh)
        with mock.patch.object(coord, "KILL_GRACE", 0.3), self.interrupt_at_poll(0.07):
            with self.assertRaises(KeyboardInterrupt):
                self.go("run", "--apply", "--roles", "implementation", "--poll", "0.07",
                        "--max-lanes", "1", shell=sh)
        self.assertFalse(self.group_alive(sh))

    def test_an_interrupt_keeps_the_results_of_lanes_that_already_finished(self):
        sh = FakeShell(SPRINT, hang_only="cli")
        with self.interrupt_at_poll(0.07, after=1):
            with self.assertRaises(KeyboardInterrupt):
                self.go("run", "--apply", "--roles", "implementation", "--poll", "0.07",
                        "--max-lanes", "2", shell=sh)
        reports = list((self.repo / ".git/claude-coordinator/runs").glob("*/report.md"))
        self.assertEqual(len(reports), 1)
        text = reports[0].read_text()
        self.assertIn("PR open, Codex clean", text)           # the lane that finished
        self.assertIn("interrupted", text)                    # the one that was cut off

    def test_a_finished_lane_leaves_no_role_state_behind(self):
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1")
        state = self.repo / ".git/claude-roles"
        self.assertEqual([p.name for p in state.glob("*") if p.is_file()], [])

    def test_a_record_that_cannot_be_written_does_not_leave_the_lane_running(self):
        coorddir = self.repo / ".git/claude-coordinator"
        coorddir.mkdir(parents=True, exist_ok=True)
        (coorddir / "active").write_text("in the way")
        sh = FakeShell(SPRINT, hang=True)
        self.guard(sh)
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1, out)
        sh.procs[0].wait(5)
        self.assertIsNotNone(sh.procs[0].poll())

    def test_a_gh_failure_listing_prs_fails_closed(self):
        self.set_status("GS-SRV-001", "in-progress")
        for kw in (dict(gh_fail=True), dict(gh_garbage=True)):
            code, out, sh = self.go("plan", "--roles", "implementation", shell=FakeShell(SPRINT, **kw))
            self.assertEqual(code, 2, out)
            self.assertNotIn("(resume)", out)
            self.assertNotIn("Traceback", out)

    def sre_run(self, **kw):
        sh = FakeShell(SPRINT, make_pr=False, claude_json={"result": "LANE RESULT: nothing to do",
                                                           "is_error": False}, **kw)
        code, out, _ = self.go("run", "--apply", "--roles", "sre", "--poll", "0.05", shell=sh)
        return code, out

    def commit_in(self, sh, cwd, cmd, env):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
                        "commit", "-q", "--allow-empty", "-m", "lane work"], cwd=cwd, check=True)

    def test_nothing_to_do_is_not_ok_when_the_lane_left_commits_or_a_dirty_tree(self):
        code, out = self.sre_run(on_start=self.commit_in)
        self.assertEqual(code, 1, out)
        code, out, _ = self.go("plan", "--roles", "sre", shell=FakeShell(SPRINT))
        self.assertNotIn("nothing changed", out)

    def test_nothing_to_do_is_not_ok_with_uncommitted_files(self):
        code, out = self.sre_run(on_start=lambda sh, cwd, cmd, env: (Path(cwd) / "scratch.txt").write_text("x"))
        self.assertEqual(code, 1, out)

    def test_nothing_to_do_is_not_ok_when_the_pr_lookup_failed(self):
        code, out = self.sre_run(head_gh_fail=True)
        self.assertEqual(code, 1, out)

    def test_only_the_exact_words_nothing_to_do_count(self):
        sh = FakeShell(SPRINT, make_pr=False, claude_json={
            "result": "LANE RESULT: nothing to do except PR #5 needs a fix", "is_error": False})
        code, out, _ = self.go("run", "--apply", "--roles", "sre", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 1, out)

    def test_a_lane_whose_pr_was_closed_unmerged_is_planned_again_on_an_unchanged_board(self):
        sh = FakeShell(SPRINT)
        code, out, _ = self.go("run", "--apply", "--roles", "sre", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 0, out)
        code, out, _ = self.go("plan", "--roles", "sre", shell=sh)
        self.assertIn("nothing changed since SRE's last lane", out)   # its PR is still open
        for pr in sh.prs.values():
            pr["state"] = "CLOSED"
        code, out, _ = self.go("plan", "--roles", "sre", shell=sh)
        self.assertNotIn("nothing changed", out)
        self.assertIn("sre", out.split("Lanes to start:")[1])

    def test_a_lane_whose_pr_merged_is_planned_again_on_an_unchanged_board(self):
        # architecture left a draft for an ADR PR's ruling: the merge is what it was waiting for
        sh = FakeShell(SPRINT)
        code, out, _ = self.go("run", "--apply", "--roles", "sre", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 0, out)
        for pr in sh.prs.values():
            pr["state"] = "MERGED"
        code, out, _ = self.go("plan", "--roles", "sre", shell=sh)
        self.assertNotIn("nothing changed", out)
        self.assertIn("sre", out.split("Lanes to start:")[1])

    def test_architecture_answering_a_question_on_a_story_being_built_counts_as_recorded(self):
        # GS-SRV-001's own lane runs alongside; architecture's answer on it is still architecture's work
        self.gh.comments.setdefault(1, []).append(note("architecture", "implementation", "which codec?"))

        def work(sh_, cwd, cmd, env):
            if env["COORDINATOR_LANE"].startswith("architecture"):
                self.gh.comments[1].append(note("implementation", "architecture", "protobuf, see ADR-007"))
                sh_.prs.pop(f"impl/{env['COORDINATOR_LANE']}")   # board work only, no PR
                sh_.claude_json = {"result": "LANE RESULT: recorded 1", "is_error": False}
            else:
                sh_.claude_json = None
        code, out, sh = self.go("run", "--apply", "--roles", "implementation,architecture", "--poll", "0.05",
                                "--max-lanes", "3", shell=FakeShell(SPRINT, on_start=work))
        lanes = [s["env"]["COORDINATOR_LANE"] for s in sh.started]
        self.assertIn("implementation-gs-srv-001", lanes, out)
        self.assertTrue(any(l.startswith("architecture") for l in lanes), out)
        self.assertNotIn("no sprint story changed", out)
        self.assertEqual(code, 0, out)

    def test_the_saved_signature_is_the_board_after_the_lane_not_before(self):
        # the SRE lane moves a story itself; that must not make the next plan start SRE again
        sh = FakeShell(SPRINT, on_start=lambda sh_, cwd, cmd, env: self.set_status("GS-SRV-003", "review"))
        code, out, _ = self.go("run", "--apply", "--roles", "sre", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 0, out)
        code, out, _ = self.go("plan", "--roles", "sre", shell=sh)
        self.assertIn("nothing changed since SRE's last lane", out)

    def test_a_signal_between_starting_a_lane_and_tracking_it_still_stops_it(self):
        self.addCleanup(signal.signal, signal.SIGINT, signal.signal(signal.SIGINT, signal.default_int_handler))
        sh = FakeShell(SPRINT, hang=True, signal_in_start=True)
        self.guard(sh)
        with self.assertRaises(KeyboardInterrupt):
            self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                    shell=sh)
        self.no_lane_state(sh)

    def sre_pr_run(self):
        sh = FakeShell(SPRINT)
        code, out, _ = self.go("run", "--apply", "--roles", "sre", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 0, out)
        code, out, _ = self.go("plan", "--roles", "sre", shell=sh)
        self.assertIn("nothing changed since SRE's last lane", out)
        return sh

    def test_a_comment_addressed_to_sre_plans_sre_again(self):
        sh = self.sre_pr_run()
        self.gh.comments.setdefault(4, []).append("**For sre:** is the metric right?")
        code, out, _ = self.go("plan", "--roles", "sre", shell=sh)
        self.assertNotIn("nothing changed", out)

    def test_an_edited_story_body_plans_sre_again(self):
        sh = self.sre_pr_run()
        self.gh.issues[4]["body"] = "Contract changed"
        code, out, _ = self.go("plan", "--roles", "sre", shell=sh)
        self.assertNotIn("nothing changed", out)

    def test_a_pr_with_uncommitted_files_in_the_worktree_is_not_ok(self):
        sh = FakeShell(SPRINT, on_start=lambda sh_, cwd, cmd, env: (Path(cwd) / "scratch.txt").write_text("x"))
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1, out)
        self.assertIn("uncommitted", out)
        self.assertNotIn("PR open, Codex clean", out)

    def test_a_pr_is_not_ok_when_the_worktree_state_could_not_be_read(self):
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=FakeShell(SPRINT, status_fail=True))
        self.assertEqual(code, 1, out)
        self.assertNotIn("PR open, Codex clean", out)

    def test_the_final_report_shows_the_sprint_as_the_lane_left_it(self):
        planned = "state:   planned\nnext:    SPRINT-06\n"
        sh = FakeShell(planned, make_pr=False,
                       claude_json={"result": "LANE RESULT: nothing to do", "is_error": False},
                       on_start=lambda sh_, cwd, cmd, env: setattr(sh_, "sprint_out", SPRINT))
        code, out, _ = self.go("run", "--apply", "--roles", "pm", "--poll", "0.05", shell=sh)
        report = out.split("# Run", 1)[1]
        self.assertIn("Sprint: active SPRINT-05", report)

    VD_CHARTER = """---
role: visual-designer
aliases: [design]
branch_prefix: design/
writes: [brands/]
sprint_run: story
start_skill: design-start-sprint
inbox: true
approval: the owner approves the audit and selects every candidate
---
Assist level: L2 — drafts brand docs; the owner decides.
"""

    def add_designer(self):
        (self.repo / ".claude/roles/visual-designer.md").write_text(self.VD_CHARTER)
        self.gh.add("GS-VD-001", "brand doc", Status="ready", Lane="visual-designer", Rank=1,
                    Sprint="SPRINT-05", Component="server")

    def test_the_default_roles_are_the_lane_roles_with_a_charter_here(self):
        code, out, _ = self.go("plan")
        self.assertNotIn("GS-VD-001", out)           # no visual-designer charter: not a lane
        self.add_designer()
        code, out, _ = self.go("plan")
        self.assertIn("visual-designer GS-VD-001", out)

    def test_a_visual_designer_lane_follows_its_own_skill_for_one_story(self):
        self.add_designer()
        code, out, sh = self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05")
        self.assertEqual(code, 0, out)
        prompt = sh.started[0]["cmd"][sh.started[0]["cmd"].index("-p") + 1]
        self.assertIn("`visual-designer` lane", prompt)
        self.assertIn(".claude/skills/design-start-sprint/SKILL.md", prompt)
        self.assertIn("GS-VD-001", prompt)
        self.assertIn("skip its question-answering, Carryover and Backlog selection", prompt)
        self.assertEqual(sh.started[0]["role"], "visual-designer")

    def test_a_finished_designer_lane_lists_what_waits_on_owner(self):
        self.add_designer()
        code, out, _ = self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05")
        self.assertIn("awaits you: the owner approves the audit and selects every candidate", out)

    def test_a_role_without_an_approval_note_adds_no_line(self):
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1")
        self.assertNotIn("awaits you", out)

    def ask_designer(self, text=None):
        self.add_designer()
        self.gh.comments.setdefault(1, []).append(
            text or note("visual-designer", "implementation", "which blue is accent?"))   # on GS-SRV-001

    def answer(self, sh_=None, cwd=None, cmd=None, env=None):
        self.gh.comments[1].append(note("implementation", "visual-designer", "use accent, see visual-language.md#color"))

    def test_a_question_for_the_designer_starts_an_inbox_lane_that_ends_on_answered(self):
        self.ask_designer()
        sh = FakeShell(SPRINT, make_pr=False, claude_json={"result": "LANE RESULT: answered 1",
                                                          "is_error": False}, on_start=self.answer)
        code, out, _ = self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 0, out)
        prompt = sh.started[0]["cmd"][sh.started[0]["cmd"].index("-p") + 1]
        self.assertIn("GS-SRV-001", prompt)
        self.assertIn("do not take carryover or backlog", prompt)
        self.assertIn("visual-designer-inbox", out)
        self.assertIn("answered 1", out)

    def test_an_inbox_lane_is_recorded_as_one_while_it_runs(self):
        self.ask_designer()
        seen = []

        def hook(n):
            if n == 1:
                seen.extend(json.loads(f.read_text()) for f in
                            (self.repo / ".git/claude-coordinator/active").glob("*.json"))
                raise KeyboardInterrupt
        sh = FakeShell(SPRINT, hang=True)
        self.guard(sh)
        with self.poll_sleep(0.05, hook):
            with self.assertRaises(KeyboardInterrupt):
                self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05",
                        "--max-lanes", "1", shell=sh)
        self.assertEqual([(r["lane"].startswith("visual-designer-inbox-"), r["inbox"]) for r in seen],
                         [(True, True)])

    def test_answered_with_the_question_still_open_is_not_ok(self):
        self.ask_designer()
        for n in ("answered 0", "answered 1"):
            sh = FakeShell(SPRINT, make_pr=False, claude_json={"result": f"LANE RESULT: {n}", "is_error": False})
            code, out, _ = self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05",
                                   "--max-lanes", "1", shell=sh)
            self.assertEqual(code, 1, out)
            self.assertIn("still waiting: GS-SRV-001", out)
            # the lane ran but changed nothing; forget that so the next claim is judged afresh
            (self.repo / ".git/claude-coordinator/signatures.json").unlink(missing_ok=True)

    def test_an_inbox_lane_is_not_ok_when_the_questions_cannot_be_re_read(self):
        self.ask_designer()
        real, calls = coord.Ctx.stories, []

        def stories(ctx, sprint):
            calls.append(1)
            if len(calls) > 1:   # the plan read the board; the check after the lane cannot
                raise coord.story.StoryError("board unreachable")
            return real(ctx, sprint)
        sh = FakeShell(SPRINT, make_pr=False, claude_json={"result": "LANE RESULT: answered 1", "is_error": False},
                       on_start=self.answer)
        with mock.patch.object(coord.Ctx, "stories", stories):
            code, out, _ = self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05",
                                   "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1, out)
        self.assertIn("couldn't re-read the questions", out)

    def test_a_question_the_lane_could_not_answer_is_not_retried_until_something_changes(self):
        self.ask_designer()
        blocked = {"result": "LANE RESULT: blocked: it is the owner's call", "is_error": False}

        def inbox_lanes():
            code, out, sh = self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05",
                                    "--max-lanes", "1", shell=FakeShell(SPRINT, make_pr=False, claude_json=blocked))
            return out, [c for c in sh.started if "only the part that answers questions" in c["cmd"][c["cmd"].index("-p") + 1]]
        out, inbox = inbox_lanes()
        self.assertEqual(len(inbox), 1, out)
        out, inbox = inbox_lanes()
        self.assertEqual(inbox, [], out)
        self.assertIn("already tried", out)
        self.gh.comments[1].append(note("visual-designer", "owner", "use #1a73e8"))
        out, inbox = inbox_lanes()
        self.assertEqual(len(inbox), 1, out)

    def test_each_inbox_lane_gets_its_own_worktree(self):
        self.ask_designer()
        commit = lambda sh_, cwd, cmd, env: (self.answer(), self.commit_in(sh_, cwd, cmd, env))  # noqa: E731
        first = FakeShell(SPRINT, claude_json={"result": "LANE RESULT: https://gh/pull/1", "is_error": False},
                          on_start=commit)
        self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05", "--max-lanes", "1", shell=first)
        self.gh.comments[1].append(note("visual-designer", "implementation", "and the font?"))
        second = FakeShell(SPRINT, make_pr=False, claude_json={"result": "LANE RESULT: answered 1",
                                                              "is_error": False}, on_start=self.answer)
        code, out, _ = self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05",
                               "--max-lanes", "1", shell=second)
        self.assertEqual(len(second.started), 1, out)
        self.assertNotEqual(first.started[0]["cwd"], second.started[0]["cwd"])
        self.assertNotIn("uncommitted work or commits", out)

    def test_an_inbox_lane_that_left_files_behind_is_not_ok(self):
        self.ask_designer()
        sh = FakeShell(SPRINT, make_pr=False, claude_json={"result": "LANE RESULT: answered 1",
                                                          "is_error": False},
                       on_start=lambda sh_, cwd, cmd, env: (self.answer(), (Path(cwd) / "x.md").write_text("x")))
        code, out, _ = self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1, out)

    def test_an_inbox_lane_that_says_anything_else_is_not_ok(self):
        self.ask_designer()
        sh = FakeShell(SPRINT, make_pr=False, claude_json={"result": "LANE RESULT: answered some",
                                                          "is_error": False})
        code, out, _ = self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1, out)

    def test_an_unanswered_question_is_planned_and_an_answered_one_is_not(self):
        self.ask_designer()
        code, out, _ = self.go("plan", "--roles", "visual-designer")
        self.assertIn("answer questions on GS-SRV-001", out)
        self.answer()
        code, out, _ = self.go("plan", "--roles", "visual-designer")
        self.assertNotIn("answer questions", out)

    def test_a_running_inbox_lane_shows_in_status_and_blocks_a_second(self):
        self.ask_designer()
        sh = FakeShell(SPRINT, hang=True)
        self.guard(sh)
        act = self.repo / ".git/claude-coordinator/active"
        act.mkdir(parents=True)
        (act / "visual-designer-inbox.json").write_text(json.dumps(
            {"lane": "visual-designer-inbox", "role": "visual-designer", "story": None, "inbox": True,
             "pid": os.getppid(), "worktree": "/x", "started": "t", "run": "r"}))
        code, out, _ = self.go("plan", "--roles", "visual-designer")
        self.assertIn("Running: visual-designer-inbox", out)
        self.assertNotIn("answer questions", out)

    PRODUCT_KEYS = ("sprint_run: session\nstart_skill: product-triage\nwatch_label: product\n"
                    "watch_pr_section: Release changes (after merge)\n"
                    "approval: the owner vetoes or approves the calls listed under Decided this session\n")

    def make_product_a_lane(self):
        path = self.repo / ".claude/roles/product.md"
        path.write_text(path.read_text().replace("\nwrites:", "\n" + self.PRODUCT_KEYS + "writes:", 1))

    def issue(self, n, updated="2026-10-10T00:00:00Z"):
        return {"number": n, "title": f"question {n}", "updatedAt": updated}

    def triage(self, sh):
        for i in sh.issues:
            i["updatedAt"] = "2026-10-10T09:00:00Z"   # a comment or a close moved it

    def run_product(self, **kw):
        sh = FakeShell(SPRINT, make_pr=False, **kw)
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        return code, out, sh

    def test_open_product_issues_start_a_product_lane_that_follows_product_triage(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, make_pr=False, issues=[self.issue(12), self.issue(13)],
                       claude_json={"result": "LANE RESULT: triaged 2", "is_error": False})
        sh.on_start = lambda s_, cwd, cmd, env: self.triage(sh)
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 0, out)
        prompt = sh.started[0]["cmd"][sh.started[0]["cmd"].index("-p") + 1]
        self.assertIn(".claude/skills/product-triage/SKILL.md", prompt)
        self.assertIn("#12", prompt)
        self.assertIn("#13", prompt)
        self.assertIn("`LANE RESULT: triaged <n>`", prompt)
        self.assertEqual(sh.started[0]["role"], "product")

    def test_no_queue_no_product_lane(self):
        self.make_product_a_lane()
        code, out, _ = self.go("plan", "--roles", "product", shell=FakeShell(SPRINT))
        self.assertIn("No lane can start", out)

    def test_a_merged_pr_owing_a_handoff_starts_the_lane_and_one_without_does_not(self):
        self.make_product_a_lane()
        owing = {"number": 7, "url": "https://gh/pull/7", "body": "x\n\n## Release changes (after merge)\n- pm: R1 names no milestone"}
        quiet = {"number": 8, "url": "https://gh/pull/8", "body": "just docs"}
        code, out, _ = self.go("plan", "--roles", "product", shell=FakeShell(SPRINT, merged_prs=[quiet]))
        self.assertIn("No lane can start", out)
        code, out, _ = self.go("plan", "--roles", "product", shell=FakeShell(SPRINT, merged_prs=[quiet, owing]))
        self.assertIn("Lanes to start:\n  product", out)

    def test_triaged_is_not_ok_while_a_queued_issue_is_untouched(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, make_pr=False, issues=[self.issue(12), self.issue(13)],
                       claude_json={"result": "LANE RESULT: triaged 2", "is_error": False})
        sh.on_start = lambda s_, cwd, cmd, env: sh.issues[0].update(updatedAt="2026-10-10T09:00:00Z")
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 1, out)
        self.assertIn("left untouched: #13", out)

    def test_triaged_is_not_ok_when_the_queue_cannot_be_re_read(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, make_pr=False, issues=[self.issue(12)],
                       claude_json={"result": "LANE RESULT: triaged 1", "is_error": False})
        sh.on_start = lambda s_, cwd, cmd, env: setattr(sh, "issues_fail", True)   # fails after the lane
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 1, out)
        self.assertIn("couldn't re-read the queue", out)

    def test_a_closed_issue_counts_as_handled(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, make_pr=False, issues=[self.issue(12)],
                       claude_json={"result": "LANE RESULT: triaged 1", "is_error": False})
        sh.on_start = lambda s_, cwd, cmd, env: sh.issues[0].update(closed=True)
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 0, out)

    def test_triaged_needs_a_clean_worktree(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, make_pr=False, issues=[self.issue(12)],
                       claude_json={"result": "LANE RESULT: triaged 1", "is_error": False})
        sh.on_start = lambda s_, cwd, cmd, env: (self.triage(sh), (Path(cwd) / "docs.md").write_text("x"))
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 1, out)

    def test_an_issue_left_open_on_purpose_is_not_queued_again_until_it_changes(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, make_pr=False, issues=[self.issue(12), self.issue(13)],
                       claude_json={"result": "LANE RESULT: triaged 2", "is_error": False})
        sh.on_start = lambda s_, cwd, cmd, env: self.triage(sh)      # both commented; neither closed
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 0, out)
        sh.issues.append(self.issue(14))                              # a new one arrives
        code, out, _ = self.go("plan", "--roles", "product", shell=sh)
        self.assertIn("Lanes to start:\n  product", out)
        sh.on_start = None
        code, out, sh2 = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        prompt = sh.started[-1]["cmd"][sh.started[-1]["cmd"].index("-p") + 1]
        self.assertIn("#14", prompt)
        self.assertNotIn("#12", prompt)            # already seen, unchanged: not the lane's queue again

    def test_a_handoff_the_lane_never_opened_is_not_done(self):
        self.make_product_a_lane()
        owing = {"number": 7, "url": "https://gh/pull/7", "body": "## Release changes (after merge)\n- pm: x"}
        sh = FakeShell(SPRINT, make_pr=False, merged_prs=[owing],
                       claude_json={"result": "LANE RESULT: nothing to do", "is_error": False})
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 1, out)
        self.assertIn("left untouched: PR#7", out)

    def test_a_handoff_that_has_its_release_change_issue_is_not_queued(self):
        self.make_product_a_lane()
        owing = {"number": 7, "url": "https://gh/pull/7", "body": "## Release changes (after merge)\n- pm: x"}
        sh = FakeShell(SPRINT, merged_prs=[owing], handoffs_opened={7})
        code, out, _ = self.go("plan", "--roles", "product", shell=sh)
        self.assertIn("No lane can start", out)

    def test_the_handoff_search_waits_out_the_search_index(self):
        self.make_product_a_lane()
        owing = {"number": 7, "url": "https://gh/pull/7", "body": "## Release changes (after merge)\n- pm: x"}
        sh = FakeShell(SPRINT, make_pr=False, merged_prs=[owing], handoff_lag=2,
                       claude_json={"result": "LANE RESULT: triaged 1", "is_error": False})
        sh.on_start = lambda s_, cwd, cmd, env: sh.open_handoff(7)
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 0, out)

    def test_an_issue_the_lane_commented_on_and_left_open_stays_in_front_of_owner(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, make_pr=False, issues=[self.issue(12)],
                       claude_json={"result": "LANE RESULT: triaged 1", "is_error": False})
        sh.on_start = lambda s_, cwd, cmd, env: self.triage(sh)       # commented, not closed
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 0, out)
        self.assertIn("#12", out.split("Needs attention")[-1])
        self.assertIn("left open for the owner", out)
        code, out, _ = self.go("plan", "--roles", "product", shell=sh)
        self.assertIn("left open for the owner", out)
        sh.issues[0]["closed"] = True                                  # decided: gone from the list
        code, out, _ = self.go("plan", "--roles", "product", shell=sh)
        self.assertNotIn("#12", out)

    def test_triaged_n_must_match_the_queue(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, make_pr=False, issues=[self.issue(12), self.issue(13)],
                       claude_json={"result": "LANE RESULT: triaged 99", "is_error": False})
        sh.on_start = lambda s_, cwd, cmd, env: self.triage(sh)
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 1, out)
        self.assertIn("said triaged 99, but the queue had 2", out)

    def test_a_crashed_lane_does_not_dequeue_its_queue(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, make_pr=False, issues=[self.issue(12)], exit_code=1,
                       claude_json={"result": "", "is_error": True})
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 1, out)
        code, out, _ = self.go("plan", "--roles", "product", shell=sh)
        self.assertIn("Lanes to start:\n  product", out)

    def test_the_plan_shows_what_a_watch_lane_will_work(self):
        self.make_product_a_lane()
        code, out, _ = self.go("plan", "--roles", "product", shell=FakeShell(SPRINT, issues=[self.issue(12)]))
        self.assertIn("product         triage #12", out)

    def test_more_open_issues_than_gh_returns_fails_closed(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, issues=[self.issue(n) for n in range(100)])
        code, out, _ = self.go("plan", "--roles", "product", shell=sh)
        self.assertIn("100 or more", out)
        self.assertNotIn("Lanes to start:\n  product", out)

    def test_a_clean_watch_or_inbox_worktree_is_removed_when_the_lane_is_done(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, make_pr=False, issues=[self.issue(12)],
                       claude_json={"result": "LANE RESULT: triaged 1", "is_error": False})
        sh.on_start = lambda s_, cwd, cmd, env: self.triage(sh)
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 0, out)
        self.assertEqual(list((self.repo / ".claude/worktrees").glob("product-*")), [])   # its scratch too

    def test_a_watch_worktree_with_commits_is_kept(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, issues=[self.issue(12)],
                       claude_json={"result": "LANE RESULT: https://gh/pull/1", "is_error": False})
        sh.on_start = lambda s_, cwd, cmd, env: (self.triage(sh), self.commit_in(s_, cwd, cmd, env))
        self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(len(list((self.repo / ".claude/worktrees").glob("product-*[!h]"))), 1)   # not .scratch

    def test_the_inbox_prompt_leaves_owners_decisions_to_them(self):
        self.ask_designer()
        sh = FakeShell(SPRINT, make_pr=False, claude_json={"result": "LANE RESULT: answered 1",
                                                          "is_error": False}, on_start=self.answer)
        self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05", "--max-lanes", "1", shell=sh)
        prompt = sh.started[0]["cmd"][sh.started[0]["cmd"].index("-p") + 1]
        self.assertIn("the owner's call", prompt)
        self.assertIn("--to owner", prompt)
        self.assertIn("never decide", prompt)

    def test_inbox_on_a_session_role_is_an_error(self):
        self.make_product_a_lane()
        path = self.repo / ".claude/roles/product.md"
        path.write_text(path.read_text().replace("watch_label: product", "watch_label: product\ninbox: true"))
        code, out, _ = self.go("plan", "--roles", "product")
        self.assertEqual(code, 2, out)
        self.assertIn("inbox", out)

    def test_one_odd_charter_does_not_stop_the_default_plan_but_is_reported(self):
        self.add_designer()
        path = self.repo / ".claude/roles/visual-designer.md"
        path.write_text(path.read_text().replace("sprint_run: story", "sprint_run: stroy"))
        code, out, _ = self.go("plan")
        self.assertEqual(code, 0, out)
        self.assertIn("implementation  GS-SRV-001", out)
        self.assertIn("visual-designer", out.split("Not starting:")[-1])
        self.assertIn("stroy", out)
        code, out, _ = self.go("plan", "--roles", "visual-designer")
        self.assertEqual(code, 2, out)

    def test_a_role_that_opts_out_is_not_a_lane_and_not_a_problem(self):
        self.add_designer()
        path = self.repo / ".claude/roles/visual-designer.md"
        path.write_text(path.read_text().replace("sprint_run: story", "sprint_run: none"))
        code, out, _ = self.go("plan")
        self.assertEqual(code, 0, out)
        self.assertNotIn("stroy", out)
        self.assertNotIn("sprint_run", out)

    def test_a_failed_handoff_search_skips_the_role_not_the_plan(self):
        self.make_product_a_lane()
        owing = {"number": 7, "url": "https://gh/pull/7", "body": "## Release changes (after merge)\n- pm: x"}
        code, out, _ = self.go("plan", "--roles", "product,implementation",
                               shell=FakeShell(SPRINT, merged_prs=[owing], search_fail=True))
        self.assertEqual(code, 0, out)
        self.assertIn("couldn't check the handoffs of PR #7", out)
        self.assertNotIn("Lanes to start:\n  product", out)

    def test_a_handoff_to_two_planners_is_owed_until_both_issues_exist(self):
        self.make_product_a_lane()
        body = "## Release changes (after merge)\n- pm: R1 names no milestone\n- producer: C2 needs a release\n\n## Notes\n- x"
        owing = {"number": 7, "url": "https://gh/pull/7", "body": body}
        for opened, queued in ((0, True), (1, True), (2, False)):
            sh = FakeShell(SPRINT, merged_prs=[owing])
            for _ in range(opened):
                sh.open_handoff(7)
            code, out, _ = self.go("plan", "--roles", "product", shell=sh)
            self.assertEqual("Lanes to start:\n  product" in out, queued, f"{opened}: {out}")

    def test_a_merged_pr_whose_release_changes_say_none_is_not_queued(self):
        self.make_product_a_lane()
        none = {"number": 5, "url": "https://gh/pull/5",
                "body": "## Release changes (after merge)\nNone. Releases are planned in the next PR."}
        routed = {"number": 37, "url": "https://gh/pull/37", "body": "## Release changes (after merge)\n"
                  "- **architecture:** routed through FEAT-02 Dependencies\n- **pm:** cut the privacy story"}
        sh = FakeShell(SPRINT, merged_prs=[none, routed], handoffs_opened={37})
        code, out, _ = self.go("plan", "--roles", "product", shell=sh)
        self.assertIn("No lane can start", out)

    def test_a_missing_handoff_is_reported_as_missing_not_untouched(self):
        self.make_product_a_lane()
        owing = {"number": 7, "url": "https://gh/pull/7", "body": "## Release changes (after merge)\n- pm: x\n- producer: y"}
        sh = FakeShell(SPRINT, make_pr=False, merged_prs=[owing], handoffs_opened={7},
                       claude_json={"result": "LANE RESULT: nothing to do", "is_error": False})
        self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        code, out, _ = self.go("plan", "--roles", "product", shell=sh)
        self.assertIn("PR#7", out)
        self.assertIn("merged; release-change handoff missing (1 of 2)", out)
        self.assertNotIn("left untouched by the last lane", out)

    def test_a_handoff_counts_once_its_issue_exists(self):
        self.make_product_a_lane()
        owing = {"number": 7, "url": "https://gh/pull/7", "body": "## Release changes (after merge)\n- pm: x"}
        sh = FakeShell(SPRINT, make_pr=False, merged_prs=[owing],
                       claude_json={"result": "LANE RESULT: triaged 1", "is_error": False})
        sh.on_start = lambda s_, cwd, cmd, env: sh.open_handoff(7)
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 0, out)

    def test_an_untouched_item_is_reported_again_on_later_plans_not_retried(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, make_pr=False, issues=[self.issue(12)],
                       claude_json={"result": "LANE RESULT: blocked: gh issue comment denied", "is_error": False})
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 1, out)
        code, out, _ = self.go("plan", "--roles", "product", shell=sh)
        self.assertIn("No lane can start", out)
        self.assertIn("#12", out)
        self.assertIn("left untouched", out)
        self.assertIn("waiting on the owner", out)
        sh.issues[0]["updatedAt"] = "2026-10-11T00:00:00Z"
        code, out, _ = self.go("plan", "--roles", "product", shell=sh)
        self.assertIn("Lanes to start:\n  product", out)

    def test_an_earlier_untouched_item_stays_in_front_of_owner_when_a_later_lane_runs(self):
        self.make_product_a_lane()
        blocked = {"result": "LANE RESULT: blocked: gh issue comment denied", "is_error": False}
        sh = FakeShell(SPRINT, make_pr=False, issues=[self.issue(12)], claude_json=blocked)
        self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)      # #12 untouched
        sh.issues.append(self.issue(14))                                                   # a new item
        sh.claude_json = {"result": "LANE RESULT: triaged 1", "is_error": False}
        sh.on_start = lambda s_, cwd, cmd, env: sh.issues[1].update(updatedAt="2026-10-10T09:00:00Z")
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 0, out)
        code, out, _ = self.go("plan", "--roles", "product", shell=sh)
        self.assertIn("#12", out)
        self.assertIn("left untouched", out)

    def test_a_question_that_arrives_during_an_inbox_lane_is_not_marked_tried(self):
        self.ask_designer()                                              # on GS-SRV-001
        def answer_one_and_get_another(sh_, cwd, cmd, env):
            self.answer()
            self.gh.comments.setdefault(2, []).append(note("visual-designer", "implementation", "and the font?"))
        sh = FakeShell(SPRINT, make_pr=False, claude_json={"result": "LANE RESULT: answered 1", "is_error": False},
                       on_start=answer_one_and_get_another)
        code, out, _ = self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 0, out)
        code, out, _ = self.go("plan", "--roles", "visual-designer")
        self.assertIn("answer questions on GS-CLI-001", out)

    def test_a_second_question_on_the_same_story_during_a_lane_is_not_marked_tried(self):
        self.ask_designer()                                              # one question on GS-SRV-001
        def answer_and_ask_again(sh_, cwd, cmd, env):
            self.answer()
            self.gh.comments[1].append(note("visual-designer", "implementation", "and the font?"))
        sh = FakeShell(SPRINT, make_pr=False, claude_json={"result": "LANE RESULT: answered 1", "is_error": False},
                       on_start=answer_and_ask_again)
        self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05", "--max-lanes", "1", shell=sh)
        code, out, _ = self.go("plan", "--roles", "visual-designer")
        self.assertIn("answer questions on GS-SRV-001", out)

    def test_a_new_question_beside_a_tried_one_starts_the_lane(self):
        self.ask_designer()
        blocked = {"result": "LANE RESULT: blocked: the owner's call", "is_error": False}
        self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05", "--max-lanes", "1",
                shell=FakeShell(SPRINT, make_pr=False, claude_json=blocked))
        code, out, _ = self.go("plan", "--roles", "visual-designer")
        self.assertNotIn("answer questions", out)                         # the first is tried
        self.gh.comments.setdefault(2, []).append(note("visual-designer", "implementation", "and the font?"))
        code, out, _ = self.go("plan", "--roles", "visual-designer")
        self.assertIn("answer questions on GS-SRV-001, GS-CLI-001", out)  # a new one: both go to the lane

    def test_a_crashed_inbox_lane_does_not_mark_its_questions_tried(self):
        self.ask_designer()
        sh = FakeShell(SPRINT, make_pr=False, exit_code=1, claude_json={"result": "", "is_error": True})
        self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05", "--max-lanes", "1", shell=sh)
        code, out, _ = self.go("plan", "--roles", "visual-designer")
        self.assertIn("answer questions on GS-SRV-001", out)

    def test_the_lanes_own_comments_do_not_reopen_a_tried_question(self):
        self.ask_designer()
        punt = lambda sh_, cwd, cmd, env: self.gh.comments[1].append(   # noqa: E731
            note("owner", "visual-designer", "your call: palette"))
        sh = FakeShell(SPRINT, make_pr=False, claude_json={"result": "LANE RESULT: blocked: the owner's call",
                                                          "is_error": False}, on_start=punt)
        self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05", "--max-lanes", "1", shell=sh)
        code, out, _ = self.go("plan", "--roles", "visual-designer")
        self.assertNotIn("answer questions", out)
        self.assertIn("already tried", out)

    def test_a_charter_that_makes_sre_a_story_role_gets_no_session_lane(self):
        self.gh.add("GS-SRE-001", "an sre story", Status="ready", Lane="sre", Rank=1, Sprint="SPRINT-05",
                    Component="infra")
        path = self.repo / ".claude/roles/sre.md"
        path.write_text(path.read_text().replace("\nwrites:", "\nsprint_run: story\nstart_skill: sre-start-sprint\nwrites:", 1))
        code, out, _ = self.go("plan", "--roles", "sre")
        lanes = out.split("Lanes to start:")[1].split("Not starting")[0]
        self.assertIn("sre             GS-SRE-001", lanes)
        self.assertNotIn("session", lanes)
        self.assertEqual(lanes.count("sre "), 1)

    def test_a_charter_that_makes_pm_a_story_role_gets_no_session_lane(self):
        path = self.repo / ".claude/roles/pm.md"
        path.write_text(path.read_text().replace("\nwrites:", "\nsprint_run: story\nstart_skill: pm-start-sprint\nwrites:", 1))
        code, out, _ = self.go("plan", "--roles", "pm", shell=FakeShell("state:   closed\nnext:    SPRINT-06\n"))
        self.assertNotIn("session", out)

    def test_a_role_without_a_start_skill_is_refused_before_any_lane_setup(self):
        self.add_designer()
        path = self.repo / ".claude/roles/visual-designer.md"
        path.write_text(path.read_text().replace("start_skill: design-start-sprint\n", ""))
        code, out, sh = self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05")
        self.assertEqual(code, 2, out)
        self.assertIn("start_skill", out)
        self.assertFalse((self.repo / ".claude/worktrees").exists())
        self.assertEqual(sh.started, [])

    def test_a_watching_role_whose_queue_cannot_be_read_is_skipped_not_the_whole_plan(self):
        self.make_product_a_lane()
        code, out, _ = self.go("plan", "--roles", "product,implementation",
                               shell=FakeShell(SPRINT, issues_fail=True))
        self.assertEqual(code, 0, out)
        self.assertIn("implementation  GS-SRV-001", out)
        self.assertNotIn("Lanes to start:\n  product", out)
        self.assertIn("couldn't list the `product` issues", out)

    def test_each_watch_lane_gets_its_own_worktree(self):
        self.make_product_a_lane()
        first = FakeShell(SPRINT, issues=[self.issue(12)],
                          claude_json={"result": "LANE RESULT: https://gh/pull/1", "is_error": False})
        first.on_start = lambda s_, cwd, cmd, env: (self.triage(first), self.commit_in(s_, cwd, cmd, env))
        self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=first)
        second = FakeShell(SPRINT, make_pr=False, issues=[self.issue(15)],
                           claude_json={"result": "LANE RESULT: triaged 1", "is_error": False})
        second.on_start = lambda s_, cwd, cmd, env: self.triage(second)
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=second)
        self.assertEqual(len(second.started), 1, out)
        self.assertNotIn("uncommitted work or commits", out)

    def test_a_product_pr_that_is_open_lists_what_waits_on_owner(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, issues=[self.issue(12)],
                       claude_json={"result": "LANE RESULT: https://gh/pull/1", "is_error": False})
        sh.on_start = lambda s_, cwd, cmd, env: self.triage(sh)
        code, out, _ = self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        self.assertEqual(code, 0, out)
        self.assertIn("awaits you: the owner vetoes or approves the calls listed under Decided this session", out)

    def test_a_role_name_alias_is_resolved_before_planning(self):
        self.add_designer()
        code, out, _ = self.go("plan", "--roles", "design")
        self.assertEqual(code, 0, out)
        self.assertIn("visual-designer GS-VD-001", out)

    def test_a_mistyped_sprint_run_is_an_error_not_a_silent_skip(self):
        self.add_designer()
        path = self.repo / ".claude/roles/visual-designer.md"
        path.write_text(path.read_text().replace("sprint_run: story", "sprint_run: stroy"))
        code, out, _ = self.go("plan", "--roles", "visual-designer")
        self.assertEqual(code, 2, out)
        self.assertIn("sprint_run", out)
        self.assertIn("stroy", out)

    def test_a_session_role_without_a_trigger_is_an_error(self):
        self.add_designer()
        path = self.repo / ".claude/roles/visual-designer.md"
        path.write_text(path.read_text().replace("sprint_run: story", "sprint_run: session")
                        .replace("inbox: true\n", ""))
        code, out, _ = self.go("plan", "--roles", "visual-designer")
        self.assertEqual(code, 2, out)
        self.assertIn("trigger", out)

    def test_a_repo_where_no_role_runs_as_a_lane_says_so(self):
        for r in ("implementation", "sre", "pm", "product", "architecture"):
            (self.repo / f".claude/roles/{r}.md").unlink(missing_ok=True)
        code, out, _ = self.go("plan")
        self.assertEqual(code, 2, out)
        self.assertIn("runs as a lane", out)

    def allowed(self, sh):
        cmd = sh.started[0]["cmd"]
        return cmd[cmd.index("--allowedTools") + 1].split(",")

    def test_lanes_get_a_narrow_allow_list_and_the_deny_list_still_applies(self):
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                "--max-lanes", "1")
        allow = self.allowed(sh)
        for want in ("Edit", "Write", "Bash(git commit:*)", "Bash(git push:*)", "Bash(gh pr create:*)",
                     "Bash(.claude/bin/story:*)", "Bash(.claude/bin/codex-review:*)", "Bash(make check:*)",
                     "Bash(echo:*)", "Bash(gh run list:*)", "Bash(gh run view:*)"):
            self.assertIn(want, allow)
        prompt = sh.started[0]["cmd"][sh.started[0]["cmd"].index("-p") + 1]
        self.assertIn("One command per Bash call", prompt)     # a chain is denied whole for one unlisted part
        self.assertIn("never redirected to a file", prompt)
        for never in ("Bash(gh pr merge:*)", "Bash(gh:*)", "Bash(git:*)", "Bash(*)", "Bash",
                      "Bash(gh pr close:*)", "Bash(gh pr review:*)", "Bash(git reset:*)", "Bash(rm:*)",
                      "Bash(git branch:*)", "Bash(gh issue close:*)", "Bash(gh search issues:*)"):
            self.assertNotIn(never, allow)
        deny = sh.started[0]["cmd"][sh.started[0]["cmd"].index("--disallowedTools") + 1].split(",")
        self.assertTrue(set(deny) >= {"Bash(gh pr merge:*)", "Bash(git push --force:*)"})
        self.assertFalse(set(deny) & set(allow))

    def test_the_allow_list_names_exact_forms_not_whole_commands(self):
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                "--max-lanes", "1")
        allow = self.allowed(sh)
        for want in ("Bash(.claude/bin/role require:*)", "Bash(.claude/bin/role show:*)",
                     "Bash(.claude/bin/role owners:*)", "Bash(.claude/bin/role check-cmd:*)",
                     "Bash(.claude/bin/role reviewed:*)", "Bash(git fetch origin:*)", "Bash(git fetch)",
                     "Bash(git push:*)", "Bash(git checkout:*)", "Bash(grep:*)", "Bash(head:*)", "Bash(tail:*)"):
            self.assertIn(want, allow)   # a prefix rule fails `git push -q -u origin x`; the hook judges flags
        for never in ("Bash(.claude/bin/role:*)", "Bash(git fetch:*)", "Bash(gh pr edit:*)", "Bash(sed:*)",
                      "Bash(cat:*)", "Bash(find:*)", "Bash(rm:*)",
                      "Bash(.claude/bin/coordinator:*)", "Bash(git config:*)", "Bash(git remote:*)",
                      "Bash(git worktree:*)", "Bash(git branch:*)"):
            self.assertNotIn(never, allow)

    def test_a_lane_is_told_its_session_id_and_given_a_scratch_directory(self):
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                "--max-lanes", "1")
        st = sh.started[0]
        cmd, env, wt = st["cmd"], st["env"], Path(st["cwd"])
        sid, prompt = cmd[cmd.index("--session-id") + 1], cmd[cmd.index("-p") + 1]
        scratch = Path(env["COORDINATOR_SCRATCH"])
        self.assertEqual(scratch, wt.with_name(wt.name + ".scratch"))
        self.assertTrue(scratch.is_dir())
        self.assertFalse(scratch.is_relative_to(wt))                      # nothing there is committed
        self.assertIn(f"Your session id is {sid}", prompt)               # a skill's ${CLAUDE_SESSION_ID}
        self.assertIn(f"--body-file {scratch}/pr-body.md", prompt)
        self.assertIn("git switch -c <branch>", prompt)
        self.assertEqual(cmd[cmd.index("--add-dir") + 1], str(scratch))

    def test_clean_removes_a_scratch_directory_with_its_worktree(self):
        sh = FakeShell(SPRINT, make_pr=False)
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1", shell=sh)
        wt = Path(sh.started[0]["cwd"])
        scratch = wt.with_name(wt.name + ".scratch")
        (scratch / "pr-body.md").write_text("x")
        run_git = lambda *a: subprocess.run(["git", *a], cwd=wt, capture_output=True)  # noqa: E731
        run_git("switch", "-q", "--detach", "refs/remotes/origin/main")
        code, out, _ = self.go("clean", "--apply", "--same-os-user", shell=FakeShell(SPRINT))
        self.assertFalse(wt.exists(), out)
        self.assertFalse(scratch.exists(), out)

    def test_the_lane_users_agento11y_doctor_is_asked_and_its_failures_said(self):
        text = ("agento11y doctor v0.48.0\n\n✓ Conversations (generation export)\n  probe: HTTP 202\n\n"
                "✗ Analytics (OTLP metrics & traces)\n  metrics probe: HTTP 401\n"
                "  ! the OTLP endpoint rejected the metrics and traces exports: HTTP 401\n\n"
                "✓ Config\n\n✗ 1 problem(s): analytics misconfigured\n")
        self.assertEqual(coord.doctor_problems(text), [
            "Analytics (OTLP metrics & traces): the OTLP endpoint rejected the metrics and traces exports: HTTP 401"])
        self.assertEqual(coord.doctor_problems("✓ all fine\n"), [])

    def test_a_lanes_role_is_pinned_while_it_runs_and_released_after(self):
        seen = {}

        def look(sh_, cwd, cmd, env):
            sid = cmd[cmd.index("--session-id") + 1]
            seen["pin"] = (self.repo / ".git/claude-roles/.pinned" / sid).is_file()
            seen["sid"] = sid
        sh = FakeShell(SPRINT, on_start=look)
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 0, out)
        self.assertTrue(seen["pin"])
        self.assertFalse((self.repo / ".git/claude-roles/.pinned" / seen["sid"]).exists())
        self.assertFalse((self.repo / ".git/claude-roles" / seen["sid"]).exists())

    def test_a_lane_that_changed_its_own_role_is_failed(self):
        def reroll(sh_, cwd, cmd, env):
            sid = cmd[cmd.index("--session-id") + 1]
            coord.role.put_role(self.repo, sid, "none", pinned=True)   # whatever route it found
        sh = FakeShell(SPRINT, on_start=reroll)
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        self.assertEqual(code, 1, out)
        self.assertIn("changed its own role", out)

    def test_a_lane_gets_an_allowlisted_environment_not_the_coordinators(self):
        with mock.patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "s3cret", "DATABASE_URL": "x",
                                          "GH_TOKEN": "t", "ANTHROPIC_API_KEY": "k", "LC_ALL": "C",
                                          "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CONFIG_DIR": "/c",
                                          "MY_EXTRA": "e", "COORDINATOR_ENV_PASS": "MY_EXTRA"}):
            code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                    "--max-lanes", "1")
        env = sh.started[0]["env"]
        for kept in ("PATH", "HOME", "ANTHROPIC_API_KEY", "LC_ALL", "CLAUDE_CODE_USE_BEDROCK",
                     "CLAUDE_CONFIG_DIR", "MY_EXTRA", "COORDINATOR_LANE", "COORDINATOR_RUN"):
            self.assertIn(kept, env, kept)
        for dropped in ("AWS_SECRET_ACCESS_KEY", "DATABASE_URL", "COORDINATOR_ENV_PASS",
                        "GH_TOKEN", "COORDINATOR_APP_CONFIG"):   # yours: the lane gets the app's
            self.assertNotIn(dropped, env, dropped)
        self.assertEqual(env["GIT_EDITOR"], "true")   # `git rebase --continue` never waits on an editor
        self.assertEqual(env["PATH"].split(os.pathsep)[1], str(self.repo / "bin"))   # lane_path: your tools
        self.app_cfg.unlink()
        with mock.patch.dict(os.environ, {"GH_TOKEN": "t"}):     # without an app, your gh auth carries over
            code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                    "--max-lanes", "1", "--person-token", "--unprotected-main")
        self.assertEqual(sh.started[0]["env"]["GH_TOKEN"], "t", out)

    def test_lane_path_names_plain_directories_of_the_repo(self):
        md = self.repo / ".claude/roles/_repo.md"
        for bad in ("/usr/local/bin", "../other/bin", "bin:/tmp"):
            md.write_text(re.sub(r"lane_path: .*", f"lane_path: [{bad}]", md.read_text()))
            code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                    "--max-lanes", "1")
            self.assertEqual(sh.started, [], bad)
            self.assertIn("lane_path entry", out, bad)

    def test_lanes_get_their_hooks_from_outside_the_worktree(self):
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1")
        cmd, wt = sh.started[0]["cmd"], Path(sh.started[0]["cwd"]).resolve()
        settings = Path(cmd[cmd.index("--settings") + 1])
        self.assertTrue(settings.is_file(), settings)
        self.assertTrue(settings.resolve().is_relative_to((self.repo / ".git/claude-coordinator/runs").resolve()))
        hooks = json.loads(settings.read_text())["hooks"]["PreToolUse"]
        matchers = {h["matcher"] for h in hooks}
        self.assertEqual(matchers, {"Bash", "Edit|Write|MultiEdit|NotebookEdit|Read|NotebookRead|Glob|Grep|LS"})
        snap = settings.parent / "roles"
        self.assertEqual(sorted(x.name for x in snap.iterdir()),
                         sorted(x.name for x in (self.repo / ".claude/roles").iterdir()))
        self.assertFalse(snap.resolve().is_relative_to(wt))
        for h in hooks:
            for c in h["hooks"]:
                script = Path(c["command"].split('"')[1])
                self.assertTrue(script.is_absolute() and script.is_file(), c["command"])
                self.assertFalse(script.resolve().is_relative_to(wt), script)   # a checkout the lane can move
                self.assertEqual(script.read_bytes(), (self.repo / ".claude/bin/role").read_bytes())
                self.assertIn(c["command"].split('"')[2].strip(), ("hook-pre-bash", "hook-pre-tool-use"))
        self.assertIn("EnterWorktree", coord.DENY)
        self.assertIn("ExitWorktree", coord.DENY)
        self.assertIn("Bash(git push origin HEAD:refs/heads/main:*)", coord.DENY)
        self.assertIn("Bash(git push origin refs/heads/main:*)", coord.DENY)

    def test_env_pass_cannot_carry_git_or_session_variables(self):
        for bad in ("GIT_DIR", "GIT_SSH_COMMAND", "CLAUDE_SESSION_ID", "CLAUDE_PROJECT_DIR", "COORDINATOR_LANE",
                    "GH_CONFIG_DIR", "LD_PRELOAD", "BASH_ENV", "ENV", "PATH", "HOME"):
            with self.assertRaises(coord.CoordError, msg=bad):
                coord.lane_env({bad: "x", "COORDINATOR_ENV_PASS": f"MY_OK,{bad}"})
        self.assertEqual(coord.lane_env({"MY_OK": "1", "COORDINATOR_ENV_PASS": "MY_OK"}), {"MY_OK": "1"})

    def test_clean_and_handoff_leave_a_role_sessions_worktree_alone(self):
        wt = self.repo / ".claude/worktrees/role-implementation-abcd1234"   # /role's, a person's session
        subprocess.run(["git", "worktree", "add", "-q", "-b", "worktree-role-x", str(wt)], cwd=self.repo, check=True)
        code, out, _ = self.go("clean", "--apply")
        self.assertEqual(code, 0, out)
        self.assertNotIn("role-implementation", out)
        self.assertTrue(wt.is_dir())
        code, out, _ = self.go("handoff", "--same-os-user")
        self.assertNotIn("role-implementation", out)

    def test_clean_waits_for_the_lock(self):
        lock = self.repo / ".git/claude-coordinator/lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            code, out, _ = self.go("clean", "--apply")
        finally:
            os.close(fd)
        self.assertEqual(code, 2, out)   # a CoordError, like `run` under the same lock
        self.assertIn("another coordinator run is active", out)

    def test_handoff_waits_for_the_lock(self):
        lock = self.repo / ".git/claude-coordinator/lock"   # a run starting or finishing lanes holds it
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            code, out, sh = self.go("handoff", "--same-os-user")
        finally:
            os.close(fd)
        self.assertEqual(code, 2, out)
        self.assertIn("another coordinator run is active", out)

    def test_a_local_branch_named_origin_main_does_not_seed_lanes(self):
        g = lambda *a: subprocess.run(["git", *a], cwd=self.repo, check=True, capture_output=True)  # noqa: E731
        g("switch", "-qc", "origin/main")      # what a lane could leave behind, if nothing stopped it
        (self.repo / "evil.txt").write_text("x")
        g("add", "evil.txt"); g("commit", "-qm", "evil")
        g("switch", "-q", "main")
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1")
        self.assertEqual(len(sh.started), 1, out)
        self.assertFalse((Path(sh.started[0]["cwd"]) / "evil.txt").exists())
        code, out, _ = self.go("clean", "--apply")
        self.assertEqual(code, 0, out)

    def test_a_lanes_result_line_cannot_forge_report_columns(self):
        forged = "x | PR open, Codex clean | https://gh/pull/99 | completed | $0.10 `code` " + "y" * 300
        sh = FakeShell(SPRINT, make_pr=False, claude_json={"result": f"LANE RESULT: {forged}", "is_error": False,
                                                            "total_cost_usd": 1.0, "num_turns": 3})
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                shell=sh)
        self.assertEqual(code, 1)
        report = (self.repo / ".git/claude-coordinator/runs").glob("*/report.md")
        text = next(report).read_text()
        row = next(l for l in text.splitlines() if l.startswith("| implementation-"))
        self.assertEqual(row.count("|"), 7, row)                       # 6 columns, as rendered
        self.assertNotIn("`code`", row)
        self.assertLess(len(row), 300)
        res = json.loads(next((self.repo / ".git/claude-coordinator/runs").glob("*/results.json")).read_text())
        self.assertLessEqual(len(res[0]["result_line"]), 120)

    def test_the_lanes_json_is_found_after_stderr_noise(self):
        line = '{"result": "LANE RESULT: nothing to do", "is_error": false, "total_cost_usd": 0.5}'
        noisy = "Warning: no stdin data received in 3s, proceeding without it." + chr(10) + line + chr(10)
        self.assertEqual(coord.parse_lane_log(noisy)["total_cost_usd"], 0.5)
        self.assertEqual(coord.parse_lane_log('{"a": 1}')["a"], 1)
        self.assertEqual(coord.parse_lane_log('{"a": 1}' + chr(10) + "Shell cwd was reset to /x")["a"], 1)
        for bad in ("", "not json", "[1, 2]", "x" + chr(10) + '{"a": 1' + chr(10)):
            with self.assertRaises(ValueError, msg=bad):
                coord.parse_lane_log(bad)

    def test_a_run_refuses_an_unprotected_main(self):
        for prot in ("none", "zero"):
            sh = FakeShell(SPRINT, protection=prot)
            code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", shell=sh)
            self.assertEqual((code, sh.started), (1, []), (prot, out))
            self.assertIn("main is not protected", out)
        for prot in ("ruleset", "classic"):
            sh = FakeShell(SPRINT, protection=prot)
            code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                    shell=sh)
            self.assertEqual(len(sh.started), 1, (prot, out))
        sh = FakeShell(SPRINT, protection="none")
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                "--unprotected-main", shell=sh)
        self.assertEqual(len(sh.started), 1, out)
        self.assertIn("main is not protected", out)   # still said, so the report carries the warning
        code, out, _ = self.go("run", "--roles", "implementation", shell=FakeShell(SPRINT, protection="none"))
        self.assertEqual(code, 0, out)                 # a plan or dry run doesn't need it

    def run1(self, *extra, **kw):
        sh = FakeShell(SPRINT, **kw)
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                *extra, shell=sh)
        return code, out, sh

    def test_lanes_run_as_their_own_app(self):
        seen = {}
        fake_gh = Path(self.tmp.name) / "real-gh"
        fake_gh.write_text('#!/bin/sh\necho "$GH_TOKEN $*"\n')
        fake_gh.chmod(0o755)

        def probe(sh, cwd, cmd, env):   # what the lane's own gh and git see while it runs
            helper = lane_git_config(env, "credential.https://github.com.helper")[-1]
            seen["git"] = subprocess.run([helper, "get"], input="protocol=https\nhost=github.com\n\n",
                                         capture_output=True, text=True, env=env).stdout
            seen["gh"] = subprocess.run(["gh", "pr", "view"], capture_output=True, text=True, env=env, cwd=cwd).stdout
            seen["store"] = subprocess.run([helper, "store"], input="password=x\n", capture_output=True,
                                           text=True, env=env).stdout
            seen["insteadof"] = lane_git_config(env, "url.https://github.com/.insteadof")
            seen["helper"] = lane_git_config(env, "credential.https://github.com.helper")
            seen["cfgmode"] = Path(env["GIT_CONFIG_VALUE_0"]).stat().st_mode & 0o777
            tok = Path(helper).parent.parent / "token"
            seen["mode"] = tok.stat().st_mode & 0o777
            seen["tok"] = tok
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "yours", "GH_TOKEN": "yours-too"}), \
                mock.patch.object(coord, "real_gh", lambda: str(fake_gh)):
            code, out, sh = self.run1(on_start=probe)
        self.assertEqual(len(sh.started), 1, out)
        env = sh.started[0]["env"]
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        auth = Path(env["PATH"].split(os.pathsep)[0]).parent
        self.assertEqual(auth.parent.parent.name, "runs")
        self.assertEqual(Path(env["GH_CONFIG_DIR"]), auth / "gh-config")
        self.assertEqual((env["GIT_CONFIG_COUNT"], env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]),
                         ("1", "include.path", str(auth / "gitconfig")))
        self.assertEqual(seen["cfgmode"], 0o600)
        # a tool that drops empty variables (buf, running git) still gets every entry: none is empty
        self.assertFalse([k for k, v in env.items() if k.startswith("GIT_CONFIG") and not v])
        self.assertEqual(seen["insteadof"], ["git@github.com:", "ssh://git@github.com/"])
        self.assertEqual(seen["helper"], ["", str(auth / "bin/git-credential-lane")])
        self.assertEqual(seen["git"], "username=x-access-token\npassword=ghs_1\n")
        self.assertEqual(seen["gh"], "ghs_1 pr view\n")
        self.assertEqual((seen["store"], seen["mode"]), ("", 0o600))
        self.assertFalse(seen["tok"].exists())                 # gone with the run
        self.assertNotIn("ghs_", json.dumps(env))
        self.assertNotIn("lane-app", json.dumps(env))           # nor the key's whereabouts
        self.assertIn("lanes run as valesordev-lanes[bot]", out)
        method, path, _, body = self.app.calls[-1]
        self.assertEqual(body, {"repositories": ["game-server"], "permissions": coord.LANE_APP_PERMS})

    def test_the_apps_login_is_signed_with_its_key(self):
        key = app_key()
        jwt = coord.app_jwt({"app_id": 123, "key": key})
        head, body, sig = jwt.split(".")
        claims = jwt_claims(jwt)
        self.assertEqual(claims["iss"], "123")
        self.assertLessEqual(claims["exp"] - claims["iat"], 600)    # GitHub's ceiling
        d = Path(self.tmp.name)
        pub = d / "pub.pem"
        subprocess.run(["openssl", "rsa", "-in", str(key), "-pubout", "-out", str(pub)], check=True, capture_output=True)
        (d / "sig").write_bytes(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)))
        r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(pub), "-signature", str(d / "sig")],
                           input=f"{head}.{body}".encode(), capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with self.assertRaises(coord.CoordError):
            coord.app_jwt({"app_id": 123, "key": d / "missing.pem"})

    def test_the_lanes_token_is_renewed_before_it_expires(self):
        self.app.life = 200        # under RENEW_BEFORE from the start: renewed on the run's first look
        code, out, sh = self.run1()
        self.assertEqual(len(sh.started), 1, out)
        self.assertGreaterEqual(self.app.minted, 2, out)
        t = coord.LaneToken({"app_id": 123, "key": app_key()}, 42, "example/game-server", {}, "x[bot]")
        t.path = Path(self.tmp.name) / "token"
        self.app.life = 3600
        t.mint()
        self.assertEqual(t.path.read_text(), f"ghs_{self.app.minted}\n")
        n = self.app.minted
        t.renew()                  # an hour left: kept
        self.assertEqual(self.app.minted, n)
        t.expires = time.time() + 100
        t.renew()
        self.assertEqual((self.app.minted, t.path.read_text()), (n + 1, f"ghs_{n + 1}\n"))
        self.app.life, self.app.fail_mint = 200, False
        code, out, sh = self.run1(on_start=lambda *_: setattr(self.app, "fail_mint", True))
        self.assertEqual(len(sh.started), 1, out)
        self.assertIn("couldn't renew the lanes' token", out)   # said; the lane keeps the old one

    def test_a_run_refuses_lanes_that_would_act_as_you(self):
        self.app_cfg.unlink()
        code, out, sh = self.run1()
        self.assertEqual((code, sh.started), (1, []), out)
        self.assertIn("lanes need their own GitHub App", out)
        with mock.patch.dict(os.environ, {"GH_TOKEN": "yours"}):
            code, out, sh = self.run1("--person-token")       # your own call, said out loud
        self.assertEqual(len(sh.started), 1, out)
        self.assertIn("WARNING", out)
        self.assertEqual(sh.started[0]["env"]["GH_TOKEN"], "yours")   # as you, as the flag says
        self.assertNotIn("GH_CONFIG_DIR", sh.started[0]["env"])

    def test_a_run_refuses_an_app_it_cant_trust(self):
        key = self.app_cfg.parent / "lane-app.pem"
        key.chmod(0o640)
        code, out, sh = self.run1()
        self.assertEqual((code, sh.started), (1, []), out)
        self.assertIn("readable by others", out)
        key.chmod(0o600)
        self.app_cfg.write_text(json.dumps({"app_id": "abc"}))
        code, out, sh = self.run1()
        self.assertEqual((code, sh.started), (1, []), out)
        self.assertIn("numeric app_id", out)
        other = Path(self.tmp.name) / "elsewhere.pem"
        shutil.copyfile(key, other)
        other.chmod(0o600)
        key.unlink()
        self.app_cfg.write_text(json.dumps({"app_id": 123}))
        code, out, sh = self.run1()
        self.assertEqual((code, sh.started), (1, []), out)
        self.assertIn("doesn't exist", out)
        self.app_cfg.write_text(json.dumps({"app_id": 123, "private_key": str(other)}))
        code, out, sh = self.run1()
        self.assertEqual(len(sh.started), 1, out)
        for perms, why in (({**coord.LANE_APP_PERMS, "administration": "write"}, "administration: write"),
                           ({**coord.LANE_APP_PERMS, "workflows": "write"}, "workflows: write"),
                           ({**coord.LANE_APP_PERMS, "statuses": "write"}, "statuses: write"),
                           ({"contents": "admin"}, "contents: admin"),
                           ({"contents": "sideways"}, "contents: sideways")):
            self.app = FakeApp(perms=perms)
            code, out, sh = self.run1()
            self.assertEqual((code, sh.started), (1, []), (perms, out))
            self.assertIn(why, out)
            self.assertEqual(self.app.minted, 0)
        self.app = FakeApp(perms={"contents": "write", "metadata": "read"})   # less is fine
        code, out, sh = self.run1()
        self.assertEqual(len(sh.started), 1, out)
        self.assertEqual(self.app.calls[-1][3]["permissions"], {"contents": "write", "metadata": "read"})
        self.app = FakeApp(installed=False)
        code, out, sh = self.run1()
        self.assertEqual((code, sh.started), (1, []), out)
        self.assertIn("isn't installed on example/game-server", out)
        self.app = FakeApp(fail_mint=True)
        code, out, sh = self.run1()
        self.assertEqual((code, sh.started), (1, []), out)
        self.assertIn("couldn't get valesordev-lanes[bot] a token", out)

    def lane_user(self, uid=None, runs_mode=0o2750, **lu):
        """A lane OS user `lanes-test` whose home is under the test's tmp: run --apply uses it."""
        home = Path(self.tmp.name) / "lanes-home"
        (home / "repos").mkdir(parents=True, exist_ok=True)
        (home / "runs").mkdir(exist_ok=True)
        (home / "runs").chmod(runs_mode)
        cfg = Path(self.tmp.name) / "lane-user.json"
        cfg.write_text(json.dumps({"user": "lanes-test"}))
        pw = mock.Mock(pw_uid=os.getuid() + 1 if uid is None else uid, pw_gid=os.getgid(), pw_dir=str(home))
        for p in (mock.patch.dict(os.environ, {"COORDINATOR_LANE_USER_CONFIG": str(cfg)}),
                  mock.patch.object(coord.pwd, "getpwnam",
                                    lambda n: pw if n == "lanes-test" else (_ for _ in ()).throw(KeyError(n)))):
            p.start()
            self.addCleanup(p.stop)
        self.same_user = False
        return home, {"home": home, **lu}

    def test_lanes_run_as_their_own_os_user(self):
        home, lu = self.lane_user()
        seen = {}

        def probe(sh, cwd, cmd, env):   # the lane's files while it runs
            auth = Path(env["GIT_CONFIG_VALUE_0"]).parent
            seen.update(token=oct((auth / "token").stat().st_mode & 0o777),
                        bin=oct((auth / "bin").stat().st_mode & 0o777),
                        envfiles=sorted(f.name for f in auth.parent.glob("env-*.json")),
                        envmode=oct(next(auth.parent.glob("env-*.json")).stat().st_mode & 0o777),
                        lane_dir=auth.parent, dir_mode=oct(auth.parent.stat().st_mode & 0o7777))
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/home/you/.claude", "SSH_AUTH_SOCK": "/run/you/ssh",
                                          "XDG_CONFIG_HOME": "/home/you/.config", "TMPDIR": "/home/you/tmp"}):
            code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                    shell=FakeShell(SPRINT, lane_user=lu, on_start=probe))
        self.assertEqual(len(sh.started), 1, out)
        self.assertIn("lanes run as OS user lanes-test", out)
        st = sh.started[0]
        clone = home / "repos" / "example--game-server"
        self.assertEqual(Path(st["cwd"]).parent, home / "repos" / "example--game-server.worktrees")
        self.assertEqual(st["role"], "implementation")          # pinned in the clone, by the lane user
        self.assertTrue((clone / ".git").is_dir())
        self.assertFalse((self.repo / ".claude/worktrees").exists())   # nothing in your checkout
        env = st["env"]
        self.assertEqual((env["HOME"], env["USER"], env["LOGNAME"]), (str(home), "lanes-test", "lanes-test"))
        for yours in ("CLAUDE_CONFIG_DIR", "SSH_AUTH_SOCK", "XDG_CONFIG_HOME", "TMPDIR", "GH_CONFIG_DIR", "GH_TOKEN"):
            self.assertNotIn(yours, env)
        self.assertEqual(env["PATH"], f"{seen['lane_dir'] / 'auth/bin'}:{clone / 'bin'}:/lanes/bin:/usr/bin")
        #                                                    the clone's bootstrapped tools (`lane_path: [bin/]`)
        self.assertEqual(seen["lane_dir"].parent, home / "runs")
        self.assertEqual((seen["dir_mode"], seen["token"], seen["bin"], seen["envmode"]),
                         ("0o2750", "0o640", "0o750", "0o640"))
        self.assertEqual(len(seen["envfiles"]), 1)
        self.assertEqual(list(seen["lane_dir"].glob("env-*.json")), [])     # gone with the run
        self.assertFalse((seen["lane_dir"] / "auth/token").exists())
        lane_cmds = [c[c.index("--") + 1:] for c in sh.sudo_cmds if "--" in c]
        self.assertTrue(all(c[:5] == ["sudo", "-n", "-u", "lanes-test", "--"] for c in sh.sudo_cmds if c[2] != "-i"))
        clone_cmd = next(c for c in lane_cmds if "clone" in c)
        self.assertIn("credential.https://github.com.helper=", clone_cmd)        # as the app, never you
        ctl = [c[3:] for c in lane_cmds if c[:2] == ["python3", "-I"] and c[3] in ("put-role", "get-role")]
        self.assertEqual([c[0] for c in ctl], ["put-role", "get-role", "put-role"])
        trusted = [c[4:] for c in lane_cmds if c[:2] == ["python3", "-I"] and c[3] == "trust"]
        self.assertEqual(trusted, [[str(clone), str(home / ".claude.json")]])          # once, before any lane, for the clone
        self.assertEqual(ctl[0][3:], ["implementation", "pinned"])
        self.assertEqual(ctl[2][3], "-")                         # released when the lane ended
        self.assertTrue(any(c[0] == "git" and c[1] == "-C" and "status" in c for c in lane_cmds))   # inspected as it
        code, out, sh = self.go("clean", shell=FakeShell(SPRINT, lane_user=lu))
        self.assertIn("implementation-gs-srv-001", out)          # the lane user's worktree, found and judged
        self.assertTrue(all(c[:4] == ["sudo", "-n", "-u", "lanes-test"] for c in sh.sudo_cmds))

    def test_a_lane_user_is_stopped_as_that_user(self):
        home, lu = self.lane_user()
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                "--timeout-min", "0.003", shell=FakeShell(SPRINT, lane_user=lu, hang=True))
        self.assertIn("timed out", out)
        kills = [c[5:] for c in sh.sudo_cmds if c[5:6] == ["kill"]]
        self.assertTrue(kills and kills[0][:3] == ["kill", "-s", "TERM"], sh.sudo_cmds[-3:])
        self.assertEqual(kills[0][3:], ["--", f"-{sh.procs[0].pid}"])
        self.assertIsNotNone(sh.procs[0].poll())

    @staticmethod
    def commit_workflow(sh, cwd, cmd, env, path=".github/workflows/check.yml"):
        f = Path(cwd) / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("name: check\n")
        c = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
        subprocess.run(["git", "add", path], cwd=cwd, check=True)
        subprocess.run([*c, "commit", "-q", "-m", "ci: check"], cwd=cwd, check=True)

    def branch_head(self, ref, where=None):
        r = subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=where or self.repo,
                           capture_output=True, text=True)
        return r.stdout.strip() or None

    def test_a_lane_users_workflow_change_is_brought_into_your_checkout(self):
        home, lu = self.lane_user()
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                shell=FakeShell(SPRINT, lane_user=lu, make_pr=False, on_start=self.commit_workflow))
        self.assertEqual(code, 1, out)
        wt = Path(sh.started[0]["cwd"])
        branch = "impl/implementation-gs-srv-001"
        self.assertEqual(self.branch_head(f"refs/heads/{branch}"), self.branch_head("HEAD", wt))
        self.assertIn(f"{branch} changes .github/workflows/check.yml, which only you push: it's in your checkout", out)
        self.assertIn(f"`git push -u origin {branch}` and `gh pr create --head {branch} --label role:implementation`", out)
        lane_cmds = [c[c.index("--") + 1:] for c in sh.sudo_cmds if "--" in c]
        self.assertTrue(any("bundle" in c for c in lane_cmds))            # written by the lane user
        self.assertEqual(list((home / "repos").glob("*.handoff/*.bundle")), [])   # and gone once fetched

    def test_your_own_branch_of_that_name_is_never_overwritten(self):
        home, lu = self.lane_user()
        subprocess.run(["git", "branch", "impl/implementation-gs-srv-001"], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", "commit",
                        "-q", "--allow-empty", "-m", "mine"], cwd=self.repo, check=True)
        subprocess.run(["git", "branch", "-f", "impl/implementation-gs-srv-001", "HEAD"], cwd=self.repo, check=True)
        mine = self.branch_head("refs/heads/impl/implementation-gs-srv-001")
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                shell=FakeShell(SPRINT, lane_user=lu, make_pr=False, on_start=self.commit_workflow))
        self.assertIn("still only in the lane's worktree (couldn't fetch it into your checkout", out)
        self.assertEqual(self.branch_head("refs/heads/impl/implementation-gs-srv-001"), mine)
        self.assertNotIn("git push -u origin", out)

    def test_a_branch_without_a_workflow_change_is_not_handed_off(self):
        def commit_code(sh, cwd, cmd, env):
            self.commit_workflow(sh, cwd, cmd, env, path="src/x.go")
        home, lu = self.lane_user()
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                shell=FakeShell(SPRINT, lane_user=lu, make_pr=False, on_start=commit_code))
        self.assertNotIn("only you push", out)
        self.assertIsNone(self.branch_head("refs/heads/impl/implementation-gs-srv-001"))

    def test_handoff_brings_over_a_workflow_branch_an_earlier_run_left(self):
        home, lu = self.lane_user()
        sh = FakeShell(SPRINT, lane_user=lu, make_pr=False, on_start=self.commit_workflow)
        with mock.patch.object(coord, "hand_off", lambda *a: None):   # an older coordinator: left in the lane's clone
            self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1", shell=sh)
        self.assertIsNone(self.branch_head("refs/heads/impl/implementation-gs-srv-001"))
        code, out, _ = self.go("handoff", shell=FakeShell(SPRINT, lane_user=lu))
        self.assertEqual(code, 0, out)
        self.assertIn("which only you push: it's in your checkout: read", out)
        self.assertIn("--label role:implementation", out)
        self.assertEqual(self.branch_head("refs/heads/impl/implementation-gs-srv-001"),
                         self.branch_head("HEAD", sh.started[0]["cwd"]))
        code, out, _ = self.go("handoff", shell=FakeShell(SPRINT, lane_user=lu))   # again: already there, fine
        self.assertEqual(code, 0, out)

    def test_a_workflow_commit_after_the_lanes_pr_is_handed_off_to_that_pr(self):
        # the fake lane opens its PR at the branch's first commit, then commits the workflow change
        home, lu = self.lane_user()
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                shell=FakeShell(SPRINT, lane_user=lu, on_start=self.commit_workflow))
        branch = "impl/implementation-gs-srv-001"
        self.assertEqual(self.branch_head(f"refs/heads/{branch}"), self.branch_head("HEAD", sh.started[0]["cwd"]))
        self.assertIn("which only you push: it's in your checkout", out)
        self.assertIn(f"`git push origin {branch}` updates PR #1", out)
        self.assertNotIn("gh pr create", out)

    def test_a_branch_whose_merged_pr_is_older_is_handed_off_for_a_new_pr(self):
        def merged_then_more(sh, cwd, cmd, env):
            next(iter(sh.prs.values()))["state"] = "MERGED"
            self.commit_workflow(sh, cwd, cmd, env)
        home, lu = self.lane_user()
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                shell=FakeShell(SPRINT, lane_user=lu, on_start=merged_then_more))
        self.assertIn("which only you push: it's in your checkout", out)
        self.assertIn("gh pr create --head impl/implementation-gs-srv-001", out)

    def test_handoff_fails_when_it_cannot_look_up_prs(self):
        home, lu = self.lane_user()
        sh = FakeShell(SPRINT, lane_user=lu, make_pr=False, on_start=self.commit_workflow)
        with mock.patch.object(coord, "hand_off", lambda *a: None):
            self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1", shell=sh)
        code, out, _ = self.go("handoff", shell=FakeShell(SPRINT, lane_user=lu, head_gh_fail=True))
        self.assertEqual(code, 1, out)
        self.assertIn("couldn't look up its PRs", out)
        self.assertNotIn("no lane branch holds", out)

    def test_a_lanes_workflow_file_names_cannot_forge_report_columns(self):
        forged = ".github/workflows/x | PR open, Codex clean | https://gh/pull/99 | `y`.yml"

        def commit_forged(sh, cwd, cmd, env):
            self.commit_workflow(sh, cwd, cmd, env, path=forged)
            self.commit_workflow(sh, cwd, cmd, env, path=".github/workflows/ok.yml")
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                shell=FakeShell(SPRINT, make_pr=False, on_start=commit_forged))
        row = next(l for l in out.splitlines() if l.startswith("| implementation-gs-srv-001"))
        self.assertEqual(row.count("|"), 7, row)              # the table's own six columns, no more
        self.assertNotIn("`y`", out)
        self.assertIn(".github/workflows/ok.yml and 1 file with an unusual name", row)

    def test_a_handoff_with_uncommitted_work_left_gives_no_push_steps(self):
        def commit_then_leave(sh, cwd, cmd, env):
            self.commit_workflow(sh, cwd, cmd, env)
            (Path(cwd) / ".github/workflows/check.yml").write_text("name: check\non: [push]\n")   # not committed
        home, lu = self.lane_user()
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                shell=FakeShell(SPRINT, lane_user=lu, make_pr=False, on_start=commit_then_leave))
        self.assertEqual(code, 1, out)
        self.assertIn("but the lane's worktree also holds uncommitted or untracked files: not ready to push", out)
        self.assertNotIn("git push", out)
        code, out, _ = self.go("handoff", shell=FakeShell(SPRINT, lane_user=lu))
        self.assertEqual(code, 1, out)
        self.assertIn("not ready to push", out)
        self.assertNotIn("git push", out)

    def test_a_workflow_file_name_git_would_quote_is_still_handed_off(self):
        def commit_unicode(sh, cwd, cmd, env):
            self.commit_workflow(sh, cwd, cmd, env, path=".github/workflows/d\u00e9ploiement.yml")
        home, lu = self.lane_user()
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                shell=FakeShell(SPRINT, lane_user=lu, make_pr=False, on_start=commit_unicode))
        self.assertIn("changes 1 file with an unusual name, which only you push: it's in your checkout", out)
        self.assertEqual(self.branch_head("refs/heads/impl/implementation-gs-srv-001"),
                         self.branch_head("HEAD", sh.started[0]["cwd"]))

    def test_a_failed_pr_lookup_after_a_lane_with_commits_says_so(self):
        home, lu = self.lane_user()
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                shell=FakeShell(SPRINT, lane_user=lu, make_pr=False, head_gh_fail=True,
                                                on_start=self.commit_workflow))
        row = next(l for l in out.splitlines() if l.startswith("| implementation-gs-srv-001"))
        self.assertIn("couldn't look up the branch's PR", row)
        self.assertIn("`coordinator handoff`", row)
        self.assertNotIn("no PR:", row)

    def test_codex_out_of_quota_is_said_and_asked_again_next_run(self):
        sh = FakeShell(SPRINT, codex="limited")
        code, out, _ = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                               "--max-lanes", "1", shell=sh)
        row = next(l for l in out.splitlines() if l.startswith("| implementation-gs-srv-001"))
        self.assertIn("Codex is out of review quota", row)
        self.assertIn(["1", "--body", "@codex review"], sh.pr_comments)   # a limit is asked again, once

    def test_a_same_user_lanes_workflow_change_is_reported_with_the_push_steps(self):
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                shell=FakeShell(SPRINT, make_pr=False, on_start=self.commit_workflow))
        self.assertIn("which only you push: it's in your checkout", out)
        self.assertIn("git push -u origin impl/implementation-gs-srv-001", out)

    def test_a_run_refuses_lanes_that_share_your_os_user(self):
        self.same_user = False
        code, out, sh = self.run1()
        self.assertEqual((code, sh.started), (1, []), out)
        self.assertIn("lanes run as your OS user", out)
        code, out, sh = self.run1("--same-os-user")           # your call, said out loud
        self.assertEqual(len(sh.started), 1, out)
        self.assertIn("WARNING: lanes run as your OS user", out)

    def test_a_run_refuses_a_lane_user_that_isnt_apart(self):
        for kw, lu, why in (({}, {"sudo_ok": False}, "needs a password"),
                            ({}, {"can_sudo": True}, "can use sudo"),
                            ({}, {"reads_outside": True}, "can read"),
                            ({}, {"no_write": True}, "can't write"),
                            ({}, {"no_read_runs": True}, "can't read"),
                            ({"runs_mode": 0o2770}, {}, "must be yours"),
                            ({"runs_mode": 0o2755}, {}, "must be yours"),
                            ({"uid": os.getuid()}, {}, "root or you"),
                            ({"uid": 0}, {}, "root or you"),
                            ({"taken": "pid 12364 (tini)"}, {}, "already in use by pid 12364 (tini)"),
                            ({"taken": "pid 842049 (keyboxd)"}, {}, "gpgconf --kill all")):
            self.uid_taken = kw.pop("taken", None)
            home, base = self.lane_user(**kw)
            sh = FakeShell(SPRINT, lane_user={**base, **lu})
            code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                    shell=sh)
            self.assertEqual((code, sh.started), (1, []), (why, out))
            self.assertIn(why, out)
            self.assertIn("--same-os-user", out)
        self.uid_taken = None
        home, lu = self.lane_user()
        real = os.getuid()
        with mock.patch.object(coord.os, "getuid", lambda: real + 5):   # runs/ belongs to someone else
            code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                    shell=FakeShell(SPRINT, lane_user=lu))
        self.assertEqual((code, sh.started), (1, []), out)
        self.assertIn("must be yours", out)
        real_stat = os.stat
        with mock.patch.object(coord.os, "stat", lambda p, *a, **k: (_ for _ in ()).throw(PermissionError(p))
                               if str(p).endswith("lanes-home/runs") else real_stat(p, *a, **k)):
            code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                    shell=FakeShell(SPRINT, lane_user=lu))
        self.assertEqual((code, sh.started), (1, []), out)
        self.assertIn("log in again", out)
        self.app_cfg.unlink()                                   # the lane user has no GitHub login but the app's
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--person-token",
                                shell=FakeShell(SPRINT, lane_user=lu))
        self.assertEqual((code, sh.started), (1, []), out)
        self.assertIn("need the lane app", out)

    def test_a_lane_users_clone_must_be_of_this_repo(self):
        home, lu = self.lane_user()
        other = Path(self.tmp.name) / "other.git"
        subprocess.run(["git", "init", "-q", "--bare", str(other)], check=True)
        subprocess.run(["git", "clone", "-q", str(other), str(home / "repos/example--game-server")],
                       check=True, capture_output=True)
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                shell=FakeShell(SPRINT, lane_user=lu))
        self.assertEqual((code, sh.started), (1, []), out)
        self.assertIn("move it aside", out)
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--worktrees", "/tmp/x",
                                shell=FakeShell(SPRINT, lane_user=lu))
        self.assertEqual((code, sh.started), (1, []), out)
        self.assertIn("--worktrees is for", out)

    def test_a_uid_is_in_use_only_by_what_you_didnt_start(self):
        stub = mock.patch.object(coord, "strays", REAL_STRAYS)
        stub.start()
        self.addCleanup(stub.stop)
        proc = Path(self.tmp.name) / "proc"
        me = os.getuid()

        def ps(pid, name, uid, ppid):
            (proc / str(pid)).mkdir(parents=True)
            (proc / str(pid) / "status").write_text(f"Name:\t{name}\nPPid:\t{ppid}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
        ps(100, "zsh", me, 1)
        ps(200, "sudo", me, 100)          # sudo: real UID yours, effective root
        ps(201, "python3", 61001, 200)    # lanectl -> claude: a lane
        ps(202, "claude", 61001, 201)
        ps(300, "containerd-shim", 0, 1)
        self.assertIsNone(REAL_UID_IN_USE(61001, proc))
        ps(301, "tini", 61001, 300)       # a container process with the same UID
        self.assertEqual(REAL_UID_IN_USE(61001, proc), "pid 301 (tini)")
        ps(400, "sudo", 4242, 1)          # someone else's sudo doesn't vouch for anything
        ps(401, "sh", 61002, 400)
        self.assertEqual(REAL_UID_IN_USE(61002, proc), "pid 401 (sh)")
        self.assertIsNone(REAL_UID_IN_USE(61003, proc))

    def test_the_lane_users_clone_is_trusted_once(self):
        d = Path(self.tmp.name) / "ctl"
        d.mkdir()
        (d / "lanectl").write_text(coord.LANECTL)
        home = Path(self.tmp.name) / "trusthome"
        home.mkdir()
        (home / ".claude.json").write_text(json.dumps({"projects": {"/x": {"allowedTools": []}}, "other": 1}))
        run = lambda: subprocess.run([sys.executable, "-I", str(d / "lanectl"), "trust", "/var/lib/lanes/repos/r",  # noqa: E731
                                      str(home / ".claude.json")], check=True)
        run()
        data = json.loads((home / ".claude.json").read_text())
        self.assertIs(data["projects"]["/var/lib/lanes/repos/r"]["hasTrustDialogAccepted"], True)
        self.assertEqual((data["other"], data["projects"]["/x"]), (1, {"allowedTools": []}))   # the rest kept
        self.assertEqual((home / ".claude.json").stat().st_mode & 0o777, 0o600)
        before = (home / ".claude.json").stat().st_mtime_ns
        run()
        self.assertEqual((home / ".claude.json").stat().st_mtime_ns, before)   # already trusted: untouched

    def test_codex_is_asked_as_you_for_a_lanes_pr(self):
        code, out, sh = self.run1(codex="none")         # the app's PR: Codex never reviews it unasked
        self.assertEqual(sh.pr_comments, [["1", "--body", "@codex review"]], out)   # once per head commit
        code, out, sh = self.run1(codex="completed")
        self.assertEqual(sh.pr_comments, [], out)        # already reviewed: nothing to ask
        self.app_cfg.unlink()
        code, out, sh = self.run1("--person-token", codex="none")
        self.assertEqual(sh.pr_comments, [], out)        # your own PR: Codex reviews it on its own

    def test_you_are_asked_to_review_a_lanes_pr(self):
        code, out, sh = self.run1()
        self.assertEqual(self.app.review_requests,
                         [("repos/example/game-server/pulls/1/requested_reviewers",
                           {"reviewers": ["solo-seven"]})], out)        # once, by the app, of you
        self.app = FakeApp(refuse_review=True)
        code, out, sh = self.run1()
        self.assertEqual(len(self.app.review_requests), 1, out)       # one try, however many polls
        self.assertEqual(out.count("WARNING: implementation-gs-srv-001's PR: GitHub answered 422"), 1, out)
        self.assertEqual(len(sh.started), 1, out)
        self.app = FakeApp()
        self.app_cfg.unlink()
        code, out, sh = self.run1("--person-token")
        self.assertEqual(self.app.review_requests, [], out)            # your own PR: you can't be asked

    def test_a_group_you_cant_signal_is_still_alive(self):
        proc = mock.Mock(pid=4242)
        with mock.patch.object(coord.os, "killpg", side_effect=PermissionError):
            self.assertTrue(coord.group_alive(proc))     # the lane user's processes: alive until it says so
        with mock.patch.object(coord.os, "killpg", side_effect=ProcessLookupError):
            self.assertFalse(coord.group_alive(proc))

    def test_lanes_carry_agento11y_tags_but_no_credentials(self):
        self.o11y_cfg.write_text("# Managed by `agento11y login`.\nAGENTO11Y_LOCAL_FORWARD=true\n"
                                 "AGENTO11Y_AUTH_TOKEN=tok-cfg\nexport AGENTO11Y_CONTENT_CAPTURE_MODE=\"metadata_only\"\n"
                                 "# AGENTO11Y_COMMENTED=1\nOTEL_EXPORTER_OTLP_HEADERS=\"Authorization=Basic hdr-cfg\"\n")
        secrets = {"AGENTO11Y_AUTH_TOKEN": "tok-env", "SIGIL_AUTH_TOKEN": "tok-sigil", "AGENTO11Y_OTEL_AUTH_TOKEN": "tok-otel",
                   "AGENTO11Y_AUTH_TENANT_ID": "tenant-999", "OTEL_EXPORTER_OTLP_HEADERS": "hdr-env"}
        with mock.patch.dict(os.environ, {**secrets, "AGENTO11Y_LOCAL": "true",
                                          "AGENTO11Y_TAGS": "project=automate.x,team=valesordev,agent_role=coordinator"}):
            code, out, sh = self.run1()
        env = sh.started[0]["env"]
        self.assertEqual(env["AGENTO11Y_TAGS"],
                         "project=automate.x,team=valesordev,agent_role=implementation,launcher=coordinator")
        self.assertEqual((env["AGENTO11Y_LOCAL"], env["AGENTO11Y_LOCAL_FORWARD"], env["AGENTO11Y_CONTENT_CAPTURE_MODE"]),
                         ("true", "true", "metadata_only"))
        self.assertNotIn("AGENTO11Y_COMMENTED", env)
        dump = json.dumps(env)
        for v in [*secrets.values(), "tok-cfg", "hdr-cfg"]:
            self.assertNotIn(v, dump)
        self.o11y_cfg.unlink()
        code, out, sh = self.run1()     # no .envrc tags (cron): from github_repo
        self.assertEqual(sh.started[0]["env"]["AGENTO11Y_TAGS"],
                         "project=game-server,team=example,agent_role=implementation,launcher=coordinator")

    def test_a_lane_user_reports_to_grafana_with_its_own_login(self):
        home, lu = self.lane_user()
        argv = ("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1")
        code, out, sh = self.go(*argv, shell=FakeShell(SPRINT, lane_user=lu))
        self.assertNotIn("WARNING", out)                 # you don't use agento11y: nothing to say
        self.o11y_cfg.write_text("AGENTO11Y_LOCAL=true\nAGENTO11Y_LOCAL_FORWARD=true\n"
                                 "AGENTO11Y_ENDPOINT=http://127.0.0.1:8765\nAGENTO11Y_STACK_URL=https://x.grafana.net\n"
                                 "AGENTO11Y_OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:8765/otlp\n"
                                 "AGENTO11Y_CONTENT_CAPTURE_MODE=metadata_only\n")
        code, out, sh = self.go(*argv, shell=FakeShell(SPRINT, lane_user=lu))
        self.assertNotIn("WARNING", out)
        env = sh.started[0]["env"]
        for routing in ("AGENTO11Y_LOCAL", "AGENTO11Y_LOCAL_FORWARD", "AGENTO11Y_ENDPOINT", "AGENTO11Y_STACK_URL",
                        "AGENTO11Y_OTEL_EXPORTER_OTLP_ENDPOINT"):
            self.assertNotIn(routing, env)               # where its captures go is the lane user's own config
        self.assertEqual(env["AGENTO11Y_CONTENT_CAPTURE_MODE"], "metadata_only")
        self.assertIn("launcher=coordinator", env["AGENTO11Y_TAGS"])
        for kw, why in (({"o11y": False}, "has no agento11y on its PATH"),
                        ({"o11y_plugin": False}, "agento11y claude install"),
                        ({"o11y_token": False}, "no Grafana token of its own"),
                        ({"o11y_local": True}, "is in local mode")):
            code, out, sh = self.go(*argv, shell=FakeShell(SPRINT, lane_user={**lu, **kw}))
            self.assertIn(why, out)
            self.assertEqual(len(sh.started), 1, out)   # said, never a reason to refuse

    def test_what_lanes_leave_running_is_stopped(self):
        home, lu = self.lane_user()
        argv = ("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1")
        code, out, sh = self.go(*argv, shell=FakeShell(SPRINT, lane_user=lu))
        self.assertFalse([c for c in sh.sudo_cmds if c[5:7] == ["kill", "-TERM"]])
        self.left = [("157043", "agento11y"), ("157050", "node")]     # appeared during the run
        self.uid_taken = None                                          # (the preflight saw none)
        code, out, sh = self.go(*argv, shell=FakeShell(SPRINT, lane_user=lu))
        self.assertIn(["sudo", "-n", "-u", "lanes-test", "--", "kill", "-TERM", "157043", "157050"], sh.sudo_cmds)
        self.assertIn("stopped what the lanes left running: agento11y (157043), node (157050)", out)
        self.same_user = True
        code, out, sh = self.go(*argv)                                 # lanes as you: never
        self.assertNotIn("stopped what", out)

    def test_lanectl_records_roles_and_starts_lanes_as_written(self):
        d = Path(self.tmp.name) / "ctl"
        d.mkdir()
        (d / "lanectl").write_text(coord.LANECTL)
        shutil.copyfile(BIN / "role", d / "role")
        ctl = [sys.executable, "-I", str(d / "lanectl")]
        sid = "11111111-2222-3333-4444-555555555555"
        subprocess.run(ctl + ["put-role", str(self.repo), sid, "implementation", "pinned"], check=True)
        r = subprocess.run(ctl + ["get-role", str(self.repo), sid], capture_output=True, text=True, check=True)
        self.assertEqual(r.stdout.strip(), "implementation")
        self.assertTrue(coord.role.pin_file(self.repo, sid).exists())
        subprocess.run(ctl + ["put-role", str(self.repo), sid, "-", "-"], check=True)
        self.assertIsNone(coord.role.get_role(self.repo, sid))
        (d / "env.json").write_text(json.dumps({"PATH": "/usr/bin:/bin", "ONLY": "this"}))
        r = subprocess.run(ctl + ["exec", str(d / "env.json"), str(self.repo), "--", "sh", "-c", 'echo "$ONLY $HOME $(pwd)"'],
                           capture_output=True, text=True, env={**os.environ, "HOME": "/leak"})
        self.assertEqual(r.stdout.strip(), f"this  {self.repo}")       # nothing of the caller's environment

    def test_a_run_refuses_main_a_lane_could_merge_without_you(self):
        for kw, why in (({"last_push": False}, "latest push"),
                        ({"bypass": [{"actor_id": 4, "actor_type": "RepositoryRole", "bypass_mode": "always"}]}, "bypass"),
                        ({"bypass": [{"actor_id": 1, "actor_type": "Integration", "bypass_mode": "pull_request"}]}, "bypass"),
                        ({"bypass": [{"actor_id": None, "actor_type": "OrganizationAdmin", "bypass_mode": "always"}]}, "bypass")):
            code, out, sh = self.run1(**kw)
            self.assertEqual((code, sh.started), (1, []), (kw, out))
            self.assertIn(why, out, kw)
        for kw in ({"bypass": []}, {"bypass": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}]},
                   {"protection": "classic"}):
            code, out, sh = self.run1(**kw)
            self.assertEqual(len(sh.started), 1, (kw, out))
        code, out, sh = self.run1(protection="classic", last_push=False)
        self.assertEqual((code, sh.started), (1, []), out)

    def test_codex_threads_answered_by_reply_need_a_look(self):
        sh = FakeShell(SPRINT, handled=2)
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1",
                                shell=sh)
        self.assertEqual(code, 0, out)                 # ok: the PR is open, labelled, at HEAD, Codex has nothing open
        self.assertIn("2 Codex threads answered by the lane", out)
        res = json.loads(next((self.repo / ".git/claude-coordinator/runs").glob("*/results.json")).read_text())
        self.assertEqual(res[0]["codex"]["handled"], 2)

    def test_no_bare_push_and_no_search_in_the_allow_list(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, make_pr=False, issues=[self.issue(12)])
        self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        allow = self.allowed(sh)
        self.assertNotIn("Bash(git push)", allow)
        self.assertNotIn("Bash(gh search issues:*)", allow)

    def test_every_bash_entry_names_a_command_and_a_verb(self):
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                "--max-lanes", "1")
        readers = {"grep", "head", "tail", "wc", "ls", "diff",   # whole commands: the hook judges their files
                   "echo"}                                     # (echo has none; its redirections are judged)
        for e in self.allowed(sh):
            if e.startswith("Bash("):
                body = e[5:-1].removesuffix(":*")
                self.assertTrue(body.startswith(".claude/bin/") or " " in body or body in readers, e)

    def test_a_watching_role_also_gets_issue_commands(self):
        self.make_product_a_lane()
        sh = FakeShell(SPRINT, make_pr=False, issues=[self.issue(12)])
        self.go("run", "--apply", "--roles", "product", "--poll", "0.05", shell=sh)
        allow = self.allowed(sh)
        for want in ("Bash(gh issue comment:*)", "Bash(gh issue close:*)", "Bash(gh issue create:*)"):
            self.assertIn(want, allow)

    def test_a_charter_can_add_to_a_lanes_allow_list(self):
        self.add_designer()
        path = self.repo / ".claude/roles/visual-designer.md"
        path.write_text(path.read_text().replace("inbox: true\n",
                        "inbox: true\nlane_allow: [Bash(make build-brand-valesordev:*), Bash(npm run build:*)]\n"))
        code, out, sh = self.go("run", "--apply", "--roles", "visual-designer", "--poll", "0.05",
                                "--max-lanes", "1")
        self.assertIn("Bash(make build-brand-valesordev:*)", self.allowed(sh))
        self.assertIn("Bash(npm run build:*)", self.allowed(sh))

    def test_a_charter_cannot_widen_it_past_a_command_and_verb(self):
        self.add_designer()
        path = self.repo / ".claude/roles/visual-designer.md"
        for bad in ("Bash(*)", "Bash(gh:*)", "Bash(git:*)", "Bash(make:*)", "Bash", "Read(*)x", "WebFetch",
                    "Bash(rm -rf:*)", "Bash(curl -s:*)", "Bash(bash -c:*)", "Bash(sudo make:*)",
                    "Bash(git push -f:*)", "Bash(git push origin main:*)", "Bash(gh pr merge:*)",
                    "Bash(cat /etc/passwd:*)", "Bash(find . -delete:*)", "Bash(npx foo:*)",
                    "Bash(/usr/bin/curl x:*)", "Bash(make build; rm -rf /:*)", "Bash(make a|b:*)",
                    "Bash(make $(x):*)", "Bash(make `x`:*)", "Bash(make a&&b:*)", "Bash(make a > f:*)",
                    "Bash(.claude/bin/../../x:*)", "Bash(.claude/bin/role:*)", "Bash(sed -i x:*)",
                    "Bash(.claude/bin/role show:*)", "Bash(.claude/bin/story list:*)",
                    "Bash(timeout 5 bash:*)", "Bash(nice sh:*)", "Bash(command sh:*)", "Bash(busybox sh:*)",
                    "Bash(python3.12 -c:*)", "Bash(pypy3 x:*)", "Bash(nodejs x:*)", "Bash(deno run:*)",
                    "Bash(bun run:*)", "Bash(lua x:*)", "Bash(npm exec:*)", "Bash(go test -exec:*)",
                    "Bash(make -f x:*)", "Bash(./.claude/bin/role set:*)", "Bash(.CLAUDE/bin/x y:*)",
                    "Bash(script -c:*)", "Bash(watch -x:*)", "Bash(rg --pre:*)", "Bash(tar -x:*)",
                    "Bash(/usr/bin/make build:*)", "Bash(./make test:*)", "Bash(npm x foo:*)",
                    "Bash(yarn dlx foo:*)"):
            path.write_text(self.VD_CHARTER.replace("inbox: true\n", f"inbox: true\nlane_allow: [{bad}]\n"))
            code, out, sh = self.go("plan", "--roles", "visual-designer")
            self.assertEqual(code, 2, f"{bad}: {out}")
            self.assertIn("lane_allow", out)

    def test_no_lane_allow_leaves_a_lane_with_only_the_repos_settings(self):
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                "--max-lanes", "1", "--no-lane-allow")
        self.assertNotIn("--allowedTools", sh.started[0]["cmd"])
        self.assertIn("--disallowedTools", sh.started[0]["cmd"])

    def test_a_fetch_that_fails_starts_no_lane(self):
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                "--max-lanes", "1", shell=FakeShell(SPRINT, fetch_fail=True))
        self.assertEqual((code, len(sh.started)), (1, 0), out)
        self.assertIn("git fetch origin failed", out)

    def test_a_worktree_deleted_by_hand_is_recreated(self):
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1")
        shutil.rmtree(self.wt("implementation-gs-srv-001"))
        code, out, sh = self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                                "--max-lanes", "1")
        self.assertEqual((code, len(sh.started)), (0, 1), out)

    def test_status_and_plan_do_not_delete_records_but_a_run_does(self):
        act = self.repo / ".git/claude-coordinator/active"
        act.mkdir(parents=True)
        dead = subprocess.Popen(["true"])
        dead.wait()
        for name, rec in (("dead", {"lane": "dead", "role": "implementation", "story": "X-1", "pid": dead.pid,
                                    "worktree": "/nope", "started": "t", "run": "r"}),
                          ("zero", {"lane": "zero", "pid": 0, "worktree": "/nope"}),
                          ("junk", ["not", "a", "record"])):
            (act / f"{name}.json").write_text(json.dumps(rec))
        code, out, _ = self.go("status")
        self.assertIn("no lanes running", out)
        self.go("plan", "--roles", "implementation")
        self.assertEqual(len(list(act.glob("*.json"))), 3)
        self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05", "--max-lanes", "1")
        self.assertEqual(list(act.glob("*.json")), [])

    def test_a_shell_call_that_hangs_is_cut_off(self):
        with mock.patch.object(coord, "RUN_TIMEOUT", 0.3):
            r = coord.Shell().run(["sleep", "5"])
        self.assertEqual(r.returncode, 124)

    def poll_sleep(self, interval, hook):
        """time.sleep that runs `hook(n)` on the nth sleep of exactly `interval`, passing the rest."""
        real, n = time.sleep, [0]

        def sleep(x):
            if x != interval:
                return real(x)
            n[0] += 1
            hook(n[0])
            real(0.02)
        return mock.patch.object(coord.time, "sleep", side_effect=sleep)

    def no_lane_state(self, sh):
        for p in sh.procs:
            self.assertFalse(self.alive(p), "a lane is still running")
        self.assertFalse(list((self.repo / ".git/claude-coordinator/active").glob("*.json")))
        self.assertEqual([p.name for p in (self.repo / ".git/claude-roles").glob("*") if p.is_file()], [])

    def alive(self, proc):
        try:
            os.killpg(proc.pid, 0)
            return True
        except ProcessLookupError:
            return False

    def test_an_interrupt_while_a_lane_is_being_finished_still_cleans_up_and_reports(self):
        sh = FakeShell(SPRINT, stubborn=True)
        self.guard(sh)

        def hook(n):
            if n == 1:      # the first 0.05 s sleep is inside terminate(), finishing the timed-out lane
                raise KeyboardInterrupt
        with mock.patch.object(coord, "KILL_GRACE", 0.3), self.poll_sleep(0.05, hook):
            with self.assertRaises(KeyboardInterrupt):
                self.go("run", "--apply", "--roles", "implementation", "--poll", "0.07",
                        "--max-lanes", "1", "--timeout-min", "0.001", shell=sh)
        self.no_lane_state(sh)
        report = next((self.repo / ".git/claude-coordinator/runs").glob("*/report.md")).read_text()
        self.assertIn("interrupted", report)

    def test_a_second_signal_during_the_interrupt_cleanup_does_not_abandon_it(self):
        # a job started in the background inherits SIGINT as ignored; make Ctrl-C real
        self.addCleanup(signal.signal, signal.SIGINT, signal.signal(signal.SIGINT, signal.default_int_handler))
        sh = FakeShell(SPRINT, stubborn=True)
        self.guard(sh)
        hook = lambda n: os.kill(os.getpid(), signal.SIGINT)  # noqa: E731  a Ctrl-C on every short sleep
        with mock.patch.object(coord, "KILL_GRACE", 0.3), self.poll_sleep(0.05, hook):
            with self.assertRaises(KeyboardInterrupt):
                self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                        "--max-lanes", "2", shell=sh)
        self.assertEqual(len(sh.procs), 2)
        self.no_lane_state(sh)
        self.assertTrue(list((self.repo / ".git/claude-coordinator/runs").glob("*/report.md")))

    def test_an_interrupt_while_reading_a_finished_lane_reports_it_interrupted(self):
        sh = FakeShell(SPRINT)
        with mock.patch.object(coord, "inspect", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.go("run", "--apply", "--roles", "implementation", "--poll", "0.05",
                        "--max-lanes", "1", shell=sh)
        self.no_lane_state(sh)
        report = next((self.repo / ".git/claude-coordinator/runs").glob("*/report.md")).read_text()
        self.assertIn("interrupted", report)

    def test_nothing_to_do_is_not_ok_when_git_status_timed_out(self):
        code, out = self.sre_run(status_fail=True)
        self.assertEqual(code, 1, out)



if __name__ == "__main__":
    unittest.main()
