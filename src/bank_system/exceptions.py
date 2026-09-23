"""Ошибки операций со счетами."""


class BankAccountError(Exception):
    """Базовая ошибка учебной модели счёта."""


class AccountFrozenError(BankAccountError):
    """Операция запрещена: счёт заморожен."""


class AccountClosedError(BankAccountError):
    """Операция запрещена: счёт закрыт."""


class InvalidOperationError(BankAccountError):
    """Некорректные данные счёта или сумма операции."""


class InsufficientFundsError(BankAccountError):
    """Для снятия недостаточно средств."""
