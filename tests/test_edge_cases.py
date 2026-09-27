"""Проверки границ счёта, календаря и обязательного анализа риска."""

import unittest
from datetime import datetime
from decimal import Decimal

from bank_system import (
    AccountStatus,
    AuditEventType,
    AuditLog,
    AuditReporter,
    AuditSeverity,
    Bank,
    BankAccount,
    Currency,
    InvalidOperationError,
    InvestmentAccount,
    PremiumAccount,
    RiskBlockedError,
    SavingsAccount,
    TransactionProcessor,
    TransactionQueue,
    TransactionStatus,
    TransactionType,
)


class AccountEdgeCases(unittest.TestCase):
    def test_explicit_number_must_hide_at_least_one_digit(self) -> None:
        with self.assertRaises(InvalidOperationError):
            BankAccount("Анна", account_number="1234")
        account = BankAccount("Анна", account_number="12345")
        self.assertNotIn("12345", str(account))

    def test_closed_account_cannot_begin_with_money(self) -> None:
        with self.assertRaises(InvalidOperationError):
            BankAccount("Анна", initial_balance="10", status=AccountStatus.CLOSED)

    def test_interest_is_applied_once_per_calendar_month(self) -> None:
        now = [datetime(2026, 1, 1, 12)]
        bank = Bank(clock=lambda: now[0])
        client = bank.add_client("Анна", 20, {"email": "a@test"}, "a")
        number = bank.open_account(
            client.client_id, "a", SavingsAccount, initial_balance="100",
            monthly_interest_rate="0.1",
        )
        self.assertEqual(
            bank.apply_monthly_interest(client.client_id, "a", number),
            Decimal("10.0"),
        )
        with self.assertRaises(InvalidOperationError):
            bank.apply_monthly_interest(client.client_id, "a", number)
        now[0] = datetime(2026, 2, 1, 12)
        self.assertEqual(
            bank.apply_monthly_interest(client.client_id, "a", number),
            Decimal("11.00"),
        )

    def test_rates_reject_impossible_loss_and_monthly_rate_over_one(self) -> None:
        with self.assertRaises(InvalidOperationError):
            SavingsAccount("Анна", monthly_interest_rate="1.01")
        investment = InvestmentAccount("Анна", initial_balance="100")
        investment.allocate_to_asset("stocks", "100")
        with self.assertRaises(InvalidOperationError):
            investment.project_yearly_growth({"stocks": "-1.01"})
        self.assertEqual(
            investment.project_yearly_growth({"stocks": "-1"}),
            Decimal("-100"),
        )


class RiskIntegrationCases(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 1, 1, 12)
        self.bank = Bank(clock=lambda: self.now)
        self.alice = self.bank.add_client("Анна", 20, {"email": "a@test"}, "a")
        self.bob = self.bank.add_client("Борис", 20, {"email": "b@test"}, "b")
        self.source = self.bank.open_account(
            self.alice.client_id, "a", initial_balance="5000"
        )
        self.target = self.bank.open_account(self.bob.client_id, "b")
        self.processor = TransactionProcessor(self.bank, TransactionQueue())

    def test_default_processor_blocks_high_risk_transfer(self) -> None:
        with self.assertRaises(RiskBlockedError):
            self.processor.submit(
                self.alice.client_id, "a", self.source, self.target, "1000"
            )
        self.assertEqual(self.bank.get_total_balance()["RUB"], Decimal("5000"))
        self.assertEqual(
            len(self.processor.audit_log.filter(
                event_type=AuditEventType.TRANSACTION_BLOCKED
            )), 1,
        )

    def test_large_direct_withdrawal_is_blocked_before_balance_changes(self) -> None:
        with self.assertRaises(RiskBlockedError):
            self.bank.withdraw(self.alice.client_id, "a", self.source, "1000")
        self.assertEqual(self.bank.get_total_balance()["RUB"], Decimal("5000"))
        self.assertEqual(
            len(self.processor.audit_log.filter(
                event_type=AuditEventType.WITHDRAWAL_BLOCKED
            )), 1,
        )
        self.assertTrue(self.alice.has_suspicious_activity)
        self.assertEqual(
            self.processor.audit_log.filter(
                event_type=AuditEventType.WITHDRAWAL_BLOCKED
            )[0].severity, AuditSeverity.CRITICAL,
        )
        reporter = AuditReporter(self.processor.audit_log)
        self.assertEqual(
            len(reporter.suspicious_operations(self.alice.client_id)), 1
        )
        self.assertEqual(reporter.error_statistics()["blocked"], 1)

    def test_existing_audit_cannot_be_replaced(self) -> None:
        self.bank.withdraw(self.alice.client_id, "a", self.source, "10")
        prior_log = self.bank.audit_log
        with self.assertRaises(InvalidOperationError):
            TransactionProcessor(
                self.bank, TransactionQueue(), audit_log=AuditLog()
            )
        self.assertIs(self.bank.audit_log, prior_log)
        self.assertEqual(len(prior_log.entries), 2)

    def test_second_processor_cannot_split_audit_and_risk(self) -> None:
        with self.assertRaises(InvalidOperationError):
            TransactionProcessor(
                self.bank, TransactionQueue(), audit_log=AuditLog()
            )
        self.assertIs(self.bank.audit_log, self.processor.audit_log)

    def test_processor_cannot_disable_risk_after_creation(self) -> None:
        with self.assertRaises(AttributeError):
            self.processor.risk_analyzer = None
        self.assertIs(self.processor.risk_analyzer, self.bank.risk_analyzer)

    def test_transfer_is_assessed_once_and_converted_balance_is_actual(self) -> None:
        usd = self.bank.open_account(self.bob.client_id, "b", currency="USD")
        self.processor.exchange_rates[(Currency.RUB, Currency.USD)] = Decimal(
            "0.011"
        )
        transaction = self.processor.submit(
            self.alice.client_id, "a", self.source, usd, "30"
        )
        self.processor.process_ready()
        self.assertEqual(transaction.status, TransactionStatus.COMPLETED)
        self.assertEqual(transaction.converted_amount, Decimal("0.330"))
        self.assertEqual(
            self.bank.get_balance_history(self.bob.client_id, "b", usd)[-1].balance,
            Decimal("0.330"),
        )
        self.assertEqual(len(self.processor.audit_log.filter(
            event_type=AuditEventType.RISK_ASSESSED,
            transaction_id=transaction.transaction_id,
        )), 1)

    def test_premium_transfer_fee_matches_debit(self) -> None:
        premium = self.bank.open_account(
            self.alice.client_id, "a", PremiumAccount,
            initial_balance="100", withdrawal_fee="10",
        )
        before = next(
            row["balance"] for row in self.bank.search_accounts(
                self.alice.client_id, "a"
            ) if row["account_number"] == premium
        )
        transaction = self.processor.submit(
            self.alice.client_id, "a", premium, self.target, "20",
            kind=TransactionType.EXTERNAL,
        )
        self.processor.process_ready()
        after = next(
            row["balance"] for row in self.bank.search_accounts(
                self.alice.client_id, "a"
            ) if row["account_number"] == premium
        )
        self.assertEqual(before - after, transaction.amount + transaction.fee)


if __name__ == "__main__":
    unittest.main()
