"""Проверки границы доступа и отложенного исполнения заявок."""

import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from bank_system import (
    AuditLog,
    Bank,
    Currency,
    InvalidOperationError,
    Transaction,
    TransactionProcessor,
    TransactionQueue,
    TransactionStatus,
    TransactionType,
)


class TransactionSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 1, 1, 12)
        self.bank = Bank(clock=lambda: self.now)
        self.alice = self.bank.add_client("Анна", 20, {"email": "a@test"}, "a")
        self.bob = self.bank.add_client("Борис", 20, {"email": "b@test"}, "b")
        self.source = self.bank.open_account(
            self.alice.client_id, "a", initial_balance="100"
        )
        self.target = self.bank.open_account(
            self.bob.client_id, "b", initial_balance="20"
        )
        self.queue = TransactionQueue()
        self.processor = TransactionProcessor(
            self.bank, self.queue, audit_log=AuditLog()
        )

    def submit(self, **options):
        return self.processor.submit(
            self.alice.client_id, "a", self.source, self.target, "10", **options
        )

    def test_returned_transaction_cannot_change_approved_request(self) -> None:
        transaction = self.submit()
        with self.assertRaises(AttributeError):
            transaction.sender = self.target
        with self.assertRaises(AttributeError):
            transaction.amount = Decimal("-10")
        with self.assertRaises(AttributeError):
            transaction.status = TransactionStatus.COMPLETED
        for name in (
            "kind", "currency", "recipient", "created_at", "scheduled_at",
            "priority", "client_id", "transaction_id", "fee",
            "converted_amount", "rejection_reason", "updated_at",
            "finished_at", "attempts", "errors",
        ):
            with self.subTest(field=name), self.assertRaises(AttributeError):
                setattr(transaction, name, getattr(transaction, name))
        history = self.processor.get_history(self.alice.client_id, "a")
        self.assertIsInstance(history[0].errors, tuple)
        self.processor.process_ready()
        self.assertEqual(transaction.status, TransactionStatus.COMPLETED)

    def test_blocked_client_cannot_execute_a_previously_queued_transfer(self) -> None:
        transaction = self.submit(scheduled_at=self.now + timedelta(hours=1))
        for _ in range(3):
            self.bank.authenticate_client(self.alice.client_id, "wrong")
        self.now += timedelta(hours=1)
        self.processor.process_ready()
        self.assertEqual(transaction.status, TransactionStatus.FAILED)
        self.assertEqual(self.bank.search_accounts(
            self.bob.client_id, "b"
        )[0]["balance"], Decimal("20"))

    def test_queue_insertion_cannot_bypass_password_check(self) -> None:
        transaction = Transaction(
            TransactionType.INTERNAL, Decimal("10"), Currency.RUB,
            self.source, self.target, self.now, self.now,
            client_id=self.alice.client_id,
        )
        self.queue.add(transaction)
        self.processor.process_ready()
        self.assertEqual(transaction.status, TransactionStatus.FAILED)
        self.assertEqual(self.bank.search_accounts(
            self.alice.client_id, "a"
        )[0]["balance"], Decimal("100"))

    def test_batch_stops_when_midnight_arrives_between_transfers(self) -> None:
        first, second = self.submit(), self.submit()
        self.now = datetime(2026, 1, 1, 23, 59, 59)
        transfer = self.bank._transfer

        def cross_midnight(*args, **kwargs):
            transfer(*args, **kwargs)
            self.now = datetime(2026, 1, 2, 0)

        with patch.object(self.bank, "_transfer", side_effect=cross_midnight):
            processed = self.processor.process_ready()
        self.assertEqual(processed, [first])
        self.assertEqual(second.status, TransactionStatus.PENDING)
        self.now = datetime(2026, 1, 2, 5)
        self.assertEqual(self.processor.process_ready(), [second])

    def test_invalid_updated_exchange_rate_cannot_debit_recipient(self) -> None:
        usd = self.bank.open_account(self.bob.client_id, "b", currency="USD")
        self.processor.exchange_rates[(Currency.RUB, Currency.USD)] = Decimal("-1")
        transaction = self.processor.submit(
            self.alice.client_id, "a", self.source, usd, "10"
        )
        self.processor.process_ready()
        self.assertEqual(transaction.status, TransactionStatus.FAILED)
        self.assertEqual(self.bank.search_accounts(
            self.bob.client_id, "b", currency="USD"
        )[0]["balance"], Decimal("0"))

    def test_negative_updated_fee_cannot_credit_sender(self) -> None:
        self.processor.external_fee = Decimal("-20")
        transaction = self.submit(kind=TransactionType.EXTERNAL)
        self.processor.process_ready()
        self.assertEqual(transaction.status, TransactionStatus.FAILED)
        self.assertEqual(self.bank.search_accounts(
            self.alice.client_id, "a"
        )[0]["balance"], Decimal("100"))

    def test_equal_priority_is_fifo_and_cancelled_retry_never_executes(self) -> None:
        first, second = self.submit(), self.submit()
        self.assertEqual(self.queue.pop_ready(self.now), first)
        retry_at = self.now + timedelta(minutes=1)
        self.queue.retry(first, self.now, retry_at)
        self.processor.cancel(self.alice.client_id, "a", first.transaction_id)
        self.assertEqual(self.processor.process_ready(), [second])
        self.now = retry_at
        self.assertEqual(self.processor.process_ready(), [])
        self.assertEqual(first.status, TransactionStatus.CANCELLED)

    def test_retry_rejects_a_transaction_from_another_queue(self) -> None:
        transaction = self.submit()
        self.queue.pop_ready(self.now)
        foreign_queue = TransactionQueue()
        with self.assertRaises(InvalidOperationError):
            foreign_queue.retry(transaction, self.now, self.now + timedelta(minutes=1))
        self.assertEqual(foreign_queue.pop_ready(self.now + timedelta(hours=1)), None)


if __name__ == "__main__":
    unittest.main()
