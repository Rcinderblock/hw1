"""Проверки журнала аудита, оценки риска и блокировки переводов."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bank_system import (
    AuditEventType,
    AuditLog,
    AuditReporter,
    AuditSeverity,
    Bank,
    Currency,
    OperatingHoursError,
    RetryableTransactionError,
    RiskAnalyzer,
    RiskBlockedError,
    RiskLevel,
    RiskReason,
    TransactionProcessor,
    TransactionQueue,
    TransactionStatus,
)


class AuditLogTests(unittest.TestCase):
    def test_memory_file_and_filters(self) -> None:
        at = datetime(2026, 1, 1, 12)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "audit.jsonl"
            log = AuditLog(path)
            first = log.record(
                AuditEventType.RISK_ASSESSED, AuditSeverity.WARNING,
                "client-a", "transaction-a", at,
                {"risk_level": "medium", "reasons": "new_recipient"},
            )
            log.record(
                AuditEventType.TRANSACTION_COMPLETED, AuditSeverity.INFO,
                "client-a", "transaction-a", at + timedelta(minutes=1),
            )
            log.record(
                AuditEventType.TRANSACTION_BLOCKED, AuditSeverity.CRITICAL,
                "client-b", "transaction-b", at + timedelta(minutes=2),
                {"reason": "Высокий риск", "error_type": "RiskBlockedError"},
            )

            self.assertEqual(len(log.entries), 3)
            self.assertEqual(
                log.filter(client_id="client-a", min_severity=AuditSeverity.WARNING),
                (first,),
            )
            self.assertEqual(
                len(log.filter(since=at + timedelta(minutes=1))), 2
            )
            self.assertEqual(
                log.filter(transaction_id="transaction-b"), (log.entries[2],)
            )
            lines = [json.loads(line) for line in path.read_text(
                encoding="utf-8"
            ).splitlines()]
            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[2]["details"]["reason"], "Высокий риск")
            self.assertEqual(lines[0]["entry_id"], first.entry_id)
            self.assertEqual(log.persistence_errors, ())

    def test_file_error_is_visible_but_memory_entry_remains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = AuditLog(directory)  # Это каталог, а не файл.
            log.record(
                AuditEventType.TRANSACTION_QUEUED, AuditSeverity.INFO,
                "client", "transaction", datetime(2026, 1, 1, 12),
            )
            self.assertEqual(len(log.entries), 1)
            self.assertEqual(len(log.persistence_errors), 1)


class RiskAnalyzerTests(unittest.TestCase):
    def test_four_signals_and_three_levels(self) -> None:
        analyzer = RiskAnalyzer()
        at = datetime(2026, 1, 1, 12)
        first = analyzer.assess("client", "tx-1", "10", Currency.RUB,
                                "recipient-a", at)
        self.assertEqual(first.level, RiskLevel.MEDIUM)
        self.assertEqual(first.reasons, (RiskReason.NEW_RECIPIENT,))

        analyzer.record_success("client", "recipient-a")
        familiar = analyzer.assess("client", "tx-2", "10", Currency.RUB,
                                   "recipient-a", at)
        self.assertEqual(familiar.level, RiskLevel.LOW)
        large = analyzer.assess("client", "tx-3", "1000", Currency.RUB,
                                "recipient-a", at)
        self.assertEqual(large.reasons, (RiskReason.LARGE_AMOUNT,))
        self.assertEqual(large.level, RiskLevel.MEDIUM)

        analyzer.record_attempt("client", "tx-1", at)
        analyzer.record_attempt("client", "tx-2", at)
        frequent = analyzer.assess("client", "tx-3", "10", Currency.RUB,
                                   "recipient-a", at)
        self.assertEqual(frequent.reasons, (RiskReason.FREQUENT_OPERATIONS,))
        self.assertEqual(frequent.level, RiskLevel.MEDIUM)
        combined = analyzer.assess("client", "tx-4", "1000", Currency.RUB,
                                   "recipient-b", at)
        self.assertEqual(combined.level, RiskLevel.HIGH)
        self.assertIn(RiskReason.LARGE_AMOUNT, combined.reasons)
        self.assertIn(RiskReason.NEW_RECIPIENT, combined.reasons)

        night = analyzer.assess(
            "other", "tx-5", "10", Currency.RUB, "recipient-a",
            datetime(2026, 1, 2, 1),
        )
        self.assertEqual(night.level, RiskLevel.HIGH)
        self.assertIn(RiskReason.NIGHT_OPERATION, night.reasons)


class AuditedTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 1, 1, 12)
        self.bank = Bank(clock=lambda: self.now)
        self.alice = self.bank.add_client(
            "Анна Иванова", 30, {"email": "a@example.test"}, "a"
        )
        self.bob = self.bank.add_client(
            "Борис Петров", 30, {"email": "b@example.test"}, "b"
        )
        self.source = self.bank.open_account(
            self.alice.client_id, "a", initial_balance="5000",
            account_number="10001",
        )
        self.first_target = self.bank.open_account(
            self.bob.client_id, "b", account_number="20001"
        )
        self.second_target = self.bank.open_account(
            self.bob.client_id, "b", account_number="20002"
        )
        self.log = AuditLog()
        self.risk = RiskAnalyzer()
        self.queue = TransactionQueue()
        self.processor = TransactionProcessor(
            self.bank, self.queue, audit_log=self.log,
            risk_analyzer=self.risk,
        )

    def submit(self, amount: str, recipient: str | None = None, **options):
        return self.processor.submit(
            self.alice.client_id, "a", self.source,
            recipient or self.first_target, amount, **options,
        )

    def balance(self, number: str) -> Decimal:
        return next(
            info["balance"]
            for info in self.bank.search_accounts(self.alice.client_id, "a")
            if info["account_number"] == number
        )

    def test_first_recipient_is_medium_then_known_recipient_is_low(self) -> None:
        first = self.submit("10")
        self.processor.process_ready()
        second = self.submit("20")
        self.processor.process_ready()
        self.assertEqual(first.status, TransactionStatus.COMPLETED)
        self.assertEqual(second.status, TransactionStatus.COMPLETED)
        report = AuditReporter(self.log)
        profile = report.client_risk_profile(self.alice.client_id)
        self.assertEqual(profile["levels"], {
            "low": 1, "medium": 1, "high": 0,
        })
        self.assertEqual(profile["highest_risk"], "medium")
        self.assertEqual(len(report.suspicious_operations()), 1)
        self.assertEqual(report.error_statistics()["failed"], 0)

    def test_large_transfer_to_new_recipient_is_blocked_before_queue(self) -> None:
        with self.assertRaises(RiskBlockedError):
            self.submit("1000")
        self.assertEqual(self.balance(self.source), Decimal("5000"))
        self.assertEqual(
            self.log.filter(event_type=AuditEventType.TRANSACTION_QUEUED), ()
        )
        report = AuditReporter(self.log)
        self.assertEqual(len(report.suspicious_operations()), 1)
        self.assertEqual(report.error_statistics()["blocked"], 1)
        self.assertEqual(
            report.client_risk_profile(self.alice.client_id)["highest_risk"],
            "high",
        )

    def test_third_quick_new_recipient_request_is_blocked(self) -> None:
        self.submit("10")
        self.submit("10")
        with self.assertRaises(RiskBlockedError):
            self.submit("10")
        self.assertEqual(
            len(self.log.filter(event_type=AuditEventType.TRANSACTION_QUEUED)),
            2,
        )
        self.assertEqual(self.balance(self.source), Decimal("5000"))

    def test_night_attempt_is_audited_and_day3_rule_still_blocks(self) -> None:
        self.now = datetime(2026, 1, 2, 1)
        with self.assertRaises(OperatingHoursError):
            self.submit("10")
        suspicious = AuditReporter(self.log).suspicious_operations()
        self.assertEqual(len(suspicious), 1)
        self.assertIn("night_operation", suspicious[0].details["reasons"])
        self.assertEqual(len(self.bank.security_events), 1)
        self.assertEqual(
            AuditReporter(self.log).error_statistics()["blocked"], 1
        )

    def test_delayed_transfer_is_rechecked_before_debit(self) -> None:
        known = self.submit("10")
        self.processor.process_ready()
        self.assertEqual(known.status, TransactionStatus.COMPLETED)
        self.now += timedelta(minutes=11)
        delayed = self.submit(
            "1000", scheduled_at=self.now + timedelta(minutes=1)
        )
        self.submit("10")
        self.submit("10")
        self.now += timedelta(minutes=1)
        self.processor.process_ready()
        self.assertEqual(delayed.status, TransactionStatus.FAILED)
        self.assertIn("высокий риск", delayed.rejection_reason)
        self.assertEqual(self.balance(self.source), Decimal("4970"))
        self.assertEqual(
            AuditReporter(self.log).error_statistics()["blocked"], 1
        )
        self.assertEqual(
            len(AuditReporter(self.log).suspicious_operations()), 3
        )

    def test_failure_and_retry_appear_in_error_statistics(self) -> None:
        def temporary_failure(transaction) -> None:
            if transaction.attempts == 1:
                raise RetryableTransactionError("Учебный сбой")

        self.processor.before_transfer = temporary_failure
        retrying = self.submit("10")
        self.processor.process_ready()
        self.assertEqual(retrying.status, TransactionStatus.RETRYING)
        self.now += timedelta(minutes=1)
        self.processor.process_ready()
        self.assertEqual(retrying.status, TransactionStatus.COMPLETED)

        failing = self.submit("10", self.second_target)
        self.bank.freeze_account(self.bob.client_id, "b", self.second_target)
        self.processor.before_transfer = None
        self.processor.process_ready()
        self.assertEqual(failing.status, TransactionStatus.FAILED)
        stats = AuditReporter(self.log).error_statistics()
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["retries"], 1)
        self.assertEqual(stats["by_error_type"], {
            "AccountFrozenError": 1,
            "RetryableTransactionError": 1,
        })

    def test_file_write_failure_does_not_change_completed_transfer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.log.path = Path(directory)  # Путь указывает на каталог.
            transaction = self.submit("10")
            self.processor.process_ready()
            self.assertEqual(transaction.status, TransactionStatus.COMPLETED)
            self.assertEqual(self.balance(self.source), Decimal("4990"))
            self.assertEqual(len(self.log.entries), 3)
            self.assertEqual(len(self.log.persistence_errors), 3)


if __name__ == "__main__":
    unittest.main()
