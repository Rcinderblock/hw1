"""Проверки клиентов, операций банка и правил безопасности."""

import unittest
from datetime import datetime
from decimal import Decimal

from bank_system import (
    AccessDeniedError,
    AccountClosedError,
    AccountFrozenError,
    AuthenticationError,
    Bank,
    BankAccount,
    Client,
    ClientStatus,
    InsufficientFundsError,
    InvalidOperationError,
    InvestmentAccount,
    OperatingHoursError,
    PremiumAccount,
    SavingsAccount,
)


class ClientTests(unittest.TestCase):
    def test_age_and_contact_validation(self) -> None:
        adult = Client("Анна Иванова", 18, {"email": "a@example.test"}, "p")
        self.assertEqual(adult.age, 18)
        for age in (17, -1, True, "20"):
            with self.subTest(age=age):
                with self.assertRaises(InvalidOperationError):
                    Client("Анна Иванова", age, {"email": "a@example.test"}, "p")
        with self.assertRaises(InvalidOperationError):
            Client("Анна Иванова", 20, {}, "p")
        with self.assertRaises(InvalidOperationError):
            Client("Анна Иванова", 20, {"email": "  "}, "p")

    def test_client_fields_and_copied_collections(self) -> None:
        client = Client(" Анна Иванова ", 20, {"email": " a@example.test "}, "p")
        client.contacts["email"] = "other@example.test"
        client.account_numbers.append("1234")

        self.assertEqual(client.full_name, "Анна Иванова")
        self.assertEqual(client.age, 20)
        self.assertEqual(client.contacts["email"], "a@example.test")
        self.assertEqual(client.account_numbers, [])
        self.assertEqual(client.status, ClientStatus.ACTIVE)


class BankTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0)
        self.bank = Bank(clock=lambda: self.now)
        self.anna = self.bank.add_client(
            "Анна Иванова", 30, {"email": "anna@example.test"}, "pass-a"
        )
        self.boris = self.bank.add_client(
            "Борис Петров", 25, {"phone": "+70000000000"}, "pass-b"
        )

    def test_open_search_and_duplicate_number(self) -> None:
        first = self.bank.open_account(
            self.anna.client_id, "pass-a", BankAccount,
            initial_balance="100", account_number="12345678",
        )
        second = self.bank.open_account(
            self.anna.client_id, "pass-a", SavingsAccount,
            initial_balance="200", currency="USD", min_balance="50",
        )

        self.assertEqual(self.anna.account_numbers, [first, second])
        self.assertEqual(
            len(self.bank.search_accounts(self.anna.client_id, "pass-a")), 2
        )
        self.assertEqual(
            [info["account_number"] for info in self.bank.search_accounts(
                self.anna.client_id, "pass-a", currency="USD"
            )],
            [second],
        )
        with self.assertRaises(InvalidOperationError):
            self.bank.open_account(
                self.boris.client_id, "pass-b", account_number="12345678"
            )
        with self.assertRaises(InvalidOperationError):
            self.bank.open_account(
                self.anna.client_id, "pass-a", owner="Другой владелец"
            )

    def test_freeze_unfreeze_and_close(self) -> None:
        number = self.bank.open_account(
            self.anna.client_id, "pass-a", initial_balance="100"
        )

        self.bank.freeze_account(self.anna.client_id, "pass-a", number)
        with self.assertRaises(AccountFrozenError):
            self.bank.withdraw(self.anna.client_id, "pass-a", number, "1")
        with self.assertRaises(AccountFrozenError):
            self.bank.close_account(self.anna.client_id, "pass-a", number)
        self.assertEqual(
            self.bank.search_accounts(
                self.anna.client_id, "pass-a", status="frozen"
            )[0]["account_number"],
            number,
        )
        self.bank.unfreeze_account(self.anna.client_id, "pass-a", number)
        self.assertEqual(
            self.bank.close_account(self.anna.client_id, "pass-a", number),
            Decimal("100"),
        )
        info = self.bank.search_accounts(self.anna.client_id, "pass-a")[0]
        self.assertEqual(info["status"], "closed")
        self.assertEqual(info["balance"], Decimal("0"))
        with self.assertRaises(AccountClosedError):
            self.bank.deposit(self.anna.client_id, "pass-a", number, "1")

    def test_premium_debt_must_be_repaid_before_closing(self) -> None:
        number = self.bank.open_account(
            self.anna.client_id, "pass-a", PremiumAccount,
            initial_balance="100", overdraft_limit="500", withdrawal_fee="10",
        )
        self.bank.withdraw(self.anna.client_id, "pass-a", number, "200")

        with self.assertRaises(InvalidOperationError):
            self.bank.close_account(self.anna.client_id, "pass-a", number)
        self.bank.deposit(self.anna.client_id, "pass-a", number, "110")
        self.assertEqual(
            self.bank.close_account(self.anna.client_id, "pass-a", number),
            Decimal("0"),
        )

    def test_investment_release_before_closing(self) -> None:
        number = self.bank.open_account(
            self.anna.client_id, "pass-a", InvestmentAccount,
            initial_balance="100",
        )
        self.bank.allocate_to_asset(
            self.anna.client_id, "pass-a", number, "stocks", "80"
        )

        with self.assertRaises(InvalidOperationError):
            self.bank.close_account(self.anna.client_id, "pass-a", number)
        self.bank.release_from_asset(
            self.anna.client_id, "pass-a", number, "stocks", "80"
        )
        self.assertEqual(
            self.bank.close_account(self.anna.client_id, "pass-a", number),
            Decimal("100"),
        )

    def test_three_failed_attempts_block_client(self) -> None:
        for expected_attempts in (1, 2, 3):
            self.assertFalse(
                self.bank.authenticate_client(self.anna.client_id, "wrong")
            )
            self.assertEqual(self.anna.failed_attempts, expected_attempts)

        self.assertEqual(self.anna.status, ClientStatus.BLOCKED)
        self.assertFalse(
            self.bank.authenticate_client(self.anna.client_id, "pass-a")
        )
        self.assertTrue(self.anna.has_suspicious_activity)
        self.assertEqual(len(self.anna.suspicious_actions), 3)
        self.assertIn("заблокирован", self.bank.security_events[-1].reason)
        with self.assertRaises(AuthenticationError):
            self.bank.open_account(self.anna.client_id, "pass-a")

    def test_successful_login_resets_failed_count(self) -> None:
        self.bank.authenticate_client(self.anna.client_id, "wrong")
        self.assertTrue(
            self.bank.authenticate_client(self.anna.client_id, "pass-a")
        )
        self.assertEqual(self.anna.failed_attempts, 0)

    def test_night_restriction_and_boundaries(self) -> None:
        number = self.bank.open_account(self.anna.client_id, "pass-a")
        self.now = datetime(2026, 1, 2, 0, 0)
        with self.assertRaises(OperatingHoursError):
            self.bank.deposit(self.anna.client_id, "pass-a", number, "10")
        self.now = datetime(2026, 1, 2, 4, 59)
        with self.assertRaises(OperatingHoursError):
            self.bank.freeze_account(self.anna.client_id, "pass-a", number)
        self.assertEqual(len(self.anna.suspicious_actions), 2)
        self.assertEqual(
            self.bank.search_accounts(self.anna.client_id, "pass-a")[0]["status"],
            "active",
        )
        self.now = datetime(2026, 1, 2, 5, 0)
        self.assertEqual(
            self.bank.deposit(self.anna.client_id, "pass-a", number, "10"),
            Decimal("10"),
        )

    def test_other_client_account_access_is_flagged(self) -> None:
        number = self.bank.open_account(
            self.anna.client_id, "pass-a", initial_balance="100"
        )

        with self.assertRaises(AccessDeniedError):
            self.bank.withdraw(self.boris.client_id, "pass-b", number, "10")
        self.assertTrue(self.boris.has_suspicious_activity)
        self.assertEqual(self.bank.get_total_balance()["RUB"], Decimal("100"))

    def test_totals_and_ranking_are_per_currency(self) -> None:
        self.bank.open_account(
            self.anna.client_id, "pass-a", initial_balance="100"
        )
        investment = self.bank.open_account(
            self.anna.client_id, "pass-a", InvestmentAccount,
            initial_balance="200",
        )
        self.bank.allocate_to_asset(
            self.anna.client_id, "pass-a", investment, "etf", "100"
        )
        self.bank.open_account(
            self.boris.client_id, "pass-b", initial_balance="50"
        )
        premium = self.bank.open_account(
            self.boris.client_id, "pass-b", PremiumAccount,
            initial_balance="100", currency="USD",
        )
        self.bank.withdraw(self.boris.client_id, "pass-b", premium, "150")

        self.assertEqual(
            self.bank.get_total_balance(),
            {"RUB": Decimal("350"), "USD": Decimal("-60")},
        )
        ranking = self.bank.get_clients_ranking()
        self.assertEqual(
            [row["full_name"] for row in ranking["RUB"]],
            ["Анна Иванова", "Борис Петров"],
        )
        self.assertEqual(ranking["RUB"][0]["total"], Decimal("300"))
        self.assertEqual(ranking["USD"][0]["total"], Decimal("-60"))

    def test_bank_wrappers_for_type_specific_operations(self) -> None:
        savings = self.bank.open_account(
            self.anna.client_id, "pass-a", SavingsAccount,
            initial_balance="100", monthly_interest_rate="0.01",
        )
        investment = self.bank.open_account(
            self.anna.client_id, "pass-a", InvestmentAccount,
            initial_balance="100",
        )

        self.assertEqual(
            self.bank.apply_monthly_interest(
                self.anna.client_id, "pass-a", savings
            ),
            Decimal("1.00"),
        )
        self.bank.allocate_to_asset(
            self.anna.client_id, "pass-a", investment, "bonds", "40"
        )
        self.assertEqual(
            self.bank.release_from_asset(
                self.anna.client_id, "pass-a", investment, "bonds", "10"
            ),
            Decimal("30"),
        )
        with self.assertRaises(InvalidOperationError):
            self.bank.apply_monthly_interest(
                self.anna.client_id, "pass-a", investment
            )


if __name__ == "__main__":
    unittest.main()
