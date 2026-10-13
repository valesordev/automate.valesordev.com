"""bin/codex-review: status-table parsing, wait/timeout, re-review request (no network)."""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
BOT = "chatgpt-codex-connector[bot]"
SHA = "66e51d609d55f950fe9c484f1294f5fd174de21d"


def load():
    path = str(ROOT / "plugins/valesor-dev/bin/codex-review")
    loader = importlib.machinery.SourceFileLoader("codex_review", path)
    spec = importlib.util.spec_from_loader("codex_review", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def status(*rows):
    body = ("<!-- codex-pull-request-review-summary -->\n\n## Codex Review Summary\n\n"
            "| Review | Status | Commit | Review trigger |\n| --- | --- | --- | --- |\n")
    for st, commit, trig, *kind in rows:
        body += f"| 📝 **{kind[0] if kind else 'Code Review'}** | {st} | `{commit}` | {trig} |\n"
    return {"user": {"login": BOT}, "body": body}


DONE = '✅ **Completed** <relative-time datetime="2026-10-09T11:54:15Z">x</relative-time>'
RUNNING = "⏳ **In progress**"


class Gh:
    """Scripted GitHub: `conv` is a list of snapshots of the issue comments, one per poll."""

    def __init__(self, conv, inline=(), reviews=()):
        self.conv, self.inline, self.reviews = list(conv), list(inline), list(reviews)
        self.posted, self.polls = [], 0

    def api(self, path, *extra):
        if path.endswith("/issues/7/comments"):
            snap = self.conv[min(self.polls, len(self.conv) - 1)]
            self.polls += 1
            return snap
        if path.endswith("/pulls/7/comments"):
            return self.inline
        if path.endswith("/pulls/7/reviews"):
            return self.reviews
        raise AssertionError(path)

    def comment(self, pr, body):
        self.posted.append((pr, body))


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class CodexReviewTest(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def use(self, gh):
        self.m.gh_api, self.m.gh_post_comment = gh.api, gh.comment
        return gh

    def wait(self, gh, **kw):
        clk = Clock()
        args = dict(timeout=600, interval=30, grace=120, request=True)
        args.update(kw)
        return self.m.wait(7, SHA, sleep=clk.sleep, clock=clk.now, **args)

    def test_rows(self):
        c = [status((DONE, "66e51d6", "PR opened"), (RUNNING, "abc1234", "review"))]
        self.assertEqual(self.m.rows_for(c, SHA), ["completed"])
        self.assertEqual(self.m.rows_for(c, "abc1234" + "0" * 33), ["pending"])
        self.assertEqual(self.m.rows_for(c, "ffff" * 10), [])

    def test_ignores_other_authors_and_unmarked(self):
        spoof = status((DONE, "66e51d6", "x"))
        spoof["user"]["login"] = "someone"
        unmarked = {"user": {"login": BOT}, "body": "| a | b | `66e51d6` | c |"}
        self.assertEqual(self.m.rows_for([spoof, unmarked], SHA), [])

    def test_failed_row_wins_over_completed(self):
        c = [status((DONE, "66e51d6", "a"), ("❌ **Failed**", "66e51d6", "b"))]
        self.assertEqual(self.m.state_of(self.m.rows_for(c, SHA)), "failed")

    def test_completed_after_pending_polls(self):
        gh = self.use(Gh([[status((RUNNING, "66e51d6", "x"))], [status((DONE, "66e51d6", "x"))]]))
        r = self.wait(gh)
        self.assertEqual((r["state"], r["requested"], gh.posted), ("completed", False, []))

    def test_findings_are_codex_inline_comments_on_sha_only(self):
        mk = lambda i, who, sha, reply=None: {"id": i, "user": {"login": who}, "commit_id": sha,
                                              "in_reply_to_id": reply, "path": "a.py", "line": 3,
                                              "html_url": f"u{i}", "body": "P2"}
        gh = self.use(Gh([[status((DONE, "66e51d6", "x"))]], inline=[
            mk(1, BOT, SHA), mk(2, BOT, "0" * 40), mk(3, "owner", SHA), mk(4, BOT, SHA, reply=1)],
            reviews=[{"id": 9, "state": "COMMENTED", "user": {"login": BOT}, "commit_id": SHA}]))
        r = self.wait(gh)
        self.assertEqual([f["id"] for f in r["findings"]], [1])
        self.assertEqual(r["reviews"], [{"id": 9, "state": "COMMENTED"}])

    def test_handled_threads_are_not_findings(self):
        mk = lambda i, reply=None, body="P2", who=BOT: {
            "id": i, "user": {"login": who}, "commit_id": SHA, "in_reply_to_id": reply,
            "path": "a.py", "line": 3, "html_url": f"u{i}", "body": body}
        inline = [mk(1), mk(2), mk(3), mk(4),
                  mk(11, reply=1, body="**pr-comments:** disputed: test passes under mutation", who="owner"),
                  mk(12, reply=2, body="thanks, looking", who="owner"),
                  mk(13, reply=3, body="**pr-comments:** fixed in abc1234", who=BOT)]
        gh = self.use(Gh([[status((DONE, "66e51d6", "x"))]], inline=inline))
        r = self.wait(gh)
        # 1 is handled; 2 has a human reply without the tag; 3's tag came from the bot, not us; 4 is untouched
        self.assertEqual([f["id"] for f in r["findings"]], [2, 3, 4])
        self.assertEqual(r["handled"], 1)   # the coordinator shows these to the owner: a reply isn't a fix

    def test_only_code_review_rows_count(self):
        c = [status((DONE, "66e51d6", "x", "Security Review"))]
        self.assertEqual(self.m.rows_for(c, SHA), [])
        gh = self.use(Gh([c]))
        r = self.wait(gh, timeout=150)
        self.assertEqual((r["state"], gh.posted), ("timeout", [(7, "@codex review")]))
        c = [status((DONE, "66e51d6", "x", "Security Review"), (RUNNING, "66e51d6", "y"))]
        self.assertEqual(self.m.state_of(self.m.rows_for(c, SHA)), "pending")

    def test_later_reply_reopens_a_handled_thread(self):
        mk = lambda i, reply=None, body="P2", who=BOT: {
            "id": i, "user": {"login": who}, "commit_id": SHA, "in_reply_to_id": reply,
            "path": "a.py", "line": 3, "html_url": f"u{i}", "body": body}
        tag = "**pr-comments:** fixed in abc1234"
        inline = [mk(1), mk(2),
                  mk(11, reply=1, body=tag, who="owner"),
                  mk(12, reply=1, body="still broken", who=BOT),
                  mk(21, reply=2, body="looking", who=BOT),
                  mk(22, reply=2, body=tag, who="owner")]
        gh = self.use(Gh([[status((DONE, "66e51d6", "x"))]], inline=inline))
        self.assertEqual([f["id"] for f in self.wait(gh)["findings"]], [1])

    def test_requests_review_once_when_no_row_after_grace(self):
        gh = self.use(Gh([[], [], [], [], [], [status((DONE, "66e51d6", "x"))]]))
        r = self.wait(gh)
        self.assertEqual(gh.posted, [(7, "@codex review")])
        self.assertEqual((r["state"], r["requested"]), ("completed", True))

    def test_no_request_flag(self):
        gh = self.use(Gh([[]]))
        r = self.wait(gh, request=False, timeout=300)
        self.assertEqual((r["state"], gh.posted), ("timeout", []))

    def test_timeout_when_pending_forever(self):
        gh = self.use(Gh([[status((RUNNING, "66e51d6", "x"))]]))
        r = self.wait(gh, timeout=90)
        self.assertEqual((r["state"], gh.posted), ("timeout", []))
        self.assertLessEqual(gh.polls, 4)

    def test_a_coordinator_lane_never_asks_codex_itself(self):
        # a lane is the GitHub App, which Codex won't review for; the coordinator asks as the person
        argv = ["wait", "7", "--sha", SHA, "--timeout", "0", "--interval", "1", "--grace", "0"]
        for lane, want in (("implementation-aw-1", []), ("", [(7, "@codex review")])):
            gh = self.use(Gh([[]]))
            with mock.patch.dict(os.environ, {"COORDINATOR_LANE": lane}), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.m.main(argv)
            self.assertEqual(gh.posted, want, lane)

    LIMIT = {"user": {"login": BOT}, "body": "You have reached your Codex usage limits for code reviews. "
                                             "You can see your limits in the Codex usage dashboard."}
    ASK = {"user": {"login": "solo-seven"}, "body": "@codex review"}

    def test_a_usage_limit_reply_to_the_latest_request_is_limited(self):
        gh = self.use(Gh([[self.ASK, self.LIMIT]]))
        self.assertEqual(self.m.snapshot(7, SHA)["state"], "limited")
        gh = self.use(Gh([[self.ASK, self.LIMIT, self.ASK]]))      # asked again since: not answered yet
        self.assertEqual(self.m.snapshot(7, SHA)["state"], "none")
        gh = self.use(Gh([[self.ASK, self.LIMIT, status((DONE, "66e51d6", "x"))]]))   # a row wins
        self.assertEqual(self.m.snapshot(7, SHA)["state"], "completed")
        spoof = {"user": {"login": "someone"}, "body": self.LIMIT["body"]}
        gh = self.use(Gh([[self.ASK, spoof]]))
        self.assertEqual(self.m.snapshot(7, SHA)["state"], "none")

    def test_wait_stops_at_a_usage_limit(self):
        gh = self.use(Gh([[], [], [], [], [], [self.ASK, self.LIMIT]]))
        r = self.wait(gh)
        self.assertEqual(r["state"], "limited")
        self.assertEqual(gh.posted, [(7, "@codex review")])    # asked once, then not again
        self.assertLessEqual(gh.polls, 7)

    def test_never_requests_while_a_row_exists(self):
        gh = self.use(Gh([[status((RUNNING, "66e51d6", "x"))]]))
        self.wait(gh, timeout=400)
        self.assertEqual(gh.posted, [])


if __name__ == "__main__":
    unittest.main()
