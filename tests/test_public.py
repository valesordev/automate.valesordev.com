"""This repo is public: no private Notion links anywhere, and docs/ exports to the docs site cleanly."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


class PublicTest(unittest.TestCase):
    def test_no_private_notion_links(self):
        # The full gate (denylist, gitleaks) is `make check-public`; this part needs neither.
        r = run(sys.executable, "toolkit/check_public.py", "--root", str(ROOT), "--no-denylist", "--no-gitleaks")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_docs_export_cleanly(self):
        with tempfile.TemporaryDirectory() as site:
            r = run(sys.executable, "toolkit/export_docs.py", "--root", str(ROOT), "--repo",
                    "valesordev/automate.valesordev.com", "--dry-run", site)
        if "uncommitted" in r.stderr:
            self.skipTest("docs/ has uncommitted changes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would write  content/automate.valesordev.com/README.md", r.stdout)


if __name__ == "__main__":
    unittest.main()
