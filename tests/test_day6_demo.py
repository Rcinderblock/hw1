"""Сквозные проверки комплексной демонстрации."""

import json
import tempfile
import unittest
from collections import Counter
from decimal import Decimal
from pathlib import Path

from bank_system import (
    AuditEventType,
    AuditReporter,
    AuthenticationError,
    ClientStatus,
    TransactionStatus,
    TransactionType,
)
from demo_day6 import run_simulation


class Day6DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory()
        cls.audit_path = Path(cls.directory.name) / "audit.jsonl"
        cls.result = run_simulation(cls.audit_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.directory.cleanup()

    def test_required_scale_and_mixed_outcomes(self) -> None:
        result = self.result
        self.assertEqual(len(result.clients), 6)
        self.assertEqual(sum(len(pair) for pair in result.account_numbers), 12)
        # Шестой клиент заблокирован в конце сценария; нужные типы есть у пяти.
        account_types = {
            info["account_type"]
            for index, client in enumerate(result.clients[:5])
            for info in result.bank.search_accounts(
                client.client_id, f"demo-{index}"
            )
        }
        self.assertEqual(account_types, {
            "BankAccount", "SavingsAccount", "PremiumAccount",
            "InvestmentAccount",
        })
        self.assertEqual(result.attempted, 42)
        self.assertEqual(len(result.transactions), 40)
        self.assertEqual(result.blocked_before_queue, 2)
        self.assertEqual(
            Counter(item.status for item in result.transactions),
            {
                TransactionStatus.COMPLETED: 36,
                TransactionStatus.FAILED: 3,
                TransactionStatus.CANCELLED: 1,
            },
        )
        event_counts = Counter(
            entry.event_type for entry in result.audit_log.entries
        )
        self.assertEqual(event_counts[AuditEventType.TRANSACTION_QUEUED], 40)
        self.assertEqual(event_counts[AuditEventType.TRANSACTION_COMPLETED], 36)
        self.assertEqual(event_counts[AuditEventType.TRANSACTION_FAILED], 3)
        self.assertEqual(event_counts[AuditEventType.TRANSACTION_BLOCKED], 2)

    def test_file_contains_same_events_without_demo_passwords(self) -> None:
        text = self.audit_path.read_text(encoding="utf-8")
        rows = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(len(rows), len(self.result.audit_log.entries))
        self.assertEqual(len(rows), 127)
        self.assertNotIn("demo-0", text)
        self.assertNotIn("wrong", text)

    def test_client_views_are_scoped_and_authenticated(self) -> None:
        client = self.result.clients[0]
        accounts = self.result.bank.search_accounts(client.client_id, "demo-0")
        self.assertEqual(len(accounts), 2)
        history = self.result.processor.get_history(client.client_id, "demo-0")
        self.assertEqual(len(history), 7)
        self.assertTrue(all(item.client_id == client.client_id for item in history))
        self.assertIn(TransactionStatus.FAILED, {item.status for item in history})
        with self.assertRaises(AuthenticationError):
            self.result.processor.get_history(client.client_id, "incorrect")

        reporter = AuditReporter(self.result.audit_log)
        suspicious = self.result.processor.get_suspicious_operations(
            client.client_id, "demo-0"
        )
        self.assertTrue(all(entry.client_id == client.client_id
                            for entry in suspicious))
        with self.assertRaises(AuthenticationError):
            self.result.processor.get_suspicious_operations(
                client.client_id, "incorrect"
            )
        self.assertEqual(
            reporter.client_risk_profile(client.client_id)["highest_risk"],
            "high",
        )
        self.assertEqual(self.result.clients[5].status, ClientStatus.BLOCKED)

    def test_reports_and_special_transfers_agree_with_account_state(self) -> None:
        totals = self.result.bank.get_total_balance()
        ranking = self.result.bank.get_clients_ranking()
        for currency, rows in ranking.items():
            self.assertEqual(
                sum((row["total"] for row in rows), Decimal(0)),
                totals[currency],
            )
            self.assertEqual(rows, sorted(
                rows, key=lambda row: (-row["total"], row["full_name"])
            ))

        external = [
            item for item in self.result.transactions
            if item.kind is TransactionType.EXTERNAL
        ]
        self.assertEqual(len(external), 1)
        self.assertEqual(external[0].fee, Decimal("10"))
        self.assertEqual(external[0].converted_amount, Decimal("2.00"))
        self.assertEqual(totals["USD"], Decimal("102.00"))
        self.assertEqual(
            AuditReporter(self.result.audit_log).error_statistics()["retries"],
            1,
        )
        self.assertEqual(
            sorted(item.attempts for item in self.result.transactions)[-1], 2
        )


if __name__ == "__main__":
    unittest.main()
