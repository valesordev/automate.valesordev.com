"""bin/story against an in-memory GitHub: role transitions, field edits, create, comments."""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests/fixtures/consumer"   # game-server, game-content, and solo7-media
TOOLKIT = ROOT / "toolkit"
os.environ.setdefault("STORY_FIND_DELAY", "0")  # no real sleeping on a miss
SID = "session_story_test"
SERVER, CONTENT = "game-server", "game-content"
OPTIONS = {
    "Status": ["draft", "ready", "in-progress", "review", "done", "blocked"],
    "Plan": ["Current", "Next", "Later"],
    "Sprint": ["SPRINT-04", "SPRINT-05"],
    "Lane": ["architecture", "sre", "implementation", "content", "world-builder", "director",
             "visual-designer", "product"],
    "Size": ["S", "M", "L"],
    "Risk": ["low", "medium", "high"],
    "Component": ["server", "cli", "infra", "canon", "pack", "text"],
}


class FakeGH:
    """Just enough of the REST API for bin/story."""

    def __init__(self):
        self.calls = []
        self.fields = [{"id": 100 + i, "name": n, "data_type": "single_select",
                        "options": [{"id": f"{n}-{o}", "name": {"raw": o}} for o in opts]}
                       for i, (n, opts) in enumerate(OPTIONS.items())]
        self.fields += [{"id": 200, "name": "Rank", "data_type": "number"},
                        {"id": 201, "name": "Story ID", "data_type": "text"},
                        {"id": 202, "name": "Title", "data_type": "title"},
                        {"id": 203, "name": "Cycle", "data_type": "number"}]
        self.issues, self.items, self.comments, self.links, self.prs = {}, {}, {}, [], {}
        self.hidden = {}  # item id -> project listings that still omit it (GitHub's sync lag)

    def pr(self, n, labels, body="", commits=(), merged=True, base="main"):
        self.prs[n] = {"number": n, "merged_at": "2026-10-03T00:00:00Z" if merged else None,
                       "base": {"ref": base, "repo": {"default_branch": "main"}},
                       "labels": [{"name": l} for l in labels], "body": body,
                       "commits": [{"commit": {"message": c}} for c in commits]}

    def add(self, sid, title, repo=SERVER, **fields):
        n = len(self.issues) + 1
        self.issues[n] = {"id": 9000 + n, "number": n, "title": f"{sid} — {title}", "state": "open",
                          "body": f"Body of {sid}", "html_url": f"https://gh/{n}", "repo": repo}
        # project-mirror sets Story ID on stories only; epics are known by title
        self.items[500 + n] = {"n": n, "fields": ({} if sid.startswith("EPIC-") else {"Story ID": sid})
                               | fields}
        return n

    def _fid(self, name):
        return next(f for f in self.fields if f["name"] == name)

    def _item_json(self, iid):
        it, iss = self.items[iid], self.issues[self.items[iid]["n"]]
        out = []
        for f in self.fields:
            v = it["fields"].get(f["name"])
            if v is not None and f["data_type"] == "single_select":
                v = {"id": f"{f['name']}-{v}", "name": {"raw": v}}
            elif v is not None and f["data_type"] == "text":
                v = {"raw": v}
            out.append({"id": f["id"], "name": f["name"], "data_type": f["data_type"], "value": v})
        return {"id": iid, "content_type": "Issue", "fields": out,
                "content": {**{k: iss[k] for k in ("id", "number", "title", "state")},
                            "repository_url": f"https://api.github.com/repos/example/{iss['repo']}"}}

    def api(self, method, path, body=None, paginate=False):
        self.calls.append((method, path, body))
        base = "/orgs/example/projectsV2/2"
        if method == "GET" and path.startswith("/orgs/example/projectsV2?"):
            return [{"title": "Demo Game", "number": 2}]
        if method == "GET" and path.startswith(f"{base}/fields"):
            return self.fields
        if method == "GET" and path.startswith(f"{base}/items"):
            seen = [i for i in self.items if not self.hidden.get(i)]
            for i in list(self.hidden):
                self.hidden[i] = max(0, self.hidden[i] - 1)
            return [self._item_json(i) for i in seen]
        m = re.fullmatch(r"/repos/example/[^/]+/pulls/(\d+)(/commits)?", path.split("?")[0])
        if method == "GET" and m:
            pr = self.prs[int(m.group(1))]
            return pr["commits"] if m.group(2) else pr
        if method == "POST" and path == f"{base}/items":
            n = next(k for k, v in self.issues.items() if v["id"] == body["id"])
            iid = 500 + n
            self.items[iid] = {"n": n, "fields": {}}
            return {"id": iid}
        m = re.fullmatch(rf"{base}/items/(\d+)", path)
        if method == "PATCH" and m:
            it = self.items[int(m.group(1))]
            for f in body["fields"]:
                name = next(x["name"] for x in self.fields if x["id"] == f["id"])
                v = f["value"]
                it["fields"][name] = v.split("-", 1)[1] if isinstance(v, str) and \
                    self._fid(name)["data_type"] == "single_select" else v
            return {}
        m = re.fullmatch(r"/repos/example/([^/]+)/issues", path.split("?")[0])
        if method == "GET" and m and "labels=sprint" in path:
            return [i for i in self.issues.values() if i["repo"] == m.group(1)
                    and any(l["name"] == "sprint" for l in i.get("labels", []))]
        if method == "GET" and m and "state=all" in path:
            return [i for i in self.issues.values() if i["repo"] == m.group(1)]
        if method == "POST" and m:
            n = len(self.issues) + 1
            self.issues[n] = {"id": 9000 + n, "number": n, "title": body["title"], "state": "open",
                              "body": body["body"], "html_url": f"https://gh/{n}", "repo": m.group(1),
                              "labels": [{"name": l} for l in body.get("labels", [])]}
            return self.issues[n]
        m = re.fullmatch(r"/repos/example/([^/]+)/issues/(\d+)(/.*)?", path.split("?")[0])
        if m:
            n, rest = int(m.group(2)), m.group(3) or ""
            assert self.issues[n]["repo"] == m.group(1), f"#{n} is in {self.issues[n]['repo']}, not {m.group(1)}"
            if rest == "" and method == "GET":
                return self.issues[n]
            if rest == "" and method == "PATCH":
                self.issues[n].update(body)
                return self.issues[n]
            if rest == "/dependencies/blocked_by" and method == "GET":
                ids = [i for (k, r, i) in self.links if k == n and r == rest]
                return [{"id": i["id"], "number": i["number"], "state": i["state"]}
                        for i in self.issues.values() if i["id"] in ids]
            if rest == "/labels" and method == "POST":
                self.issues[n].setdefault("labels", []).extend({"name": l} for l in body["labels"])
                return {}
            if rest.startswith("/labels/") and method == "DELETE":
                self.issues[n]["labels"] = [l for l in self.issues[n]["labels"] if l["name"] != rest[8:]]
                return {}
            if rest == "/comments" and method == "POST":
                self.comments.setdefault(n, []).append(body["body"])
                return {}
            if rest == "/comments" and method == "GET":
                return [{"body": b, "user": {"login": "x"}} for b in self.comments.get(n, [])]
            if rest in ("/sub_issues", "/dependencies/blocked_by") and method == "POST":
                key = "sub_issue_id" if rest == "/sub_issues" else "issue_id"
                ids = {i["id"] for i in self.issues.values()}
                assert body[key] in ids, f"{key} must be an issue id, got {body[key]}"
                self.links.append((n, rest, body[key]))
                return {}
            dm = re.fullmatch(r"/dependencies/blocked_by/(\d+)", rest)
            if dm and method == "DELETE":
                link = (n, "/dependencies/blocked_by", int(dm.group(1)))
                assert link in self.links, f"no link {link}"
                self.links.remove(link)
                return {}
        raise AssertionError(f"unexpected {method} {path}")


def load_story():
    loader = importlib.machinery.SourceFileLoader("story_bin", str(ROOT / "plugins/valesor-dev/bin/story"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("story_bin", loader))
    sys.modules["story_bin"] = mod
    sys.dont_write_bytecode = True
    loader.exec_module(mod)
    return mod


story = load_story()


class StoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "game-server"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        r = subprocess.run([sys.executable, str(TOOLKIT / "install.py"), "--root", str(FIXTURE), "game-server",
                            str(self.repo)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        repo_md = self.repo / ".claude/roles/_repo.md"
        repo_md.write_text(repo_md.read_text().replace("story_prefix: GS\n",
                                                       "story_prefix: GS\nstories: github\n"))
        self.old = os.getcwd()
        os.chdir(self.repo)
        self.gh = FakeGH()
        self.gh.add("GS-SRV-001", "server thing", Status="ready", Lane="implementation", Rank=2,
                    Plan="Current", Sprint="SPRINT-05")
        self.gh.add("GS-INF-001", "infra thing", Status="ready", Lane="sre", Rank=1,
                    Plan="Current", Sprint="SPRINT-05")
        self.gh.add("GS-SRV-002", "a draft", Status="draft", Lane="implementation")
        self.gh.add("GS-SRV-003", "in review", Status="review", Lane="implementation")
        self.gh.add("EPIC-05", "an epic")

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def as_role(self, name):
        r = subprocess.run([str(self.repo / ".claude/bin/role"), "set", name, SID],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def record(self, n, *heads):
        """Seed records (`**§8 (architecture):**`, …) on issue n, as `story comment --record` writes them."""
        for h in heads:
            self.gh.comments.setdefault(n, []).append(f"**{h}:** ok\n\n<!-- from: x -->")

    def done_records(self, n):
        self.record(n, "§8 instrumentation (sre)", "§8 (architecture)")

    def run_story(self, *args, sid=SID):
        out, err = io.StringIO(), io.StringIO()
        argv = (["--sid", sid] if sid else []) + list(args)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = story.main(argv, gh=self.gh)
        return code, out.getvalue() + err.getvalue()

    def status(self, sid):
        return next(i["fields"].get("Status") for i in self.gh.items.values()
                    if i["fields"].get("Story ID") == sid)

    def test_list_filters_and_ranks(self):
        code, out = self.run_story("list", "--plan", "Current", sid=None)
        self.assertEqual(code, 0, out)
        lines = out.strip().splitlines()
        self.assertEqual([l.split()[1] for l in lines], ["GS-INF-001", "GS-SRV-001"])
        code, out = self.run_story("list", "--lane", "sre", sid=None)
        self.assertEqual([l.split()[1] for l in out.strip().splitlines()], ["GS-INF-001"])
        self.assertNotIn("EPIC-05", self.run_story("list", sid=None)[1])

    def test_show_needs_no_role(self):
        code, out = self.run_story("show", "GS-SRV-001", sid=None)
        self.assertEqual(code, 0, out)
        self.assertIn("Status: ready", out)
        self.assertIn("Blocked by: -", out)
        self.gh.links.append((1, "/dependencies/blocked_by", self.gh.issues[4]["id"]))
        self.assertIn("Blocked by: GS-SRV-003 (review)", self.run_story("show", "GS-SRV-001", sid=None)[1])
        self.assertIn("Body of GS-SRV-001", out)

    def blockers(self, n):
        return sorted(i for (k, r, i) in self.gh.links if k == n and r == "/dependencies/blocked_by")

    def test_depends_adds_and_removes_blocked_by_links(self):
        gc = self.gh.add("GC-PCK-001", "a content pack", repo=CONTENT, Status="draft", Lane="content")
        self.as_role("pm")
        code, out = self.run_story("depends", "GS-SRV-002", "--add", "GS-INF-001,GC-PCK-001", "--note", "infra first")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.blockers(3), sorted([self.gh.issues[2]["id"], self.gh.issues[gc]["id"]]))
        self.assertIn("blocked by GS-INF-001 (ready), GC-PCK-001 (draft) (+GS-INF-001, +GC-PCK-001)", out)
        self.assertEqual(self.gh.comments[3][-1], "**Depends (pm):** blocked by +GS-INF-001, +GC-PCK-001. infra first")
        self.assertIn("Blocked by: GS-INF-001 (ready), GC-PCK-001 (draft)", self.run_story("show", "GS-SRV-002")[1])
        writes = len([c for c in self.gh.calls if c[0] != "GET"])
        code, out = self.run_story("depends", "GS-SRV-002", "--add", "GS-INF-001", "--remove", "GS-SRV-001")
        self.assertEqual(code, 0, out)
        self.assertIn("no change (already blocked by GS-INF-001; not blocked by GS-SRV-001)", out)
        self.assertEqual(len([c for c in self.gh.calls if c[0] != "GET"]), writes)    # nothing written
        code, out = self.run_story("depends", "GS-SRV-002", "--remove", "GC-PCK-001", "--add", "GS-SRV-001")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.blockers(3), sorted([self.gh.issues[2]["id"], self.gh.issues[1]["id"]]))
        self.assertIn("(+GS-SRV-001, -GC-PCK-001)", out)

    def test_depends_needs_the_story_edit_and_this_repos_story(self):
        gc = self.gh.add("GC-PCK-001", "a content pack", repo=CONTENT, Status="draft", Lane="content")
        code, out = self.run_story("depends", "GS-SRV-002", "--add", "GS-INF-001", sid=None)
        self.assertIn("no role set", out)
        self.as_role("impl")
        code, out = self.run_story("depends", "GS-SRV-002", "--add", "GS-INF-001")
        self.assertEqual(code, 1)
        self.assertIn("needs `depends` in story_edits", out)
        self.as_role("arch")
        for args, why in ((("GC-PCK-001", "--add", "GS-INF-001"), "belongs to example/game-content"),
                          (("GS-SRV-002", "--add", "GS-SRV-002"), "can't block itself"),
                          (("GS-SRV-002", "--add", "GS-INF-001", "--remove", "GS-INF-001"), "both added and removed"),
                          (("GS-SRV-002",), "give --add"),
                          (("GS-SRV-002", "--add", "GS-INF-001,GS-NOPE-404"), "no story GS-NOPE-404")):
            code, out = self.run_story("depends", *args)
            self.assertEqual(code, 1, args)
            self.assertIn(why, out, args)
        self.assertEqual(self.gh.links, [])          # a bad ID anywhere leaves nothing behind
        self.as_role("none")
        self.assertEqual(self.run_story("depends", "GS-SRV-002", "--add", "GS-INF-001")[0], 0)
        self.assertNotIn(gc, [k for (k, _, _) in self.gh.links])

    def test_lane_starts_only_its_own_ready_stories(self):
        self.as_role("impl")
        self.assertEqual(self.run_story("start", "GS-SRV-001")[0], 0)
        self.assertEqual(self.status("GS-SRV-001"), "in-progress")
        code, out = self.run_story("start", "GS-INF-001")  # sre's story
        self.assertEqual(code, 1)
        self.assertIn("can't move GS-INF-001 ready → in-progress", out)
        self.assertEqual(self.run_story("start", "GS-SRV-002")[0], 1)  # draft, not ready
        self.assertEqual(self.run_story("status", "GS-SRV-003", "done")[0], 1)  # §8 is architecture's
        self.as_role("sre")
        self.assertEqual(self.run_story("start", "GS-INF-001")[0], 0)

    def test_a_lane_never_moves_a_person_only_story(self):
        self.gh.issues[1]["labels"] = [{"name": "person-only"}]      # GS-SRV-001
        self.as_role("impl")
        with mock.patch.dict(os.environ, {"COORDINATOR_LANE": "implementation-x"}):
            code, out = self.run_story("start", "GS-SRV-001")
        self.assertEqual(code, 1)
        self.assertIn("person-only", out)
        self.assertEqual(self.status("GS-SRV-001"), "ready")
        self.assertIn("Labels: person-only", self.run_story("show", "GS-SRV-001")[1])
        self.assertEqual(self.run_story("start", "GS-SRV-001")[0], 0)    # the owner's own session may

    def test_architecture_contract_review_and_done(self):
        self.as_role("arch")
        self.record(3, "Observability review (sre)")
        self.done_records(4)
        self.assertEqual(self.run_story("status", "GS-SRV-002", "ready", "--note", "contract ok")[0], 0)
        self.assertEqual(self.status("GS-SRV-002"), "ready")
        self.assertIn("**Status (architecture):** draft → ready. contract ok", self.gh.comments[3])
        self.assertEqual(self.run_story("status", "GS-SRV-003", "done")[0], 0)
        self.assertEqual(self.gh.issues[4]["state"], "closed")
        self.assertEqual(self.run_story("status", "GS-SRV-001", "in-progress")[0], 1)  # not its lane
        self.assertEqual(self.run_story("status", "GS-SRV-001", "bogus")[0], 1)

    def test_pm_creates_and_plans(self):
        self.as_role("pm")
        body = self.repo / "b.md"
        body.write_text("## Context\nnew story\n")
        code, out = self.run_story("create", "--comp", "SRV", "--title", "new thing", "--lane",
                                   "implementation", "--size", "S", "--risk", "low", "--component",
                                   "server", "--body-file", str(body), "--epic", "EPIC-05",
                                   "--depends", "GS-SRV-001")
        self.assertEqual(code, 0, out)
        self.assertIn("created GS-SRV-004", out)  # max SRV is 003
        new = next(i for i in self.gh.items.values() if i["fields"].get("Story ID") == "GS-SRV-004")
        self.assertEqual(new["fields"]["Status"], "draft")
        self.assertEqual(new["fields"]["Lane"], "implementation")
        new_n = next(n for n, i in self.gh.issues.items() if i["title"].startswith("GS-SRV-004"))
        self.assertIn((5, "/sub_issues", self.gh.issues[new_n]["id"]), self.gh.links)  # epic ← new
        self.assertIn((new_n, "/dependencies/blocked_by", self.gh.issues[1]["id"]), self.gh.links)
        self.assertEqual(self.run_story("set", "GS-SRV-004", "Sprint=SPRINT-05", "Plan=Next",
                                        "Rank=3")[0], 0)
        self.assertEqual(new["fields"]["Rank"], 3.0)
        self.assertEqual(new["fields"]["Plan"], "Next")
        self.assertEqual(self.run_story("set", "GS-SRV-004", "Plan=Someday")[0], 1)  # no option
        self.assertEqual(self.run_story("set", "GS-SRV-004", "Status=done")[0], 1)  # not settable
        self.assertEqual(self.run_story("status", "GS-SRV-004", "ready")[0], 1)  # PM doesn't review

    def test_create_validates_before_writing(self):
        self.as_role("pm")
        body = self.repo / "b.md"
        body.write_text("x")
        base = ["create", "--comp", "SRV", "--title", "t", "--lane", "implementation", "--risk", "low",
                "--component", "server", "--body-file", str(body)]
        for extra in (["--size", "XL"], ["--size", "S", "--epic", "EPIC-99"],
                      ["--size", "S", "--depends", "GS-SRV-001,GS-NOPE-001"]):
            before = len(self.gh.issues)
            code, out = self.run_story(*base, *extra)
            self.assertEqual(code, 1, out)
            self.assertEqual(len(self.gh.issues), before, extra)
            self.assertFalse([c for c in self.gh.calls if c[0] != "GET"], extra)

    def lag(self, sid, listings):
        iid = next(k for k, v in self.gh.items.items() if v["fields"].get("Story ID") == sid)
        self.gh.hidden[iid] = listings

    def test_find_waits_out_the_projects_sync_lag(self):
        self.lag("GS-SRV-001", 2)  # the next two listings omit it, the third has it
        with mock.patch.dict(os.environ, {"STORY_FIND_DELAY": "0"}):
            code, out = self.run_story("show", "GS-SRV-001", sid=None)
        self.assertEqual(code, 0, out)
        self.assertIn("GS-SRV-001", out)

    def test_find_gives_up_and_says_it_may_be_lag(self):
        self.lag("GS-SRV-001", 99)
        with mock.patch.dict(os.environ, {"STORY_FIND_DELAY": "0", "STORY_FIND_RETRIES": "2"}):
            code, out = self.run_story("show", "GS-SRV-001", sid=None)
        self.assertEqual(code, 1)
        self.assertIn("no story GS-SRV-001 on the Project", out)
        self.assertIn("lag", out)

    def test_unknown_story_still_fails_fast_when_retries_are_off(self):
        with mock.patch.dict(os.environ, {"STORY_FIND_RETRIES": "0"}):
            code, out = self.run_story("show", "GS-NOPE-001", sid=None)
        self.assertEqual((code, "lag" in out), (1, True))

    def test_create_does_not_reuse_an_id_the_listing_hasnt_caught_up_on(self):
        self.as_role("pm")
        self.gh.add("GS-SRV-004", "just created", Status="draft", Lane="implementation")
        self.lag("GS-SRV-004", 99)  # on the board, not yet in the listing; the issue itself exists
        body = self.repo / "b.md"
        body.write_text("x")
        code, out = self.run_story("create", "--comp", "SRV", "--title", "next", "--lane",
                                   "implementation", "--size", "S", "--risk", "low",
                                   "--component", "server", "--body-file", str(body))
        self.assertEqual(code, 0, out)
        self.assertIn("created GS-SRV-005", out)

    def test_a_story_this_process_created_survives_a_listing_that_hasnt_caught_up(self):
        cfg = story.role.Roles(self.repo).repo
        b = story.Board(self.gh, cfg["github_repo"], cfg["project"], cfg["story_prefix"])
        body = self.repo / "b.md"
        body.write_text("x")
        a = story.parser().parse_args(["create", "--comp", "SRV", "--title", "t", "--lane", "implementation",
                                       "--size", "S", "--risk", "low", "--component", "server",
                                       "--body-file", str(body)])
        sid = re.search(r"created (\S+)", story.cmd_create(b, story.Who(story.role.UNRESTRICTED, {}), a)).group(1)
        self.lag(sid, 99)  # the listing doesn't show it yet
        for _ in range(2):  # refreshing twice must neither lose nor duplicate it
            b.refresh()
        with mock.patch.dict(os.environ, {"STORY_FIND_RETRIES": "0"}):
            self.assertEqual(b.find(sid)["id"], sid)
        self.assertEqual(len([i for i in b.items() if i["id"] == sid]), 1)

    def test_none_is_still_bounded_by_settable_fields(self):
        self.as_role("none")
        for pair in ("Status=done", "Story ID=x", "Title=y"):
            self.assertEqual(self.run_story("set", "GS-SRV-001", pair)[0], 1, pair)
        self.assertEqual(self.status("GS-SRV-001"), "ready")
        self.assertEqual(self.run_story("set", "GS-SRV-001", "Rank=7")[0], 0)  # none may set fields
        body = self.repo / "b.md"
        body.write_text("y")
        self.assertEqual(self.run_story("body", "GS-SRV-001", "--body-file", str(body))[0], 0)
        code, out = self.run_story("set", "GS-SRV-001", "Rank=abc")
        self.assertEqual(code, 1)
        self.assertIn("must be a number", out)

    def test_reopen_and_same_status(self):
        self.as_role("arch")
        self.done_records(4)
        self.run_story("status", "GS-SRV-003", "done")
        self.assertEqual(self.gh.issues[4]["state"], "closed")
        self.as_role("none")
        self.assertEqual(self.run_story("status", "GS-SRV-003", "review")[0], 0)
        self.assertEqual(self.gh.issues[4]["state"], "open")
        writes = len([c for c in self.gh.calls if c[0] != "GET"])
        code, out = self.run_story("status", "GS-SRV-003", "review")
        self.assertIn("already review", out)
        self.assertEqual(len([c for c in self.gh.calls if c[0] != "GET"]), writes)

    def test_only_permitted_roles_create_edit_and_set(self):
        self.as_role("impl")
        body = self.repo / "b.md"
        body.write_text("x")
        code, out = self.run_story("create", "--comp", "SRV", "--title", "t", "--lane", "implementation",
                                   "--size", "S", "--risk", "low", "--component", "server",
                                   "--body-file", str(body))
        self.assertEqual(code, 1)
        self.assertIn("can't create", out)
        self.assertEqual(self.run_story("set", "GS-SRV-001", "Rank=1")[0], 1)
        self.assertEqual(self.run_story("body", "GS-SRV-001", "--body-file", str(body))[0], 1)
        self.as_role("arch")
        self.assertEqual(self.run_story("body", "GS-SRV-001", "--body-file", str(body))[0], 0)
        self.assertEqual(self.gh.issues[1]["body"], "x")

    def test_moves_need_every_required_record(self):
        """#39: one refusal lists every missing record and its owner; only this round's records count."""
        self.as_role("arch")
        code, out = self.run_story("status", "GS-SRV-003", "done")
        self.assertEqual(code, 1)
        self.assertIn("missing:\n  §8 (architecture)\n  §8 instrumentation (sre)", out)
        self.assertIn("story comment GS-SRV-003 --record NAME", out)
        self.assertEqual(self.status("GS-SRV-003"), "review")
        self.assertNotIn(4, self.gh.comments)   # nothing written
        code, out = self.run_story("check", "GS-SRV-003", "done")
        self.assertEqual(code, 1)
        self.assertIn("§8 instrumentation (sre)", out)
        self.assertIn("architecture may make this move", out)
        self.record(4, "§8 instrumentation (sre)")
        code, out = self.run_story("status", "GS-SRV-003", "done")
        self.assertEqual(code, 1)
        self.assertNotIn("instrumentation", out)
        self.assertIn("§8 (architecture)", out)
        self.assertEqual(self.run_story("comment", "GS-SRV-003", "--record", "§8", "--body", "pass")[0], 0)
        code, out = self.run_story("check", "GS-SRV-003", "done")
        self.assertEqual((code, "records complete" in out), (0, True), out)
        self.assertEqual(self.run_story("status", "GS-SRV-003", "done")[0], 0)
        # reopened for rework: the next round needs fresh records
        self.as_role("none")
        self.run_story("status", "GS-SRV-003", "review")
        self.as_role("arch")
        code, out = self.run_story("status", "GS-SRV-003", "done")
        self.assertEqual(code, 1)
        self.assertIn("§8 (architecture)\n  §8 instrumentation (sre)", out)
        # draft → ready waits on SRE's observability review; the owner's record stands in
        self.assertIn("Observability review (sre)", self.run_story("status", "GS-SRV-002", "ready")[1])
        self.record(3, "Observability review (owner)")
        self.assertEqual(self.run_story("status", "GS-SRV-002", "ready")[0], 0)

    def test_pm_blocks_ready_stories_and_architecture_readies_them(self):
        """#40: the readiness scan marks a failing ready story blocked; review stories stay put."""
        self.as_role("pm")
        code, out = self.run_story("status", "GS-SRV-001", "blocked", "--note", "AC 3 needs make chaos-drill; sre clears it")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status("GS-SRV-001"), "blocked")
        self.assertEqual(self.run_story("status", "GS-SRV-003", "blocked")[0], 1)   # review: never moved
        self.assertEqual(self.run_story("status", "GS-SRV-001", "ready")[0], 1)     # unblocking is architecture's
        self.as_role("arch")
        self.assertEqual(self.run_story("status", "GS-SRV-001", "ready")[0], 0)

    def test_unrestricted_moves_without_records_with_a_warning(self):
        self.as_role("none")
        code, out = self.run_story("status", "GS-SRV-003", "done")
        self.assertEqual(code, 0, out)
        self.assertIn("warning: moved without §8 (architecture), §8 instrumentation (sre)", out)
        self.assertEqual(self.status("GS-SRV-003"), "done")

    def test_check_reads_without_a_role_and_names_who_may_move(self):
        code, out = self.run_story("check", "GS-SRV-003", "done", sid=None)
        self.assertEqual(code, 1)
        self.assertIn("no role can't make this move", out)
        self.as_role("impl")
        self.done_records(4)
        code, out = self.run_story("check", "GS-SRV-003", "done")
        self.assertEqual(code, 0, out)
        self.assertIn("implementation can't make this move", out)
        self.assertEqual(self.run_story("check", "GS-SRV-003", "bogus")[0], 1)
        self.assertNotIn(("PATCH",), {c[:1] for c in self.gh.calls if c[0] != "GET"})

    def test_a_malformed_records_entry_names_the_role(self):
        sre = self.repo / ".claude/roles/sre.md"
        sre.write_text(sre.read_text().replace("review>done: §8 instrumentation", "review>done"))
        code, out = self.run_story("check", "GS-SRV-003", "done", sid=None)
        self.assertEqual(code, 1)
        self.assertIn("role sre: records entry 'review>done'", out)

    def test_story_done_on_merge_needs_the_records_too(self):
        self.gh.add("GS-SRV-009", "reviewed", Status="review", Lane="implementation")   # #6
        self.gh.pr(45, ["role:architecture"], body="Story-Done: GS-SRV-009")
        code, out = self.run_story("merged", "45", sid=None)
        self.assertEqual(code, 1)
        self.assertIn("GS-SRV-009 can't move review → done; missing:", out)
        self.assertEqual(self.status("GS-SRV-009"), "review")

    def test_comments_have_role_headings(self):
        self.as_role("sre")
        self.assertEqual(self.run_story("comment", "GS-SRV-001", "--to", "architecture",
                                        "--body", "is the metric right?")[0], 0)
        self.assertEqual(self.gh.comments[1][-1],
                         "**For architecture:** is the metric right?\n\n<!-- from: sre -->")
        self.assertEqual(self.run_story("comment", "GS-SRV-001", "--record", "§8 instrumentation",
                                        "--body", "line1\nline2")[0], 0)
        self.assertEqual(self.gh.comments[1][-1],
                         "**§8 instrumentation (sre):**\n\nline1\nline2\n\n<!-- from: sre -->")
        self.assertEqual(self.run_story("comment", "GS-SRV-001", "--body", "x")[0], 1)  # no heading
        code, out = self.run_story("comment", "GS-SRV-001", "--to", "architecture", "--record", "§8",
                                   "--body", "x")
        self.assertEqual(code, 1)
        self.assertIn("then hand off with a second `story comment GS-SRV-001 --to ROLE`", out)

    def test_a_role_alias_in_to_is_written_as_the_role_name(self):
        self.as_role("sre")
        self.assertEqual(self.run_story("comment", "GS-SRV-001", "--to", "impl", "--body", "check")[0], 0)
        self.assertTrue(self.gh.comments[1][-1].startswith("**For implementation:** check"))
        self.assertEqual(self.run_story("comment", "GS-SRV-001", "--to", "owner", "--body", "hi")[0], 0)
        self.assertTrue(self.gh.comments[1][-1].startswith("**For owner:** hi"))   # not a role: as written

    def test_no_role_refuses_writes_and_none_allows(self):
        code, out = self.run_story("start", "GS-SRV-001", sid=None)
        self.assertEqual(code, 1)
        self.assertIn("no role set", out)
        self.assertNotIn("coordinator comment", out)
        code, out = self.run_story("comment", "GS-SRV-001", "--to", "arch", "--body", "q", sid=None)
        self.assertEqual(code, 1)
        self.assertIn("posts with `.claude/bin/coordinator comment <ID> --to <role>`", out)   # the dispatcher's way
        self.as_role("none")
        self.assertEqual(self.run_story("status", "GS-SRV-002", "ready")[0], 0)

    def test_writes_wait_for_the_cutover(self):
        repo_md = self.repo / ".claude/roles/_repo.md"
        repo_md.write_text(repo_md.read_text().replace("stories: github\n", ""))
        self.as_role("impl")
        code, out = self.run_story("start", "GS-SRV-001")
        self.assertEqual(code, 2)
        self.assertIn("still live in docs/stories", out)
        self.assertEqual(self.status("GS-SRV-001"), "ready")
        self.assertEqual(self.run_story("list")[0], 0)

    def test_session_id_from_environment(self):
        self.as_role("impl")
        os.environ["CLAUDE_SESSION_ID"] = SID
        try:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = story.main(["start", "GS-SRV-001"], gh=self.gh)
        finally:
            del os.environ["CLAUDE_SESSION_ID"]
        self.assertEqual(code, 0, err.getvalue())


class MergedTest(unittest.TestCase):
    """story merged: the story-merge Action applies a PR's Story: lines as its role label."""

    run_story, as_role, status, tearDown = (StoryTest.run_story, StoryTest.as_role,
                                            StoryTest.status, StoryTest.tearDown)

    def setUp(self):
        StoryTest.setUp(self)
        self.gh.add("GS-SRV-004", "being built", Status="in-progress", Lane="implementation")  # #6
        self.gh.add("GS-INF-002", "infra built", Status="in-progress", Lane="sre")  # #7

    def merged(self, n):
        return self.run_story("merged", str(n), sid=None)

    def test_lane_pr_moves_its_story_to_review(self):
        self.gh.pr(40, ["role:implementation"], body="Does the thing.",
                   commits=["feat: x\n\nStory: GS-SRV-004", "fix: y\n\nStory: GS-SRV-004"])
        code, out = self.merged(40)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status("GS-SRV-004"), "review")
        self.assertIn("**Status (implementation):** in-progress → review. PR #40 merged", self.gh.comments[6])
        self.assertEqual(len(self.gh.comments[6]), 1)  # named twice, moved once
        self.assertEqual(self.merged(40)[0], 0)  # a re-run changes nothing
        self.assertEqual(len(self.gh.comments[6]), 1)

    def test_other_roles_only_reference_and_done_needs_the_right(self):
        self.gh.comments[4] = ["**§8 instrumentation (sre):** ok", "**§8 (architecture):** ok"]
        self.gh.pr(41, ["role:architecture"], body="§8 pass\n\nStory: GS-SRV-004\nStory-Done: GS-SRV-003")
        code, out = self.merged(41)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status("GS-SRV-004"), "in-progress")  # impl's lane: a reference
        self.assertEqual(self.status("GS-SRV-003"), "done")
        self.assertEqual(self.gh.issues[4]["state"], "closed")
        self.gh.pr(42, ["role:sre"], body="Story-Done: GS-SRV-002\nStory: GS-INF-002")
        code, out = self.merged(42)
        self.assertEqual(code, 1)
        self.assertIn("can't close it with Story-Done", out)
        self.assertEqual(self.status("GS-INF-002"), "review")  # the rest still applied

    def test_lane_story_not_in_progress_is_a_defect(self):
        self.gh.pr(43, ["role:implementation"], body="Story: GS-SRV-001")  # still ready
        code, out = self.merged(43)
        self.assertEqual(code, 1)
        self.assertIn("GS-SRV-001 is ready in lane implementation", out)

    def test_needs_one_role_label_and_a_merge(self):
        self.gh.pr(44, [], body="Story: GS-SRV-004")
        self.assertEqual(self.merged(44)[0], 1)
        self.gh.pr(45, ["role:implementation", "role:sre"], body="Story: GS-SRV-004")
        self.assertIn("exactly one role:", self.merged(45)[1])
        self.gh.pr(46, ["role:implementation"], body="Story: GS-SRV-004", merged=False)
        self.assertIn("isn't merged", self.merged(46)[1])
        self.gh.pr(49, ["role:implementation"], body="Story: GS-SRV-004", base="release-1")
        code, out = self.merged(49)
        self.assertEqual(code, 0, out)
        self.assertIn("merged into release-1, not main", out)
        self.gh.pr(47, ["role:pm"], body="no stories here")
        self.assertEqual(self.merged(47)[0], 0)
        self.assertEqual(self.status("GS-SRV-004"), "in-progress")

    def test_a_session_cannot_make_merge_moves(self):
        self.as_role("impl")
        code, out = self.run_story("status", "GS-SRV-004", "review")
        self.assertEqual(code, 1, out)
        self.assertEqual(self.status("GS-SRV-004"), "in-progress")

    def test_skips_before_the_cutover(self):
        repo_md = self.repo / ".claude/roles/_repo.md"
        repo_md.write_text(repo_md.read_text().replace("stories: github\n", ""))
        self.gh.pr(48, ["role:implementation"], body="Story: GS-SRV-004")
        code, out = self.merged(48)
        self.assertEqual(code, 0, out)
        self.assertIn("Skipped", out)
        self.assertEqual(self.status("GS-SRV-004"), "in-progress")


    def test_skips_without_a_project(self):
        # brand-kit#2 Codex review: a repo with no Project (brand-kit) got exit 2
        # on every merge, because the project check ran before the skip.
        repo_md = self.repo / ".claude/roles/_repo.md"
        text = repo_md.read_text()
        repo_md.write_text(re.sub(r"(?m)^(project|stories|github_repo|story_prefix):.*\n", "", text))
        self.gh.pr(50, ["role:implementation"], body="Story: GS-SRV-004")
        code, out = self.merged(50)
        self.assertEqual(code, 0, out)
        self.assertIn("Skipped", out)


class SprintTest(unittest.TestCase):
    """story sprint: sprints as issues, and the Sprint field as a number."""

    run_story, as_role, tearDown = StoryTest.run_story, StoryTest.as_role, StoryTest.tearDown

    def setUp(self):
        StoryTest.setUp(self)
        repo_md = self.repo / ".claude/roles/_repo.md"
        repo_md.write_text(repo_md.read_text().replace("stories: github\n",
                                                       "stories: github\nsprints: project\n"))
        f = next(f for f in self.gh.fields if f["name"] == "Sprint")
        f["data_type"] = "number"
        del f["options"]
        for it in self.gh.items.values():  # the fixtures' SPRINT-05 becomes 5
            if it["fields"].get("Sprint"):
                it["fields"]["Sprint"] = 5.0
        self.body = self.repo / "s.md"
        self.body.write_text("## Demo goal\nkill -9 on dev\n")

    def sprint(self, *args):
        return self.run_story("sprint", *args)

    def labels(self, n):
        return sorted(l["name"] for l in self.gh.issues[n].get("labels", []))

    def test_plan_activate_close_in_order(self):
        self.as_role("pm")
        code, out = self.sprint("plan", "1", "--title", "M2 gate", "--body-file", str(self.body))
        self.assertEqual(code, 0, out)
        n = max(self.gh.issues)
        self.assertEqual(self.gh.issues[n]["title"], "SPRINT-01 — M2 gate")
        self.assertEqual(self.labels(n), ["sprint", "sprint:planned"])
        self.assertIn("already exists", self.sprint("plan", "1", "--title", "x", "--body", "y")[1])
        self.assertIn("close it first", self.sprint("plan", "2", "--title", "x", "--body", "y")[1])
        self.assertIn("only the active", self.sprint("close", "1")[1])
        self.assertEqual(self.sprint("activate", "1")[0], 0)
        self.assertEqual(self.labels(n), ["sprint", "sprint:active"])
        self.assertIn("close it first", self.sprint("plan", "2", "--title", "x", "--body", "y")[1])
        self.assertEqual(self.sprint("body", "1", "--body", "new goal")[0], 0)
        self.assertEqual(self.gh.issues[n]["body"], "new goal")
        self.assertEqual(self.sprint("close", "1", "--note", "demo ran")[0], 0)
        self.assertEqual(self.gh.issues[n]["state"], "closed")
        self.assertEqual(self.labels(n), ["sprint"])
        self.assertIn("**Sprint (pm):** active → closed. demo ran", self.gh.comments[n])
        self.assertIn("not SPRINT-03", self.sprint("plan", "3", "--title", "x", "--body", "y")[1])
        self.assertEqual(self.sprint("plan", "2", "--title", "next", "--body", "y")[0], 0)

    def test_only_sprint_editors_and_only_after_the_switch(self):
        self.as_role("impl")
        self.assertIn("can't manage sprints", self.sprint("plan", "1", "--title", "x", "--body", "y")[1])
        self.as_role("pm")
        repo_md = self.repo / ".claude/roles/_repo.md"
        repo_md.write_text(repo_md.read_text().replace("sprints: project\n", ""))
        self.assertIn("still files", self.sprint("plan", "1", "--title", "x", "--body", "y")[1])

    def test_number_sprint_field(self):
        self.as_role("pm")
        self.assertEqual(self.run_story("set", "GS-SRV-002", "Sprint=SPRINT-06")[0], 0)
        self.assertEqual(self.run_story("set", "GS-SRV-003", "Sprint=6")[0], 0)
        for want in ("6", "06", "SPRINT-06"):
            out = self.run_story("list", "--sprint", want, sid=None)[1]
            self.assertEqual([l.split()[1] for l in out.strip().splitlines()], ["GS-SRV-002", "GS-SRV-003"], want)
        out = self.run_story("list", "--sprint", "5", sid=None)[1]
        self.assertEqual([l.split()[1] for l in out.strip().splitlines()], ["GS-INF-001", "GS-SRV-001"])
        self.assertEqual(self.run_story("set", "GS-SRV-002", "Sprint=next")[0], 1)


class ContentBoardTest(unittest.TestCase):
    """The content repo shares the server's Project: its own prefix, components, lanes, Cycle."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "content"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        r = subprocess.run([sys.executable, str(TOOLKIT / "install.py"), "--root", str(FIXTURE), "game-content",
                            str(self.repo)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.old = os.getcwd()
        os.chdir(self.repo)
        self.gh = FakeGH()
        self.gh.add("GS-SRV-001", "server thing", Status="ready", Lane="implementation")
        self.gh.add("GC-CAN-001", "settlement identity", repo=CONTENT, Status="in-progress",
                    Lane="world-builder", Cycle=1.0, Rank=1, Plan="Current")
        self.gh.add("GC-TXT-001", "commons room text", repo=CONTENT, Status="ready", Lane="director",
                    Cycle=1.0, Rank=2, Plan="Current")
        self.gh.add("GC-PCK-001", "commons aw", repo=CONTENT, Status="review", Lane="content")

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    run_story = StoryTest.run_story
    as_role = StoryTest.as_role
    status = StoryTest.status

    def test_lists_own_prefix_and_reads_the_other_repo(self):
        code, out = self.run_story("list", sid=None)
        self.assertEqual(code, 0, out)
        self.assertEqual([l.split()[1] for l in out.strip().splitlines()],
                         ["GC-CAN-001", "GC-TXT-001", "GC-PCK-001"])
        out = self.run_story("list", "--cycle", "01", sid=None)[1]
        self.assertEqual([l.split()[1] for l in out.strip().splitlines()], ["GC-CAN-001", "GC-TXT-001"])
        out = self.run_story("list", "--prefix", "GS", sid=None)[1]
        self.assertEqual([l.split()[1] for l in out.strip().splitlines()], ["GS-SRV-001"])
        code, out = self.run_story("show", "GS-SRV-001", sid=None)  # read from the server repo
        self.assertEqual(code, 0, out)
        self.assertIn("Cycle: -", out)

    def test_producer_creates_with_content_components_and_cross_repo_depends(self):
        self.as_role("producer")
        body = self.repo / "b.md"
        body.write_text("## Why\nC1 -> M3\n")
        args = ["create", "--title", "commons sheet", "--lane", "content", "--size", "S",
                "--risk", "low", "--component", "pack", "--body-file", str(body)]
        code, out = self.run_story(*args, "--comp", "SRV")
        self.assertEqual(code, 1)
        self.assertIn("--comp must be one of CAN, DES, ART, TXT, PCK", out)
        code, out = self.run_story(*args, "--comp", "pck", "--depends", "GS-SRV-001")
        self.assertEqual(code, 0, out)
        self.assertIn("created GC-PCK-002", out)
        new_n = max(self.gh.issues)
        self.assertEqual(self.gh.issues[new_n]["repo"], CONTENT)
        self.assertIn((new_n, "/dependencies/blocked_by", self.gh.issues[1]["id"]), self.gh.links)
        self.assertEqual(self.run_story("status", "GC-PCK-002", "ready")[0], 0)
        self.assertEqual(self.run_story("set", "GC-PCK-002", "Cycle=2", "Plan=Next", "Rank=1")[0], 0)
        self.assertEqual(self.status("GC-PCK-002"), "ready")
        self.assertEqual(self.run_story("status", "GC-PCK-001", "done")[0], 0)  # closes on evidence
        self.assertEqual(self.gh.issues[4]["state"], "closed")

    def test_producer_moves_owners_lane_only(self):
        self.as_role("producer")
        self.assertEqual(self.run_story("start", "GC-TXT-001")[0], 0)  # @director
        self.assertEqual(self.run_story("status", "GC-TXT-001", "review")[0], 0)
        code, out = self.run_story("status", "GC-CAN-001", "review")  # world-builder's own move
        self.assertEqual(code, 1, out)

    def test_never_writes_the_other_repos_stories(self):
        for name in ("producer", "none"):
            self.as_role(name)
            code, out = self.run_story("set", "GS-SRV-001", "Rank=1")
            self.assertEqual(code, 1, (name, out))
            self.assertIn("belongs to example/game-server", out)
            self.assertEqual(self.run_story("comment", "GS-SRV-001", "--to", "pm", "--body", "x")[0], 1)

    def test_lanes_move_their_own_work(self):
        self.as_role("world-builder")
        self.assertEqual(self.run_story("status", "GC-CAN-001", "review", "--note", "notion url")[0], 0)
        self.assertEqual(self.run_story("status", "GC-CAN-001", "done")[0], 1)  # producer's
        self.as_role("content")
        self.assertEqual(self.run_story("start", "GC-TXT-001")[0], 1)  # director lane


class GHTest(unittest.TestCase):
    """The real GH.api, against a stub `gh` that records its argv and stdin."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.log = d / "log"
        (d / "gh").write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> {self.log}\n'
            f'readlink /proc/self/fd/0 >> {self.log}\n'
            f'cat >> {self.log}\n'
            'case "$*" in *--paginate*) echo \'[[{"a":1},{"a":2}],[{"a":3}]]\' ;; *) echo \'{"ok":1}\' ;; esac\n')
        (d / "gh").chmod(0o755)
        self.path = os.environ["PATH"]
        os.environ["PATH"] = f"{d}:{self.path}"

    def tearDown(self):
        os.environ["PATH"] = self.path
        self.tmp.cleanup()

    def test_paginate_flattens_pages(self):
        out = story.GH().api("GET", "/x", paginate=True)
        self.assertEqual(out, [{"a": 1}, {"a": 2}, {"a": 3}])
        self.assertIn("api -X GET /x -H Accept: application/vnd.github+json --paginate --slurp",
                      self.log.read_text())

    def test_gh_never_inherits_stdin(self):
        from unittest import mock
        real = story.subprocess.run
        seen = []
        with mock.patch.object(story.subprocess, "run",
                               side_effect=lambda *a, **k: (seen.append(k), real(*a, **k))[1]):
            story.GH().api("GET", "/x")
            story.GH().api("PATCH", "/y", {"a": 1})
        self.assertIs(seen[0].get("stdin"), story.subprocess.DEVNULL)  # no body: no stdin at all
        self.assertEqual(seen[1].get("input"), '{"a": 1}')            # body: JSON on stdin

    def test_patch_sends_json_on_stdin(self):
        self.assertEqual(story.GH().api("PATCH", "/y", {"fields": [1]}), {"ok": 1})
        log = self.log.read_text()
        self.assertIn("api -X PATCH /y", log)
        self.assertIn("--input -", log)
        self.assertIn('{"fields": [1]}', log)


if __name__ == "__main__":
    unittest.main()
