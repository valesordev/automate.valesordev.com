"""project_mirror: story files -> the desired GitHub issues and project fields (no network)."""
import re
import sys
import unittest
from pathlib import Path

import importlib.machinery
import importlib.util

_BIN = Path(__file__).resolve().parent.parent / "plugins/valesor-dev/bin/project-mirror"
_loader = importlib.machinery.SourceFileLoader("project_mirror", str(_BIN))
pm = importlib.util.module_from_spec(importlib.util.spec_from_loader("project_mirror", _loader))
sys.modules["project_mirror"] = pm  # dataclasses look the module up
sys.dont_write_bytecode = True  # keep bin/ free of __pycache__ (install vendors bin/)
_loader.exec_module(pm)
pm.set_prefix("GS")

ROADMAP = """# Roadmap
### Milestones
#### M1 — Walking skeleton *(gate: `game-cli play` moves north)*
Epics: `EPIC-02`.
#### M2 — Persistent world *(gate: kill -9 and recover)*
Character deletion (`GS-SRV-032`).
### Sequencing rationale
GS-SRV-099 is mentioned here, outside any milestone.
"""

EPIC = """---
id: EPIC-{n}
title: Epic {n}
milestone: {ms}
status: ready
---

## Goal
"""

STORY = """---
id: {id}
title: Story {id}
epic: {epic}
component: server
type: {type}
status: {status}
size: S
depends_on: [{deps}]
lane: implementation   # a comment
risk: low
---

## Context
Body of {id}.
"""

SPRINT = """# {name} — goal
Status: {status}

## Demo goal
GS-SRV-001 is mentioned in prose and doesn't count.

## Contract review (SRE observability, then architecture — first)
- GS-SRV-004 — a draft

## Implementation backlog (pickup order)
1. GS-SRV-002 — first — depends on GS-SRV-001
2. GS-SRV-003, GS-SRV-032 — together
3. The §8 review of GS-SRV-001

## Close-out
- GS-SRV-005 — closed-out lines don't count
"""


def story(id, epic="EPIC-02", status="ready", type="feature", deps=""):
    return STORY.format(id=id, epic=epic, status=status, type=type, deps=deps)


class ParseTest(unittest.TestCase):
    def test_frontmatter(self):
        meta, body = pm.frontmatter(story("GS-SRV-001", deps="GS-SRV-002, GS-SRV-003"))
        self.assertEqual(meta["depends_on"], ["GS-SRV-002", "GS-SRV-003"])
        self.assertEqual(meta["lane"], "implementation")
        self.assertTrue(body.startswith("## Context"))

    def test_milestone_range(self):
        self.assertEqual(pm.milestone_range("M1–M3"), ["M1", "M2", "M3"])
        self.assertEqual(pm.milestone_range("M0-M1"), ["M0", "M1"])
        self.assertEqual(pm.milestone_range("M4"), ["M4"])
        self.assertEqual(pm.milestone_range("post-Phase-1"), [])

    def test_milestones(self):
        ms = pm.milestones(ROADMAP)
        self.assertEqual(list(ms), ["M1", "M2"])
        self.assertEqual(ms["M1"]["title"], "M1 — Walking skeleton")
        self.assertEqual(ms["M2"]["gate"], "kill -9 and recover")
        self.assertIn("GS-SRV-032", ms["M2"]["text"])
        self.assertNotIn("GS-SRV-099", ms["M2"]["text"])

    def test_sprint_lists_only_list_heads_in_plan_sections(self):
        ids = pm.sprint_lists(SPRINT.format(name="SPRINT-02", status="active"))
        self.assertEqual(ids, ["GS-SRV-004", "GS-SRV-002", "GS-SRV-003", "GS-SRV-032"])


class BuildTest(unittest.TestCase):
    def files(self, sprints=(("SPRINT-01", "closed"), ("SPRINT-02", "active"))):
        f = {"docs/roadmap.md": ROADMAP,
             "docs/epics/EPIC-02.md": EPIC.format(n="02", ms="M1"),
             "docs/epics/EPIC-08.md": EPIC.format(n="08", ms="M1–M2"),
             "docs/stories/GS-SRV-001.md": story("GS-SRV-001", status="done"),
             "docs/stories/GS-SRV-002.md": story("GS-SRV-002", deps="GS-SRV-001, GS-XXX-999"),
             "docs/stories/GS-SRV-003.md": story("GS-SRV-003", type="infra", status="in-progress"),
             "docs/stories/GS-SRV-004.md": story("GS-SRV-004", status="draft"),
             "docs/stories/GS-SRV-032.md": story("GS-SRV-032", epic="EPIC-08"),
             "docs/stories/GS-SRV-033.md": story("GS-SRV-033", epic="EPIC-08")}
        for name, status in sprints:
            f[f"docs/sprints/{name}.md"] = SPRINT.format(name=name, status=status)
        f["docs/sprints/SPRINT-02-demo.md"] = "# demo\n- GS-SRV-033 — demos aren't plans\n"
        return f

    def test_issues(self):
        want, ms, warn = pm.build(self.files(), "a" * 40)
        s1, s2, s3 = want["GS-SRV-001"], want["GS-SRV-002"], want["GS-SRV-003"]
        self.assertEqual(s2.title, "GS-SRV-002 — Story GS-SRV-002")
        self.assertTrue(s1.closed)
        self.assertFalse(s2.closed)
        self.assertIn("Mirrored from `docs/stories/GS-SRV-002.md` at `aaaaaaa`", s2.body)
        self.assertEqual(pm.strip_notice(s2.body), "## Context\nBody of GS-SRV-002.\n")
        self.assertEqual((s2.issue_type, s3.issue_type), ("Feature", "Task"))
        self.assertEqual(s2.parent, "EPIC-02")
        self.assertEqual(want["EPIC-02"].issue_type, "Epic")
        self.assertEqual(s2.blocked_by, ["GS-SRV-001"])
        self.assertTrue(any("GS-XXX-999" in w for w in warn))

    def test_milestone_from_epic_or_roadmap(self):
        want, _, warn = pm.build(self.files(), "a" * 40)
        self.assertEqual(want["GS-SRV-002"].milestone, "M1")          # single-milestone epic
        self.assertEqual(want["EPIC-08"].milestone, None)             # spans M1–M2
        self.assertEqual(want["GS-SRV-032"].milestone, "M2")          # named in the M2 section
        self.assertEqual(want["GS-SRV-033"].milestone, None)
        self.assertTrue(any(w.startswith("GS-SRV-033: no milestone") for w in warn))

    def test_sprint_plan_rank(self):
        want, _, _ = pm.build(self.files(), "a" * 40)
        f = lambda k: (want[k].fields["Sprint"], want[k].fields["Plan"], want[k].fields["Rank"])
        self.assertEqual(f("GS-SRV-004"), ("SPRINT-02", "Current", 1))
        self.assertEqual(f("GS-SRV-002"), ("SPRINT-02", "Current", 2))
        self.assertEqual(f("GS-SRV-001"), (None, None, None))         # prose mention only
        self.assertEqual(f("GS-SRV-033"), (None, None, None))         # demo files aren't plans
        self.assertEqual(want["GS-SRV-002"].fields["Status"], "ready")
        self.assertEqual(want["GS-SRV-002"].fields["Lane"], "implementation")

    def test_planned_sprint_is_next_and_done_has_no_plan(self):
        files = self.files((("SPRINT-01", "closed"), ("SPRINT-02", "planned")))
        files["docs/stories/GS-SRV-002.md"] = story("GS-SRV-002", status="done")
        want, _, _ = pm.build(files, "a" * 40)
        self.assertEqual(want["GS-SRV-004"].fields["Plan"], "Next")
        self.assertEqual(want["GS-SRV-002"].fields["Sprint"], "SPRINT-02")
        self.assertEqual(want["GS-SRV-002"].fields["Plan"], None)

    def test_managed_needs_title_and_notice(self):
        body = pm.notice("docs/stories/x.md", "a" * 40) + "## Context\n"
        self.assertEqual(pm.managed("GS-INF-013 — Publish the image", body), "GS-INF-013")
        self.assertEqual(pm.managed("EPIC-01 — Foundations", body), "EPIC-01")
        self.assertIsNone(pm.managed("GS-INF-013: helm-install writes a bad label", body))
        self.assertIsNone(pm.managed("GS-INF-013 — Publish the image", "a hand-written issue"))
        self.assertIsNone(pm.managed("GS-INF-013 — Publish the image", None))

    def test_comparable_ignores_the_commit_but_not_the_path(self):
        body = "## Context\n"
        a = pm.notice("docs/stories/x.md", "a" * 40) + body
        self.assertEqual(pm.comparable(a), pm.comparable(pm.notice("docs/stories/x.md", "b" * 40) + body))
        self.assertNotEqual(pm.comparable(a), pm.comparable(pm.notice("docs/stories/y.md", "a" * 40) + body))

    def test_body_limit(self):
        body = pm.fit("x" * (pm.BODY_LIMIT + 10), "docs/stories/big.md")
        self.assertLess(len(body), 65536)
        self.assertIn("docs/stories/big.md", body)



BLOB = staticmethod(lambda path: f"https://github.com/o/r/blob/stories-final/{path}")


class FakeGitHub(pm.GitHub):
    """Canned REST reads; GraphQL is never called (load_state and sync are stubbed)."""

    def __init__(self, apply=False, sprints=(), labels=(), comments=None):
        super().__init__("o/r", apply)
        self.sprints, self.labels, self.comments = list(sprints), list(labels), comments or {}
        self.posts = []

    def rest(self, *args):
        path = next(a for a in args if a.startswith("repos/"))
        if len(args) > 1:
            self.posts.append(args)
            return {"number": 900 + len(self.posts)}
        if "issues?labels=sprint" in path:
            return self.sprints
        if path.endswith("labels?per_page=100"):
            return [{"name": n} for n in self.labels]
        n = int(re.search(r"issues/(\d+)/comments", path).group(1))
        return [{"body": b} for b in self.comments.get(n, [])]

    def mutate(self, what, query, **vars):
        self.act(what)
        return {"createProjectV2Field": {"projectV2Field": {"id": "F2", "dataType": "NUMBER"}}} \
            if self.apply and "createProjectV2Field" in query else None


class CutoverTest(unittest.TestCase):
    def setUp(self):
        self.files = BuildTest.files(BuildTest(), (("SPRINT-01", "closed"), ("SPRINT-02", "active")))
        self.want, self.ms, _ = pm.build(self.files, "a" * 40)
        self.feedback = ["docs/feedback/GS-SRV-002-question.md", "docs/feedback/GS-SRV-002.md",
                         "docs/feedback/GS-SRV-0021-other.md", "docs/feedback/GS-SRV-001-old.md"]

    def number(self, key):
        """The issue number state() gives a key: its place in sorted order."""
        return sorted(self.want).index(key) + 1

    def state(self, sprint_type="SINGLE_SELECT", notice=True):
        issues, items = {}, {}
        for n, (key, w) in enumerate(sorted(self.want.items()), 1):
            body = w.body if notice else pm.strip_notice(w.body)
            issues[key] = {"id": f"I{n}", "number": n, "title": w.title, "body": body}
            items[n] = {"id": f"T{n}", "values": {k: v for k, v in w.fields.items() if v is not None}}
        return {"project": {"id": "P", "url": "https://p"}, "issues": issues, "items": items,
                "fields": {"Sprint": {"id": "F1", "name": "Sprint", "dataType": sprint_type}}}

    def run_cutover(self, gh, st, synced=True):
        import contextlib
        import io
        from unittest import mock

        def fake_sync(g, *a):
            if not synced:
                g.act("GS-SRV-002: Status = done")
        out = io.StringIO()
        with mock.patch.object(pm, "load_state", lambda *a, **k: st), \
             mock.patch.object(pm, "sync", fake_sync), contextlib.redirect_stdout(out):
            rc = pm.cutover(gh, self.want, self.ms, self.files, self.feedback, "T", "stories-final", BLOB)
        return rc, out.getvalue()

    def test_sprint_issues(self):
        sprints, problems = pm.sprint_issues(self.files, BLOB)
        self.assertEqual(problems, [])
        s1, s2 = sprints
        self.assertEqual((s1.n, s1.title, s1.status), (1, "SPRINT-01 — goal", "closed"))
        self.assertIn("[`docs/sprints/SPRINT-01.md`](https://github.com/o/r/blob/stories-final/"
                      "docs/sprints/SPRINT-01.md)", s1.body)
        self.assertNotIn("Demo goal", s1.body)
        self.assertEqual(s2.status, "active")
        self.assertTrue(s2.body.startswith("Plan at the cutover, from [`docs/sprints/SPRINT-02.md`]"))
        self.assertIn("## Demo goal", s2.body)
        self.assertNotIn("Status:", s2.body)
        self.assertNotIn("# SPRINT-02", s2.body)

    def test_sprint_issues_refuse_a_bad_series(self):
        files = dict(self.files, **{"docs/sprints/SPRINT-03.md": "# SPRINT-03 — x\nStatus: planned\n"})
        self.assertIn("only the highest-numbered", pm.sprint_issues(files, BLOB)[1][0])
        files = dict(self.files, **{"docs/sprints/SPRINT-03.md": "# SPRINT-03 — x\nStatus: done\n"})
        self.assertIn("Status 'done'", pm.sprint_issues(files, BLOB)[1][0])

    def test_cutover_body(self):
        body = pm.notice("docs/stories/x.md", "a" * 40) + pm.fit("y" * (pm.BODY_LIMIT + 5), "docs/stories/x.md")
        out = pm.cutover_body(body, BLOB)
        self.assertFalse(out.startswith("> [!NOTE]"))
        self.assertTrue(out.endswith("the full story is [`docs/stories/x.md`]"
                                     "(https://github.com/o/r/blob/stories-final/docs/stories/x.md).\n"))
        self.assertEqual(pm.cutover_body("plain\n", BLOB), "plain\n")

    def test_feedback_files_match_the_id_exactly(self):
        self.assertEqual(pm.feedback_files(self.feedback, "GS-SRV-002"),
                         ["docs/feedback/GS-SRV-002-question.md", "docs/feedback/GS-SRV-002.md"])

    def test_stops_when_the_board_differs(self):
        gh = FakeGitHub()
        rc, out = self.run_cutover(gh, self.state(), synced=False)
        self.assertEqual(rc, 1)
        self.assertIn("doesn't match stories-final", out)
        self.assertEqual(gh.ops, {})

    def test_dry_run_plans_every_step(self):
        gh = FakeGitHub()
        rc, out = self.run_cutover(gh, self.state())
        self.assertEqual(rc, 0, out)
        self.assertIn("board matches stories-final", out)
        self.assertIn("delete single-select Sprint", out)
        self.assertIn("create number field Sprint", out)
        self.assertIn("GS-SRV-002: Sprint = 2", out)
        self.assertNotIn("GS-SRV-001: Sprint", out)        # never listed in a sprint
        self.assertIn("create label sprint:active", out)
        self.assertIn("create SPRINT-01 — goal (closed)", out)
        self.assertIn("create SPRINT-02 — goal (active)", out)
        self.assertIn("GS-SRV-002 #", out)
        self.assertIn("drop the mirror notice", out)
        self.assertIn(f"GS-SRV-002 #{self.number('GS-SRV-002')}: comment linking 2 feedback file(s)", out)
        self.assertNotIn("GS-SRV-001 #", out.split("feedback pointers")[1])  # done: no pointer
        self.assertEqual(gh.posts, [])

    def test_apply_writes_sprints_and_comments(self):
        gh = FakeGitHub(apply=True)
        rc, out = self.run_cutover(gh, self.state())
        self.assertEqual(rc, 0, out)
        creates = [a for a in gh.posts if a[0].endswith("/issues") and "-f" in a]
        self.assertEqual([a[2] for a in creates], ["title=SPRINT-01 — goal", "title=SPRINT-02 — goal"])
        self.assertIn("labels[]=sprint:active", creates[1])
        self.assertNotIn("labels[]=sprint:active", creates[0])
        closes = [a for a in gh.posts if a[:2] == ("-X", "PATCH")]
        self.assertEqual(len(closes), 1)                    # only SPRINT-01 is closed
        self.assertIn("state=closed", closes[0])
        comment = next(a for a in gh.posts if a[0].endswith("/comments"))
        self.assertIn("stories-final/docs/feedback/GS-SRV-002-question.md", comment[2])

    def test_mirror_refuses_a_cut_over_board(self):
        from unittest import mock
        st = {"issues": {}, "unmarked": [1, 2], "project": None, "fields": {}, "items": {}}
        with mock.patch.object(pm, "load_state", lambda *a, **k: st):
            with self.assertRaises(SystemExit) as e:
                pm.sync(FakeGitHub(), self.want, self.ms, "T")
        self.assertIn("would duplicate", str(e.exception))

    def test_rerun_is_a_no_op(self):
        st = self.state("NUMBER", notice=False)
        for it in st["items"].values():
            if "Sprint" in it["values"]:
                it["values"]["Sprint"] = float(pm.sprint_number(it["values"]["Sprint"]))
        gh = FakeGitHub(sprints=[{"title": "SPRINT-01 — goal"}, {"title": "SPRINT-02 — goal"}],
                        labels=pm.SPRINT_LABELS, comments={self.number("GS-SRV-002"): [pm.FEEDBACK_MARK + " earlier"]})
        rc, out = self.run_cutover(gh, st, synced=False)    # no board check once started
        self.assertEqual(rc, 0, out)
        self.assertIn("cutover already started", out)
        self.assertEqual(gh.ops, {}, out)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        pm.set_prefix("GS")

    def test_read_config(self):
        f = self.dir / "_repo.md"
        f.write_text("---\ngenerated: [a/]\nproject: Demo Game  # the org Project\n"
                     "story_prefix: GS\n---\nbody: not read\n")
        self.assertEqual(pm.read_config(f),
                         {"generated": "[a/]", "project": "Demo Game", "story_prefix": "GS"})
        self.assertEqual(pm.read_config(self.dir / "missing.md"), {})

    def test_prefix_scopes_story_ids(self):
        pm.set_prefix("VG")
        self.assertEqual(pm.STORY_ID.findall("VG-APP-001 and GS-SRV-002"), ["VG-APP-001"])
        self.assertEqual(pm.sprint_lists("## Implementation backlog\n- VG-APP-001 — x\n"
                                         "- GS-SRV-002 — y\n"), ["VG-APP-001"])

    def test_origin_repo(self):
        import subprocess
        subprocess.run(["git", "init", "-q", str(self.dir)], check=True)
        self.assertIsNone(pm.origin_repo(str(self.dir)))
        for url in ("https://gitlab.com/x/y.git", "https://gitlab.com/acme/github-actions.git",
                    "https://git.example.com/git/acme/tools.git"):
            subprocess.run(["git", "-C", str(self.dir), "remote", "remove", "origin"],
                           capture_output=True)
            subprocess.run(["git", "-C", str(self.dir), "remote", "add", "origin", url], check=True)
            self.assertIsNone(pm.origin_repo(str(self.dir)), url)
        for url in ("git@github.com:example/game-server.git",
                    "https://github.com/example/game-server",
                    "ssh://git@github.com:22/example/game-server.git",
                    "git@github-valesor:example/game-server.git",
                    "http://local_proxy@127.0.0.1:41235/git/example/game-server"):
            subprocess.run(["git", "-C", str(self.dir), "remote", "remove", "origin"],
                           capture_output=True)
            subprocess.run(["git", "-C", str(self.dir), "remote", "add", "origin", url], check=True)
            self.assertEqual(pm.origin_repo(str(self.dir)), "example/game-server")

    def repo(self, files, config="---\nproject: P\nstory_prefix: GS\n---\n"):
        import subprocess
        g = lambda *a: subprocess.run(["git", "-C", str(self.dir), *a], check=True,
                                      capture_output=True)
        g("init", "-q")
        for rel, text in {".claude/roles/_repo.md": config, **files}.items():
            (self.dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.dir / rel).write_text(text)
        g("add", "-A")
        g("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
          "commit", "-qm", "x")
        g("update-ref", "refs/remotes/origin/main", "HEAD")

    def offline(self, *where):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(pm.main([str(self.dir.joinpath(*where)), "--offline"]), 0)
        return out.getvalue()

    def sprint(self, name, status, ids, section="Implementation backlog (pickup order)"):
        items = "".join(f"- {i} — x\n" for i in ids)
        return f"# {name}\nStatus: {status}\n\n## {section}\n{items}"

    def test_board_counts_each_sprint_from_its_file(self):
        self.repo({
            "docs/stories/a.md": story("GS-SRV-001", status="done"),
            "docs/stories/b.md": story("GS-SRV-002", status="done"),
            "docs/stories/c.md": story("GS-SRV-003", status="review"),
            "docs/sprints/SPRINT-03.md": self.sprint("SPRINT-03", "active",
                                                     ["GS-SRV-001", "GS-SRV-002", "GS-SRV-003"]),
            "docs/sprints/SPRINT-04.md": self.sprint("SPRINT-04", "planned", ["GS-SRV-003"],
                                                     section="Carryover from SPRINT-03"),
        })
        out = self.offline()
        self.assertIn("Current (SPRINT-03): 2/3 done — 1 review, 2 done", out)  # carryover kept
        self.assertIn("Next (SPRINT-04): 0/1 done — 1 review", out)
        self.assertIn("GS-SRV-003  implementation", out)
        self.assertNotIn("GS-SRV-001  ", out)  # done stories aren't listed

    def test_board_all_done_still_reports(self):
        self.repo({
            "docs/stories/a.md": story("GS-SRV-001", status="done"),
            "docs/sprints/SPRINT-03.md": self.sprint("SPRINT-03", "active", ["GS-SRV-001"]),
        })
        self.assertIn("Current (SPRINT-03): 1/1 done — 1 done", self.offline())

    def test_config_prefix_and_subdirectory(self):
        self.repo({
            "docs/stories/a.md": story("VG-APP-001"),
            "docs/stories/b.md": story("GS-SRV-002"),
            "docs/sprints/SPRINT-01.md": self.sprint("SPRINT-01", "active",
                                                     ["VG-APP-001", "GS-SRV-002"]),
        }, config="---\nproject: P\nstory_prefix: VG\n---\n")
        out = self.offline(".claude", "roles")  # run from a subdirectory: config still found
        self.assertIn("Current (SPRINT-01): 0/1 done — 1 ready", out)
        self.assertNotIn("GS-SRV-002", out.split("Current", 1)[1])

    def test_unconfigured_repo_is_a_no_op(self):
        import contextlib
        import io
        import subprocess
        subprocess.run(["git", "init", "-q", str(self.dir)], check=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(pm.main([str(self.dir), "--apply"]), 0)
        self.assertIn("not configured", out.getvalue())

    def test_github_native_repo_is_never_mirrored(self):
        import contextlib
        import io
        import subprocess
        subprocess.run(["git", "init", "-q", str(self.dir)], check=True)
        cfg = self.dir / "_repo.md"
        cfg.write_text("---\nproject: Demo Game\nstory_prefix: GS\nstories: github\n---\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(pm.main([str(self.dir), "--config", str(cfg), "--apply"]), 0)
        self.assertIn("nothing to mirror", out.getvalue())


if __name__ == "__main__":
    unittest.main()
