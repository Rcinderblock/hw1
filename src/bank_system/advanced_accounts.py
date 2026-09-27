"""Сберегательный, премиальный и инвестиционный счета."""

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from .accounts import AccountStatus, Amount, BankAccount, Currency, _valid_amount
from .exceptions import InsufficientFundsError, InvalidOperationError


def _valid_rate(value: Amount, *, allow_negative: bool = False) -> Decimal:
    """Проверить конечную ставку, переданную как долю единицы."""
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise InvalidOperationError("Ставка должна быть Decimal, int или строкой")
    try:
        rate = Decimal(str(value))
    except InvalidOperation as exc:
        raise InvalidOperationError("Ставка должна быть числом") from exc
    if not rate.is_finite() or (rate < 0 and not allow_negative):
        raise InvalidOperationError("Недопустимая ставка")
    return rate


class SavingsAccount(BankAccount):
    """Счёт с минимальным остатком и месячным начислением дохода."""

    def __init__(
        self,
        owner: str,
        initial_balance: Amount = 0,
        status: AccountStatus | str = AccountStatus.ACTIVE,
        account_number: str | None = None,
        currency: Currency | str = Currency.RUB,
        min_balance: Amount = 0,
        monthly_interest_rate: Amount = "0.01",
    ) -> None:
        super().__init__(owner, initial_balance, status, account_number, currency)
        self._min_balance = _valid_amount(min_balance, allow_zero=True)
        self._monthly_interest_rate = _valid_rate(monthly_interest_rate)
        if self.balance < self._min_balance:
            raise InvalidOperationError("Начальный баланс меньше минимального остатка")

    @property
    def min_balance(self) -> Decimal:
        return self._min_balance

    @property
    def monthly_interest_rate(self) -> Decimal:
        return self._monthly_interest_rate

    def apply_monthly_interest(self) -> Decimal:
        """Начислить доход на текущий баланс и вернуть сумму начисления."""
        self._ensure_active()
        interest = self.balance * self.monthly_interest_rate
        self._balance += interest
        return interest

    def withdraw(self, amount: Amount) -> Decimal:
        self._ensure_active()
        value = _valid_amount(amount)
        if self.balance - value < self.min_balance:
            raise InsufficientFundsError("Снятие нарушит минимальный остаток")
        return super().withdraw(value)

    def _check_transfer_out(self, amount: Decimal, fee: Decimal) -> None:
        super()._check_transfer_out(amount, fee)
        if self.balance - amount - fee < self.min_balance:
            raise InsufficientFundsError("Перевод нарушит минимальный остаток")

    def get_account_info(self) -> dict[str, object]:
        info = super().get_account_info()
        info.update(
            min_balance=self.min_balance,
            monthly_interest_rate=self.monthly_interest_rate,
        )
        return info

    def __str__(self) -> str:
        percent = self.monthly_interest_rate * 100
        return (
            f"{super().__str__()} | минимум {self.min_balance} "
            f"{self.currency.value} | доходность {percent}%/мес"
        )


class PremiumAccount(BankAccount):
    """Счёт с повышенными лимитами, овердрафтом и платным снятием."""

    MAX_DEPOSIT = Decimal("100000")
    MAX_WITHDRAWAL = Decimal("50000")

    def __init__(
        self,
        owner: str,
        initial_balance: Amount = 0,
        status: AccountStatus | str = AccountStatus.ACTIVE,
        account_number: str | None = None,
        currency: Currency | str = Currency.RUB,
        overdraft_limit: Amount = "2000",
        withdrawal_fee: Amount = "10",
    ) -> None:
        super().__init__(owner, initial_balance, status, account_number, currency)
        self._overdraft_limit = _valid_amount(overdraft_limit, allow_zero=True)
        self._withdrawal_fee = _valid_amount(withdrawal_fee, allow_zero=True)

    @property
    def overdraft_limit(self) -> Decimal:
        return self._overdraft_limit

    @property
    def withdrawal_fee(self) -> Decimal:
        return self._withdrawal_fee

    def withdraw(self, amount: Amount) -> Decimal:
        self._ensure_active()
        value = _valid_amount(amount)
        if value > self.MAX_WITHDRAWAL:
            raise InvalidOperationError("Превышен лимит снятия")
        total_debit = value + self.withdrawal_fee
        if self.balance - total_debit < -self.overdraft_limit:
            raise InsufficientFundsError("Превышен допустимый овердрафт")
        self._balance -= total_debit
        return self.balance

    def _check_transfer_out(self, amount: Decimal, fee: Decimal) -> None:
        self._ensure_active()
        if amount > self.MAX_WITHDRAWAL:
            raise InvalidOperationError("Превышен лимит перевода")
        if self.balance - amount - fee < -self.overdraft_limit:
            raise InsufficientFundsError("Превышен допустимый овердрафт")

    def get_account_info(self) -> dict[str, object]:
        info = super().get_account_info()
        info.update(
            overdraft_limit=self.overdraft_limit,
            withdrawal_fee=self.withdrawal_fee,
        )
        return info

    def __str__(self) -> str:
        return (
            f"{super().__str__()} | овердрафт до {self.overdraft_limit} "
            f"{self.currency.value} | комиссия за снятие "
            f"{self.withdrawal_fee} {self.currency.value}"
        )


class InvestmentAccount(BankAccount):
    """Счёт со свободными средствами и виртуальным портфелем."""

    ASSET_TYPES = ("stocks", "bonds", "etf")

    def __init__(
        self,
        owner: str,
        initial_balance: Amount = 0,
        status: AccountStatus | str = AccountStatus.ACTIVE,
        account_number: str | None = None,
        currency: Currency | str = Currency.RUB,
    ) -> None:
        super().__init__(owner, initial_balance, status, account_number, currency)
        self._portfolio = {asset: Decimal(0) for asset in self.ASSET_TYPES}

    @property
    def portfolio(self) -> dict[str, Decimal]:
        """Вернуть копию распределения, чтобы его нельзя было менять снаружи."""
        return dict(self._portfolio)

    @property
    def total_value(self) -> Decimal:
        return self.balance + sum(self._portfolio.values(), Decimal(0))

    def allocate_to_asset(self, asset_type: str, amount: Amount) -> Decimal:
        """Переместить свободные средства в виртуальный тип актива."""
        self._ensure_active()
        if asset_type not in self.ASSET_TYPES:
            raise InvalidOperationError("Допустимые активы: stocks, bonds, etf")
        value = _valid_amount(amount)
        if value > self.balance:
            raise InsufficientFundsError("Недостаточно свободных средств")
        self._balance -= value
        self._portfolio[asset_type] += value
        return self._portfolio[asset_type]

    def release_from_asset(self, asset_type: str, amount: Amount) -> Decimal:
        """Вернуть виртуальную сумму из портфеля в свободные средства."""
        self._ensure_active()
        if asset_type not in self.ASSET_TYPES:
            raise InvalidOperationError("Допустимые активы: stocks, bonds, etf")
        value = _valid_amount(amount)
        if value > self._portfolio[asset_type]:
            raise InsufficientFundsError("В выбранном активе недостаточно средств")
        self._portfolio[asset_type] -= value
        self._balance += value
        return self._portfolio[asset_type]

    def withdraw(self, amount: Amount) -> Decimal:
        self._ensure_active()
        value = _valid_amount(amount)
        if value > self.balance:
            raise InsufficientFundsError(
                "Снять можно только свободные средства, не средства портфеля"
            )
        return super().withdraw(value)

    def close(self) -> Decimal:
        if any(self._portfolio.values()):
            raise InvalidOperationError("Перед закрытием освободите портфель")
        return super().close()

    def project_yearly_growth(self, growth_rates: Mapping[str, Amount]) -> Decimal:
        """Вернуть ожидаемый прирост портфеля без изменения его стоимости."""
        if not isinstance(growth_rates, Mapping):
            raise InvalidOperationError("Ставки должны быть словарём типов активов")
        unknown = set(growth_rates) - set(self.ASSET_TYPES)
        if unknown:
            raise InvalidOperationError("Передана ставка для неизвестного актива")
        rates = {
            asset: _valid_rate(rate, allow_negative=True)
            for asset, rate in growth_rates.items()
        }
        for asset, amount in self._portfolio.items():
            if amount > 0 and asset not in rates:
                raise InvalidOperationError(f"Не задана ставка для {asset}")
        return sum(
            (
                amount * rates[asset]
                for asset, amount in self._portfolio.items()
                if amount > 0
            ),
            Decimal(0),
        )

    def get_account_info(self) -> dict[str, object]:
        info = super().get_account_info()
        info.update(portfolio=self.portfolio, total_value=self.total_value)
        return info

    def __str__(self) -> str:
        invested = sum(self._portfolio.values(), Decimal(0))
        return (
            f"{super().__str__()} | в портфеле {invested} "
            f"{self.currency.value} | всего {self.total_value} "
            f"{self.currency.value}"
        )
