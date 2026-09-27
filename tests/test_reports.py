"""Проверки снимков данных, экспорта и графиков."""

import csv
import json
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bank_system import (
    AccessDeniedError,
    AuditLog,
    AuthenticationError,
    Bank,
    BankAccount,
    InsufficientFundsError,
    InvalidOperationError,
    Report,
    ReportBuilder,
    ReportKind,
    TransactionProcessor,
    TransactionQueue,
)
from demo_day6 import run_simulation


class BalanceHistoryTests(unittest.TestCase):
    def test_successful_changes_are_recorded_and_private(self) -> None:
        now = datetime(2026, 1, 1, 9)
        bank = Bank(clock=lambda: now)
        first = bank.add_client("Первый", 20, {"email": "a@example.test"}, "a")
        second = bank.add_client("Второй", 20, {"email": "b@example.test"}, "b")
        number = bank.open_account(
            first.client_id, "a", BankAccount, initial_balance="100"
        )
        other = bank.open_account(
            second.client_id, "b", BankAccount, initial_balance="20"
        )
        bank.deposit(first.client_id, "a", number, "25")
        bank.withdraw(first.client_id, "a", number, "10")
        with self.assertRaises(InsufficientFundsError):
            bank.withdraw(first.client_id, "a", number, "150")
        bank._transfer(number, other, Decimal("5"), Decimal("5"), Decimal(0))

        points = bank.get_balance_history(first.client_id, "a", number)
        self.assertEqual([point.operation for point in points], [
            "open", "deposit", "withdraw", "transfer_out"
        ])
        self.assertEqual([point.balance for point in points], [
            Decimal("100"), Decimal("125"), Decimal("115"), Decimal("110")
        ])
        self.assertEqual(
            bank.get_balance_history(second.client_id, "b", other)[-1].balance,
            Decimal("25"),
        )
        with self.assertRaises(AuthenticationError):
            bank.get_balance_history(first.client_id, "wrong", number)
        with self.assertRaises(AccessDeniedError):
            bank.get_balance_history(second.client_id, "b", number)


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_simulation()
        cls.builder = ReportBuilder(
            cls.result.bank, cls.result.processor, cls.result.audit_log
        )

    def test_client_report_contains_own_data_and_actual_balance_points(self) -> None:
        client = self.result.clients[0]
        report = self.builder.client_report(client.client_id, "demo-0")
        self.assertIs(report.kind, ReportKind.CLIENT)
        self.assertEqual(report.data["client"]["full_name"], client.full_name)
        self.assertEqual(len(report.data["accounts"]), 2)
        self.assertEqual(len(report.data["transactions"]), 7)
        self.assertNotIn("demo-0", json.dumps(report.as_dict()))
        first_number = self.result.account_numbers[0][0]
        points = report.data["balance_history"][first_number]
        self.assertEqual(points[0]["operation"], "open")
        self.assertEqual(points[0]["balance"], "10000")
        self.assertEqual(points[-1]["balance"], str(
            self.result.bank.search_accounts(client.client_id, "demo-0")[0]["balance"]
        ))
        with self.assertRaises(AuthenticationError):
            self.builder.client_report(client.client_id, "wrong")

    def test_bank_and_risk_reports_match_source_data(self) -> None:
        bank = self.builder.bank_report()
        risk = self.builder.risk_report()
        self.assertEqual(bank.data["accounts"]["clients"], 6)
        self.assertEqual(bank.data["accounts"]["accounts"], 12)
        self.assertEqual(bank.data["audit_events"]["transaction_completed"], 36)
        self.assertEqual(bank.data["audit_events"]["transaction_failed"], 3)
        self.assertEqual(bank.data["audit_events"]["transaction_blocked"], 2)
        self.assertEqual(
            bank.data["total_balance_by_currency"],
            {key: str(value) for key, value in
             self.result.bank.get_total_balance().items()},
        )
        self.assertEqual(risk.data["error_statistics"]["failed"], 3)
        self.assertEqual(risk.data["error_statistics"]["blocked"], 2)
        self.assertTrue(risk.data["suspicious_operations"])

    def test_investment_history_separates_cash_from_portfolio_value(self) -> None:
        client = self.result.clients[2]
        report = self.builder.client_report(client.client_id, "demo-2")
        number = self.result.account_numbers[2][1]
        points = report.data["balance_history"][number]
        allocated_at = next(
            index for index, point in enumerate(points)
            if point["operation"] == "allocate_to_asset"
        )
        before, after = points[allocated_at - 1:allocated_at + 1]
        self.assertLess(Decimal(after["balance"]), Decimal(before["balance"]))
        self.assertEqual(after["total_value"], before["total_value"])

    def test_text_json_csv_and_png_exports(self) -> None:
        client = self.result.clients[0]
        reports = (
            self.builder.client_report(client.client_id, "demo-0"),
            self.builder.bank_report(),
            self.builder.risk_report(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for report in reports:
                text = self.builder.to_text(report)
                self.assertIn(report.title, text)
                json_path = self.builder.export_to_json(
                    report, root / f"{report.kind.value}.json"
                )
                csv_path = self.builder.export_to_csv(
                    report, root / f"{report.kind.value}.csv"
                )
                self.assertEqual(json.loads(json_path.read_text(
                    encoding="utf-8"
                )), report.as_dict())
                with csv_path.open(encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                self.assertIn(
                    {"field": "kind", "value": report.kind.value}, rows
                )
                paths = self.builder.save_charts(report, root / report.kind.value)
                self.assertTrue(paths)
                for path in paths:
                    self.assertGreater(path.stat().st_size, 1000)
                    self.assertTrue(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(len(list(root.rglob("*.png"))), 6)

    def test_builder_rejects_unrelated_journal(self) -> None:
        with self.assertRaises(InvalidOperationError):
            ReportBuilder(
                self.result.bank, self.result.processor, AuditLog()
            )

    def test_empty_reports_export_and_remain_snapshots(self) -> None:
        bank = Bank(clock=lambda: datetime(2026, 1, 1, 12))
        log = AuditLog()
        processor = TransactionProcessor(bank, TransactionQueue(), audit_log=log)
        builder = ReportBuilder(bank, processor, log)
        empty_bank, empty_risk = builder.bank_report(), builder.risk_report()
        client = bank.add_client("Анна", 20, {"email": "a@test"}, "a")
        empty_client = builder.client_report(client.client_id, "a")
        self.assertEqual(empty_bank.data["accounts"]["clients"], 0)
        self.assertEqual(empty_client.data["accounts"], [])
        with tempfile.TemporaryDirectory() as directory:
            for report in (empty_bank, empty_risk, empty_client):
                path = builder.export_to_json(
                    report, Path(directory) / f"{report.kind.value}.json"
                )
                self.assertEqual(json.loads(path.read_text()), report.as_dict())
                builder.save_charts(report, directory)

    def test_ranking_chart_keeps_clients_with_duplicate_names_separate(self) -> None:
        from matplotlib.figure import Figure

        report = Report(ReportKind.BANK, "Банк", "2026-01-01T12:00:00", {
            "accounts": {"by_type": {"BankAccount": 2}},
            "top_clients_by_currency": {"RUB": [
                {"client_id": "first", "full_name": "Анна Иванова", "total": "200"},
                {"client_id": "second", "full_name": "Анна Иванова", "total": "100"},
            ]},
        })
        positions = []
        savefig = Figure.savefig

        def inspect_bars(figure, path, **options):
            if Path(path).name == "bank_top_clients_RUB.png":
                positions.extend(
                    bar.get_x() + bar.get_width() / 2
                    for bar in figure.axes[0].patches
                )
            return savefig(figure, path, **options)

        with tempfile.TemporaryDirectory() as directory, patch.object(
            Figure, "savefig", inspect_bars
        ):
            self.builder.save_charts(report, directory)
        self.assertEqual(len(positions), 2)
        self.assertEqual(len(set(positions)), 2)
