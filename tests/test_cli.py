"""CLI robustness tests (no Android SDK required)."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr

from portagent.cli import main


class MissingOracleTests(unittest.TestCase):
    def test_missing_oracle_is_a_clean_error(self):
        err = io.StringIO()
        with redirect_stderr(err):
            rc = main(["run", "some/game", "--oracle", "does-not-exist.json",
                       "--work-dir", "/tmp/portagent_cli_test"])
        self.assertEqual(rc, 2)
        self.assertIn("oracle file not found", err.getvalue())


if __name__ == "__main__":
    unittest.main()
