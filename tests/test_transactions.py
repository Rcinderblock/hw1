"""Проверки очереди, переводов и состояний транзакций."""

import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from bank_system import (
    AccessDeniedError,
    Bank,
    Currency,
    InvalidOperationError,
    OperatingHoursError,
    PremiumAccount,
    RetryableTransactionError,
    SavingsAccount,
    TransactionProcessor,
    TransactionQueue,
    TransactionStatus,
    TransactionType,
)


class TransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.time = datetime(2026, 1, 1, 12, 0)
        self.bank = Bank(clock=lambda: self.time)
        self.alice = self.bank.add_client(
            "Анна Иванова", 30, {"email": "anna@example.test"}, "alice"
        )
        self.bob = self.bank.add_client(
            "Борис Петров", 30, {"email": "boris@example.test"}, "bob"
        )
        self.source = self.bank.open_account(
            self.alice.client_id, "alice", initial_balance="1000",
            account_number="10001",
        )
        self.target = self.bank.open_account(
            self.bob.client_id, "bob", initial_balance="100",
            account_number="20001",
        )
        self.usd = self.bank.open_account(
            self.bob.client_id, "bob", currency="USD", account_number="20002"
        )
        self.queue = TransactionQueue()
        self.processor = TransactionProcessor(
            self.bank, self.queue,
            exchange_rates={(Currency.RUB, Currency.USD): "0.02"},
        )

    def submit(self, amount: str, **options: object):
        return self.processor.submit(
            self.alice.client_id, "alice", self.source, self.target,
            amount, **options,
        )

    def balance(self, client_id: str, password: str, number: str) -> Decimal:
        return next(
            info["balance"]
            for info in self.bank.search_accounts(client_id, password)
            if info["account_number"] == number
        )

    def test_ready_priority_beats_earlier_due_time(self) -> None:
        low = self.submit("10", priority=1)
        high = self.submit(
            "20", priority=10, scheduled_at=self.time + timedelta(minutes=1)
        )
        self.time += timedelta(minutes=1)
        first = self.processor.process_ready(limit=1)
        self.assertEqual(first, [high])
        self.assertEqual(low.status, TransactionStatus.PENDING)
        self.assertEqual(self.processor.process_ready(), [low])
        self.assertEqual(self.balance(self.alice.client_id, "alice", self.source),
                         Decimal("970"))

    def test_delayed_and_cancelled_never_charge_early_or_twice(self) -> None:
        delayed = self.submit(
            "30", scheduled_at=self.time + timedelta(hours=1)
        )
        cancelled = self.submit("40")
        self.processor.cancel(self.alice.client_id, "alice",
                              cancelled.transaction_id)
        self.assertEqual(self.processor.process_ready(), [])
        self.assertEqual(self.balance(self.alice.client_id, "alice", self.source),
                         Decimal("1000"))
        self.time += timedelta(hours=1)
        self.assertEqual(self.processor.process_ready(), [delayed])
        self.assertEqual(self.processor.process_ready(), [])
        self.assertEqual(cancelled.status, TransactionStatus.CANCELLED)
        self.assertEqual(delayed.attempts, 1)
        self.assertEqual(self.balance(self.bob.client_id, "bob", self.target),
                         Decimal("130"))
        with self.assertRaises(InvalidOperationError):
            self.processor.cancel(
                self.alice.client_id, "alice", delayed.transaction_id
            )

    def test_external_fee_and_conversion(self) -> None:
        transaction = self.processor.submit(
            self.alice.client_id, "alice", self.source, self.usd, "100",
            kind=TransactionType.EXTERNAL,
        )
        self.processor.process_ready()
        self.assertEqual(transaction.status, TransactionStatus.COMPLETED)
        self.assertEqual(transaction.currency, Currency.RUB)
        self.assertEqual(transaction.fee, Decimal("10"))
        self.assertEqual(transaction.converted_amount, Decimal("2.00"))
        self.assertEqual(transaction.finished_at, self.time)
        self.assertEqual(self.balance(self.alice.client_id, "alice", self.source),
                         Decimal("890"))
        self.assertEqual(self.balance(self.bob.client_id, "bob", self.usd),
                         Decimal("2.00"))

    def test_failed_transfer_does_not_change_either_balance(self) -> None:
        savings = self.bank.open_account(
            self.alice.client_id, "alice", SavingsAccount,
            initial_balance="100", min_balance="80", account_number="10002",
        )
        too_much = self.processor.submit(
            self.alice.client_id, "alice", savings, self.target, "30"
        )
        self.bank.freeze_account(self.bob.client_id, "bob", self.target)
        frozen = self.submit("10")
        self.processor.process_ready()
        self.assertEqual(too_much.status, TransactionStatus.FAILED)
        self.assertEqual(frozen.status, TransactionStatus.FAILED)
        self.assertEqual(too_much.attempts, 1)
        self.assertEqual(len(frozen.errors), 1)
        self.assertEqual(self.balance(self.alice.client_id, "alice", savings),
                         Decimal("100"))
        self.assertEqual(self.balance(self.alice.client_id, "alice", self.source),
                         Decimal("1000"))
        self.assertEqual(self.balance(self.bob.client_id, "bob", self.target),
                         Decimal("100"))

    def test_only_premium_may_transfer_with_negative_balance(self) -> None:
        premium = self.bank.open_account(
            self.alice.client_id, "alice", PremiumAccount,
            initial_balance="50", account_number="10003",
        )
        self.bank.withdraw(self.alice.client_id, "alice", premium, "70")
        transaction = self.processor.submit(
            self.alice.client_id, "alice", premium, self.target, "100"
        )
        denied = self.submit("1100")
        self.processor.process_ready()
        self.assertEqual(transaction.status, TransactionStatus.COMPLETED)
        self.assertEqual(denied.status, TransactionStatus.FAILED)
        self.assertEqual(self.balance(self.alice.client_id, "alice", premium),
                         Decimal("-130"))
        self.assertEqual(transaction.fee, Decimal("0"))

    def test_retry_records_each_error_and_credits_once(self) -> None:
        def temporary_failure(transaction) -> None:
            if transaction.attempts < 3:
                raise RetryableTransactionError("Временный сбой")

        self.processor.before_transfer = temporary_failure
        transaction = self.submit("20")
        self.processor.process_ready()
        self.assertEqual(transaction.status, TransactionStatus.RETRYING)
        self.assertEqual(transaction.updated_at, self.time)
        self.time += timedelta(minutes=1)
        self.processor.process_ready()
        self.assertEqual(transaction.status, TransactionStatus.RETRYING)
        self.time += timedelta(minutes=1)
        self.processor.process_ready()
        self.assertEqual(transaction.status, TransactionStatus.COMPLETED)
        self.assertEqual(transaction.attempts, 3)
        self.assertEqual([error.attempt for error in transaction.errors], [1, 2])
        self.assertEqual(self.balance(self.bob.client_id, "bob", self.target),
                         Decimal("120"))

    def test_retry_limit_finishes_with_failure_and_no_debit(self) -> None:
        def always_fail(transaction) -> None:
            raise RetryableTransactionError("Временный сбой не исчез")

        self.processor.before_transfer = always_fail
        transaction = self.submit("20")
        for _ in range(3):
            self.processor.process_ready()
            self.time += timedelta(minutes=1)
        self.assertEqual(transaction.status, TransactionStatus.FAILED)
        self.assertEqual(transaction.attempts, 3)
        self.assertEqual(len(transaction.errors), 3)
        self.assertEqual(self.balance(self.alice.client_id, "alice", self.source),
                         Decimal("1000"))
        self.assertEqual(self.processor.process_ready(), [])

    def test_night_pause_and_sender_ownership(self) -> None:
        with self.assertRaises(AccessDeniedError):
            self.processor.submit(
                self.bob.client_id, "bob", self.source, self.target, "10"
            )
        transaction = self.submit("10")
        self.time = datetime(2026, 1, 2, 1, 0)
        self.assertEqual(self.processor.process_ready(), [])
        with self.assertRaises(OperatingHoursError):
            self.submit("10")
        self.time = datetime(2026, 1, 2, 5, 0)
        self.processor.process_ready()
        self.assertEqual(transaction.status, TransactionStatus.COMPLETED)

    def test_invalid_amount_and_missing_rate_are_rejected(self) -> None:
        with self.assertRaises(InvalidOperationError):
            self.submit(Decimal("Infinity"))
        self.processor.exchange_rates.clear()
        transaction = self.processor.submit(
            self.alice.client_id, "alice", self.source, self.usd, "10"
        )
        self.processor.process_ready()
        self.assertEqual(transaction.status, TransactionStatus.FAILED)
        self.assertIn("Нет курса", transaction.rejection_reason)
        self.assertEqual(self.balance(self.alice.client_id, "alice", self.source),
                         Decimal("1000"))


if __name__ == "__main__":
    unittest.main()
