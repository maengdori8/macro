from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import renewal_macro


class RenewalStartupTests(unittest.TestCase):
    def test_repeat_selftest_writes_passed_audit_report(self) -> None:
        with TemporaryDirectory() as temporary:
            target = Path(temporary)
            with (
                patch.object(renewal_macro, "app_dir", return_value=target),
                patch.object(
                    renewal_macro.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=0),
                ) as run,
            ):
                result = renewal_macro._startup_selftest_repeat(3)

            self.assertEqual(result, 0)
            self.assertEqual(run.call_count, 3)
            child_environment = run.call_args.kwargs["env"]
            self.assertEqual(
                child_environment["PYINSTALLER_RESET_ENVIRONMENT"],
                "1",
            )
            self.assertEqual(
                child_environment["PYINSTALLER_STRICT_UNPACK_MODE"],
                "1",
            )
            report = json.loads(
                (target / "renewal_startup_selftest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(report["passed"])
            self.assertEqual(report["completed_runs"], 3)
            self.assertEqual(report["failures"], [])

    def test_repeat_selftest_fails_closed_on_timeout(self) -> None:
        with TemporaryDirectory() as temporary:
            target = Path(temporary)
            with (
                patch.object(renewal_macro, "app_dir", return_value=target),
                patch.object(
                    renewal_macro.subprocess,
                    "run",
                    side_effect=subprocess.TimeoutExpired("renewal", 10.0),
                ),
            ):
                result = renewal_macro._startup_selftest_repeat(1)

            self.assertEqual(result, 1)
            report = json.loads(
                (target / "renewal_startup_selftest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertFalse(report["passed"])
            self.assertEqual(report["failures"][0]["return_code"], -1)


if __name__ == "__main__":
    unittest.main()
