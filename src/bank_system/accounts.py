"""Абстрактный счёт и его базовая реализация."""

from abc import ABC, abstractmethod
from decimal import Decimal, InvalidOperation
from enum import Enum
from re import fullmatch
from uuid import UUID, uuid4

from .exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)

Amount = Decimal | int | str


class AccountStatus(str, Enum):
    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"


class Currency(str, Enum):
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"
    KZT = "KZT"
    CNY = "CNY"


def _valid_amount(value: Amount, *, allow_zero: bool = False) -> Decimal:
    """Преобразовать сумму в Decimal без неявного округления float."""
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise InvalidOperationError("Сумма должна быть Decimal, int или строкой")

    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise InvalidOperationError("Сумма должна быть числом") from exc

    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        requirement = "неотрицательной" if allow_zero else "положительной"
        raise InvalidOperationError(f"Сумма должна быть конечной и {requirement}")

    return amount


class AbstractAccount(ABC):
    """Общие данные и правила доступа для будущих типов счетов."""

    def __init__(
        self,
        owner: str,
        initial_balance: Amount = 0,
        status: AccountStatus | str = AccountStatus.ACTIVE,
        account_number: str | None = None,
    ) -> None:
        if not isinstance(owner, str) or not owner.strip():
            raise InvalidOperationError("Имя владельца не может быть пустым")

        try:
            parsed_status = AccountStatus(status)
        except (TypeError, ValueError) as exc:
            raise InvalidOperationError("Неизвестный статус счёта") from exc

        self._account_id: UUID = uuid4()
        if account_number is None:
            # Номер получается из UUID, а полный UUID остаётся уникальным ID.
            account_number = f"{self._account_id.int % 10**12:012d}"
        elif not isinstance(account_number, str) or not fullmatch(
            r"[0-9]{4,20}", account_number
        ):
            raise InvalidOperationError("Номер счёта: от 4 до 20 цифр")

        self._account_number = account_number
        self._owner = owner.strip()
        self._balance = _valid_amount(initial_balance, allow_zero=True)
        self._status = parsed_status

    @property
    def account_id(self) -> UUID:
        return self._account_id

    @property
    def account_number(self) -> str:
        return self._account_number

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def balance(self) -> Decimal:
        return self._balance

    @property
    def status(self) -> AccountStatus:
        return self._status

    def _ensure_active(self) -> None:
        if self._status is AccountStatus.FROZEN:
            raise AccountFrozenError("Операция запрещена: счёт заморожен")
        if self._status is AccountStatus.CLOSED:
            raise AccountClosedError("Операция запрещена: счёт закрыт")

    @abstractmethod
    def deposit(self, amount: Amount) -> Decimal:
        """Пополнить счёт и вернуть новый баланс."""

    @abstractmethod
    def withdraw(self, amount: Amount) -> Decimal:
        """Снять деньги и вернуть новый баланс."""

    @abstractmethod
    def get_account_info(self) -> dict[str, object]:
        """Вернуть сведения о счёте для программного использования."""


class BankAccount(AbstractAccount):
    """Обычный счёт с проверкой статуса и суммы операций."""

    # Учебные лимиты на одну операцию; дочерние классы могут их увеличить.
    MAX_DEPOSIT = Decimal("10000")
    MAX_WITHDRAWAL = Decimal("5000")

    def __init__(
        self,
        owner: str,
        initial_balance: Amount = 0,
        status: AccountStatus | str = AccountStatus.ACTIVE,
        account_number: str | None = None,
        currency: Currency | str = Currency.RUB,
    ) -> None:
        super().__init__(owner, initial_balance, status, account_number)
        try:
            self._currency = Currency(currency)
        except (TypeError, ValueError) as exc:
            raise InvalidOperationError(
                "Валюта должна быть RUB, USD, EUR, KZT или CNY"
            ) from exc

    @property
    def currency(self) -> Currency:
        return self._currency

    def deposit(self, amount: Amount) -> Decimal:
        self._ensure_active()
        value = _valid_amount(amount)
        if value > self.MAX_DEPOSIT:
            raise InvalidOperationError("Превышен лимит пополнения")
        self._balance += value
        return self._balance

    def withdraw(self, amount: Amount) -> Decimal:
        self._ensure_active()
        value = _valid_amount(amount)
        if value > self.MAX_WITHDRAWAL:
            raise InvalidOperationError("Превышен лимит снятия")
        if value > self._balance:
            raise InsufficientFundsError("Недостаточно средств для снятия")
        self._balance -= value
        return self._balance

    def get_account_info(self) -> dict[str, object]:
        return {
            "account_type": type(self).__name__,
            "account_id": str(self.account_id),
            "account_number": self.account_number,
            "owner": self.owner,
            "status": self.status.value,
            "balance": self.balance,
            "currency": self.currency.value,
            "deposit_limit": self.MAX_DEPOSIT,
            "withdrawal_limit": self.MAX_WITHDRAWAL,
        }

    def __str__(self) -> str:
        return (
            f"{type(self).__name__}: {self.owner} | "
            f"счёт ****{self.account_number[-4:]} | "
            f"{self.status.value} | {self.balance} {self.currency.value}"
        )
