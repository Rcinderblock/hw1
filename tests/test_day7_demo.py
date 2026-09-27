"""Повторный экспорт должен содержать данные только текущего запуска."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from bank_system import ReportBuilder
from demo_day7 import generate_reports


class Day7DemoTests(unittest.TestCase):
    def test_repeated_export_keeps_audit_consistent_with_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with redirect_stdout(io.StringIO()), patch.object(
                ReportBuilder, "save_charts", return_value=()
            ):
                generate_reports(root)
                generate_reports(root)
            entries = [json.loads(line) for line in
                       (root / "audit.jsonl").read_text().splitlines()]
            report = json.loads((root / "bank.json").read_text())
            expected_count = sum(report["data"]["audit_events"].values())
            self.assertEqual(len(entries), expected_count)


if __name__ == "__main__":
    unittest.main()
