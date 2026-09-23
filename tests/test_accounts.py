"""Проверки работы счетов."""

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
)


class BankAccountTests(unittest.TestCase):
    def test_abstract_class_cannot_be_created(self) -> None:
        with self.assertRaises(TypeError):
            AbstractAccount("Роман")

    def test_generated_number_and_unique_identifier(self) -> None:
        first = BankAccount("Роман")
        second = BankAccount("Мария")

        self.assertEqual(len(first.account_number), 12)
        self.assertTrue(first.account_number.isascii())
        self.assertTrue(first.account_number.isdigit())
        self.assertNotEqual(first.account_id, second.account_id)

    def test_deposit_and_withdraw(self) -> None:
        account = BankAccount("Роман", initial_balance="100.10")

        self.assertEqual(account.deposit("0.20"), Decimal("100.30"))
        self.assertEqual(account.withdraw("25.30"), Decimal("75.00"))
        self.assertEqual(account.balance, Decimal("75.00"))

    def test_frozen_and_closed_accounts_reject_operations(self) -> None:
        frozen = BankAccount("Мария", status=AccountStatus.FROZEN)
        closed = BankAccount("Иван", status=AccountStatus.CLOSED)

        for operation in (frozen.deposit, frozen.withdraw):
            with self.assertRaises(AccountFrozenError):
                operation(1)
        for operation in (closed.deposit, closed.withdraw):
            with self.assertRaises(AccountClosedError):
                operation(1)

    def test_invalid_amount_does_not_change_balance(self) -> None:
        account = BankAccount("Роман", initial_balance="10")

        for amount in (0, -1, "NaN", "Infinity", "abc", True, 0.1):
            with self.subTest(amount=amount):
                with self.assertRaises(InvalidOperationError):
                    account.deposit(amount)
        self.assertEqual(account.balance, Decimal("10"))

    def test_insufficient_funds_does_not_change_balance(self) -> None:
        account = BankAccount("Роман", initial_balance="10")

        with self.assertRaises(InsufficientFundsError):
            account.withdraw("10.01")
        self.assertEqual(account.balance, Decimal("10"))

    def test_invalid_account_data(self) -> None:
        invalid_cases = (
            {"owner": "  "},
            {"owner": "Роман", "initial_balance": -1},
            {"owner": "Роман", "status": "unknown"},
            {"owner": "Роман", "status": []},
            {"owner": "Роман", "currency": "GBP"},
            {"owner": "Роман", "currency": []},
            {"owner": "Роман", "account_number": "123X"},
        )
        for kwargs in invalid_cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(InvalidOperationError):
                    BankAccount(**kwargs)

    def test_string_representation_masks_account_number(self) -> None:
        account = BankAccount(
            "Роман", initial_balance="10.50", account_number="12345678", currency="EUR"
        )

        self.assertEqual(
            str(account),
            "BankAccount: Роман | счёт ****5678 | active | 10.50 EUR",
        )
        self.assertNotIn("12345678", str(account))
        self.assertEqual(account.get_account_info()["account_number"], "12345678")


if __name__ == "__main__":
    unittest.main()
