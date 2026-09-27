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


class AuthenticationError(BankAccountError):
    """Клиент не прошёл проверку пароля или заблокирован."""


class AccessDeniedError(BankAccountError):
    """Клиент попытался обратиться к чужому счёту."""


class OperatingHoursError(BankAccountError):
    """Операция запрещена в ночные часы."""


class RetryableTransactionError(BankAccountError):
    """Временная ошибка обработки, после которой допустима повторная попытка."""
