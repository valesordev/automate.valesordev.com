"""sprint-state: derive sprint numbers and state from docs/sprints/ on a git ref."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "plugins/valesor-dev/bin/sprint-state"


def sh(*cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


class SprintStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        sh("git", "init", "-q", "-b", "main", cwd=self.repo)
        (self.repo / "README.md").write_text("x\n")
        self.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self):
        sh("git", "add", "-A", cwd=self.repo)
        sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c", cwd=self.repo)

    def sprints(self, **files):
        d = self.repo / "docs/sprints"
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*"):
            f.unlink()
        for fname, status in files.items():
            body = f"# {fname}\n" + (f"Status: {status}\nDates: 2026-09-01 →\n" if status else "")
            (d / f"{fname.replace('_', '-')}.md").write_text(body)
        self.commit()

    def state(self, ref="main"):
        r = sh(sys.executable, str(TOOL), "--ref", ref, cwd=self.repo)
        fields = dict(line.split(":", 1) for line in r.stdout.splitlines() if ":" in line)
        return r.returncode, {k.strip(): v.strip() for k, v in fields.items()}, r

    def test_first(self):
        code, f, _ = self.state()
        self.assertEqual((code, f["state"], f["next"]), (0, "first", "SPRINT-01"))

    def test_active_uses_current_number_not_01(self):
        self.sprints(SPRINT_01="closed", SPRINT_01_demo=None, SPRINT_02="active")
        code, f, _ = self.state()
        self.assertEqual((code, f["state"], f["current"], f["next"]), (0, "active", "SPRINT-02", "SPRINT-03"))

    def test_closed_without_successor(self):
        self.sprints(SPRINT_01="closed", SPRINT_01_demo=None, SPRINT_02="closed", SPRINT_02_demo=None)
        code, f, _ = self.state()
        self.assertEqual((code, f["state"], f["current"], f["next"]), (0, "closed", "SPRINT-02", "SPRINT-03"))

    def test_planned(self):
        self.sprints(SPRINT_01="closed", SPRINT_02="planned")
        code, f, _ = self.state()
        self.assertEqual((code, f["state"], f["next"]), (0, "planned", "SPRINT-02"))

    def test_padding_rolls_over(self):
        self.sprints(SPRINT_08="closed", SPRINT_09="active")
        self.assertEqual(self.state()[1]["next"], "SPRINT-10")
        self.sprints(SPRINT_99="active")
        self.assertEqual(self.state()[1]["next"], "SPRINT-100")

    def test_demo_files_are_ignored(self):
        self.sprints(SPRINT_01="closed", SPRINT_01_demo="active")
        code, f, _ = self.state()
        self.assertEqual((code, f["state"]), (0, "closed"))

    def test_invalid_states(self):
        for files in ({"SPRINT_01": "active", "SPRINT_02": "active"},
                      {"SPRINT_01": "active", "SPRINT_02": "planned"},
                      {"SPRINT_01": "closed", "SPRINT_02": "done"},
                      {"SPRINT_01": None}):
            self.sprints(**files)
            code, f, r = self.state()
            self.assertEqual((code, f["state"]), (1, "invalid"), (files, r.stdout))

    def test_reads_the_ref_not_the_worktree(self):
        self.sprints(SPRINT_01="active")
        (self.repo / "docs/sprints/SPRINT-01.md").write_text("Status: closed\n")  # uncommitted
        self.assertEqual(self.state()[1]["state"], "active")

    def test_named_cycles_in_another_dir(self):
        d = self.repo / "docs/planning/cycles"
        d.mkdir(parents=True)
        (d / "CYCLE-01.md").write_text("# CYCLE-01\nStatus: closed\n")
        (d / "CYCLE-02.md").write_text("# CYCLE-02\nStatus: active\n")
        self.sprints(SPRINT_07="active")  # the server's files don't count
        r = sh(sys.executable, str(TOOL), "--ref", "main", "--dir", "docs/planning/cycles",
               "--name", "cycle", cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("state:   active", r.stdout)
        self.assertIn("current: CYCLE-02", r.stdout)
        self.assertIn("next:    CYCLE-03", r.stdout)

    def test_missing_ref(self):
        code, _, r = self.state("origin/main")
        self.assertEqual(code, 2)
        self.assertIn("git fetch", r.stderr)


class ProjectSourceTest(unittest.TestCase):
    """sprint-state reads sprint issues when _repo.md says `sprints: project`."""

    def setUp(self):
        import json, os
        self.json, self.os = json, os
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        sh("git", "init", "-q", "-b", "main", cwd=self.repo)
        (self.repo / ".claude/roles").mkdir(parents=True)
        (self.repo / ".claude/roles/_repo.md").write_text(
            "---\ngithub_repo: example/game-server\nsprints: project\n---\n")
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        self.data = Path(self.tmp.name) / "issues.json"
        (self.bin / "gh").write_text(f"#!/bin/sh\necho \"$*\" > {self.tmp.name}/argv\ncat {self.data}\n")
        (self.bin / "gh").chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def issues(self, *rows):
        out = []
        for i, (title, state, labels) in enumerate(rows, 1):
            out.append({"number": 100 + i, "title": title, "state": state,
                        "labels": [{"name": l} for l in ["sprint", *labels]]})
        out.append({"number": 99, "title": "SPRINT-09 — a PR", "state": "open", "labels": [],
                    "pull_request": {}})
        self.data.write_text(self.json.dumps([out]))  # --slurp pages

    def state(self, *args):
        env = {**self.os.environ, "PATH": f"{self.bin}:{self.os.environ['PATH']}"}
        r = subprocess.run([sys.executable, str(TOOL), *args], cwd=self.repo, env=env,
                           capture_output=True, text=True)
        fields = dict(line.split(":", 1) for line in r.stdout.splitlines() if ":" in line)
        return r.returncode, {k.strip(): v.strip() for k, v in fields.items()}, r

    def test_active_from_labels_and_closed_issues(self):
        self.issues(("SPRINT-04 — m2", "closed", []), ("SPRINT-05 — m2 on dev", "open", ["sprint:active"]))
        code, f, r = self.state()
        self.assertEqual((code, f["state"], f["current"], f["next"]), (0, "active", "SPRINT-05", "SPRINT-06"), r)
        self.assertIn("SPRINT-05    active  (#102)", r.stdout)
        self.assertIn("labels=sprint", (Path(self.tmp.name) / "argv").read_text())

    def test_planned_first_and_invalid(self):
        self.issues()
        self.assertEqual(self.state()[1]["state"], "first")
        self.issues(("SPRINT-01 — a", "closed", []), ("SPRINT-02 — b", "open", ["sprint:planned"]))
        self.assertEqual(self.state()[1]["next"], "SPRINT-02")
        for rows in ([("SPRINT-01 — a", "open", ["sprint:active"]), ("SPRINT-02 — b", "open", ["sprint:planned"])],
                     [("SPRINT-01 — a", "open", [])],
                     [("SPRINT-01 — a", "open", ["sprint:active", "sprint:planned"])]):
            self.issues(*rows)
            code, f, r = self.state()
            self.assertEqual((code, f["state"]), (1, "invalid"), rows)

    def test_files_flag_overrides_and_github_errors(self):
        self.assertEqual(self.state("--source", "files")[0], 2)  # no origin/main: reads files
        (self.bin / "gh").write_text("#!/bin/sh\necho 'HTTP 404' >&2\nexit 1\n")
        code, _, r = self.state()
        self.assertEqual(code, 2)
        self.assertIn("can't read sprint issues", r.stderr)


if __name__ == "__main__":
    unittest.main()
