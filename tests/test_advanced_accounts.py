"""Поведенческие проверки трёх дочерних типов счетов."""

import unittest
from decimal import Decimal

from bank_system import (
    AbstractAccount,
    AccountClosedError,
    AccountFrozenError,
    AccountStatus,
    BankAccount,
    InsufficientFundsError,
    InvalidOperationError,
    InvestmentAccount,
    PremiumAccount,
    SavingsAccount,
)


class SavingsAccountTests(unittest.TestCase):
    def test_interest_and_minimum_balance(self) -> None:
        account = SavingsAccount(
            "Анна", initial_balance="1000", min_balance="200",
            monthly_interest_rate="0.01",
        )

        self.assertEqual(account.apply_monthly_interest(), Decimal("10.00"))
        self.assertEqual(account.withdraw("810"), Decimal("200.00"))
        with self.assertRaises(InsufficientFundsError):
            account.withdraw("0.01")
        self.assertEqual(account.balance, Decimal("200.00"))

    def test_invalid_savings_settings(self) -> None:
        cases = (
            {"initial_balance": "99", "min_balance": "100"},
            {"min_balance": "-1"},
            {"monthly_interest_rate": "-0.01"},
            {"monthly_interest_rate": "Infinity"},
        )
        for settings in cases:
            with self.subTest(settings=settings):
                with self.assertRaises(InvalidOperationError):
                    SavingsAccount("Анна", **settings)

    def test_frozen_savings_cannot_accrue_interest(self) -> None:
        account = SavingsAccount(
            "Анна", initial_balance="100", status=AccountStatus.FROZEN
        )

        with self.assertRaises(AccountFrozenError):
            account.apply_monthly_interest()
        self.assertEqual(account.balance, Decimal("100"))

    def test_savings_info_and_text(self) -> None:
        account = SavingsAccount(
            "Анна", initial_balance="500", min_balance="100",
            monthly_interest_rate="0.02",
        )

        info = account.get_account_info()
        self.assertEqual(info["account_type"], "SavingsAccount")
        self.assertEqual(info["min_balance"], Decimal("100"))
        self.assertEqual(info["monthly_interest_rate"], Decimal("0.02"))
        self.assertIn("доходность 2.00%/мес", str(account))


class PremiumAccountTests(unittest.TestCase):
    def test_higher_limits_than_standard_account(self) -> None:
        standard = BankAccount("Олег", initial_balance="20000")
        premium = PremiumAccount("Вера", initial_balance="20000")

        with self.assertRaises(InvalidOperationError):
            standard.deposit("10001")
        with self.assertRaises(InvalidOperationError):
            standard.withdraw("6000")
        self.assertEqual(premium.deposit("10001"), Decimal("30001"))
        self.assertEqual(premium.withdraw("6000"), Decimal("23991"))

    def test_overdraft_and_fixed_withdrawal_fee(self) -> None:
        account = PremiumAccount(
            "Вера", initial_balance="100", overdraft_limit="500",
            withdrawal_fee="10",
        )

        self.assertEqual(account.withdraw("300"), Decimal("-210"))
        self.assertEqual(account.withdraw("280"), Decimal("-500"))
        with self.assertRaises(InsufficientFundsError):
            account.withdraw("1")
        self.assertEqual(account.balance, Decimal("-500"))
        self.assertEqual(account.deposit("100"), Decimal("-400"))

    def test_invalid_premium_settings_and_limit(self) -> None:
        for settings in ({"overdraft_limit": -1}, {"withdrawal_fee": "NaN"}):
            with self.subTest(settings=settings):
                with self.assertRaises(InvalidOperationError):
                    PremiumAccount("Вера", **settings)

        account = PremiumAccount("Вера", initial_balance="100000")
        with self.assertRaises(InvalidOperationError):
            account.withdraw("50001")

    def test_premium_info_and_text(self) -> None:
        account = PremiumAccount("Вера")

        info = account.get_account_info()
        self.assertEqual(info["account_type"], "PremiumAccount")
        self.assertEqual(info["deposit_limit"], Decimal("100000"))
        self.assertEqual(info["withdrawal_limit"], Decimal("50000"))
        self.assertEqual(info["overdraft_limit"], Decimal("2000"))
        self.assertIn("комиссия за снятие 10", str(account))


class InvestmentAccountTests(unittest.TestCase):
    def test_allocation_and_yearly_growth(self) -> None:
        account = InvestmentAccount("Дина", initial_balance="1000")
        account.allocate_to_asset("stocks", "400")
        account.allocate_to_asset("bonds", "300")
        account.allocate_to_asset("etf", "200")

        self.assertEqual(account.balance, Decimal("100"))
        self.assertEqual(account.total_value, Decimal("1000"))
        self.assertEqual(
            account.project_yearly_growth(
                {"stocks": "0.10", "bonds": "0.04", "etf": "0.07"}
            ),
            Decimal("66.00"),
        )
        self.assertEqual(account.total_value, Decimal("1000"))

    def test_invalid_allocation_preserves_cash_and_portfolio(self) -> None:
        account = InvestmentAccount("Дина", initial_balance="100")

        for asset, amount, error in (
            ("crypto", "10", InvalidOperationError),
            ("stocks", "0", InvalidOperationError),
            ("stocks", "Infinity", InvalidOperationError),
            ("stocks", "101", InsufficientFundsError),
        ):
            with self.subTest(asset=asset, amount=amount):
                with self.assertRaises(error):
                    account.allocate_to_asset(asset, amount)
        self.assertEqual(account.balance, Decimal("100"))
        self.assertEqual(account.portfolio["stocks"], Decimal("0"))

    def test_only_unallocated_cash_can_be_withdrawn(self) -> None:
        account = InvestmentAccount("Дина", initial_balance="100")
        account.allocate_to_asset("stocks", "80")

        with self.assertRaises(InsufficientFundsError):
            account.withdraw("30")
        self.assertEqual(account.withdraw("20"), Decimal("0"))
        self.assertEqual(account.total_value, Decimal("80"))

    def test_growth_rate_validation_and_negative_projection(self) -> None:
        account = InvestmentAccount("Дина", initial_balance="100")
        account.allocate_to_asset("stocks", "100")

        for rates in (
            {},
            {"stocks": "0.10", "crypto": "0.20"},
            {"stocks": "Infinity"},
            {"stocks": 0.1},
        ):
            with self.subTest(rates=rates):
                with self.assertRaises(InvalidOperationError):
                    account.project_yearly_growth(rates)
        self.assertEqual(
            account.project_yearly_growth({"stocks": "-0.10"}),
            Decimal("-10.00"),
        )

    def test_portfolio_copy_info_and_text(self) -> None:
        account = InvestmentAccount("Дина", initial_balance="100")
        account.allocate_to_asset("etf", "40")
        copy = account.portfolio
        copy["etf"] = Decimal("999")

        self.assertEqual(account.portfolio["etf"], Decimal("40"))
        info = account.get_account_info()
        self.assertEqual(info["account_type"], "InvestmentAccount")
        self.assertEqual(info["portfolio"]["etf"], Decimal("40"))
        self.assertEqual(info["total_value"], Decimal("100"))
        self.assertIn("в портфеле 40", str(account))

    def test_frozen_investment_cannot_allocate(self) -> None:
        account = InvestmentAccount(
            "Дина", initial_balance="100", status=AccountStatus.FROZEN
        )

        with self.assertRaises(AccountFrozenError):
            account.allocate_to_asset("stocks", "10")


class PolymorphismTests(unittest.TestCase):
    def test_same_method_uses_each_account_type_rules(self) -> None:
        accounts: list[AbstractAccount] = [
            SavingsAccount("Анна", initial_balance="100", min_balance="50"),
            PremiumAccount("Вера", initial_balance="100", withdrawal_fee="10"),
            InvestmentAccount("Дина", initial_balance="100"),
        ]

        balances = [account.withdraw("20") for account in accounts]
        self.assertEqual(
            balances, [Decimal("80"), Decimal("70"), Decimal("80")]
        )
        self.assertEqual(
            [account.get_account_info()["account_type"] for account in accounts],
            ["SavingsAccount", "PremiumAccount", "InvestmentAccount"],
        )
        self.assertTrue(
            all(type(account).__name__ in str(account) for account in accounts)
        )

    def test_closed_account_rule_applies_to_premium_override(self) -> None:
        account = PremiumAccount("Вера", status=AccountStatus.CLOSED)

        with self.assertRaises(AccountClosedError):
            account.withdraw("1")


if __name__ == "__main__":
    unittest.main()
