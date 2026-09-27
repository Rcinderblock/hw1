"""Клиенты, управление счетами и учебные правила доступа к банку."""

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from secrets import token_bytes
from uuid import uuid4

from .accounts import AccountStatus, Amount, BankAccount, Currency
from .advanced_accounts import InvestmentAccount, SavingsAccount
from .exceptions import (
    AccessDeniedError,
    AuthenticationError,
    InvalidOperationError,
    OperatingHoursError,
)


class ClientStatus(str, Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class SecurityEvent:
    """Пометка действия, которое банк считает подозрительным."""

    occurred_at: datetime
    client_id: str
    action: str
    reason: str


@dataclass(frozen=True)
class BalancePoint:
    """Остаток счёта после успешной операции банка."""

    occurred_at: datetime
    account_number: str
    balance: Decimal
    total_value: Decimal
    currency: Currency
    operation: str


class Client:
    """Владелец счетов с защищённым паролем и состоянием входа."""

    def __init__(
        self,
        full_name: str,
        age: int,
        contacts: Mapping[str, str],
        password: str,
    ) -> None:
        if not isinstance(full_name, str) or not full_name.strip():
            raise InvalidOperationError("ФИО не может быть пустым")
        if isinstance(age, bool) or not isinstance(age, int) or age < 18:
            raise InvalidOperationError("Клиент должен быть не младше 18 лет")
        if not isinstance(contacts, Mapping) or not contacts:
            raise InvalidOperationError("Нужен хотя бы один контакт")
        if any(
            not isinstance(key, str) or not key.strip()
            or not isinstance(value, str) or not value.strip()
            for key, value in contacts.items()
        ):
            raise InvalidOperationError("Контакты должны содержать непустые строки")
        if not isinstance(password, str) or not password:
            raise InvalidOperationError("Пароль не может быть пустым")

        self._client_id = str(uuid4())
        self._full_name = full_name.strip()
        self._age = age
        self._contacts = {key.strip(): value.strip()
                          for key, value in contacts.items()}
        self._status = ClientStatus.ACTIVE
        self._account_numbers: list[str] = []
        self._failed_attempts = 0
        self._suspicious_actions: list[SecurityEvent] = []
        self._password_salt = token_bytes(16)
        self._password_hash = pbkdf2_hmac(
            "sha256", password.encode("utf-8"), self._password_salt, 100_000
        )

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def full_name(self) -> str:
        return self._full_name

    @property
    def age(self) -> int:
        return self._age

    @property
    def status(self) -> ClientStatus:
        return self._status

    @property
    def contacts(self) -> dict[str, str]:
        return dict(self._contacts)

    @property
    def account_numbers(self) -> list[str]:
        return list(self._account_numbers)

    @property
    def failed_attempts(self) -> int:
        return self._failed_attempts

    @property
    def suspicious_actions(self) -> tuple[SecurityEvent, ...]:
        return tuple(self._suspicious_actions)

    @property
    def has_suspicious_activity(self) -> bool:
        return bool(self._suspicious_actions)

    def _authenticate(self, password: str) -> bool:
        if self.status is ClientStatus.BLOCKED:
            return False
        if not isinstance(password, str):
            password = ""
        candidate = pbkdf2_hmac(
            "sha256", password.encode("utf-8"), self._password_salt, 100_000
        )
        if compare_digest(candidate, self._password_hash):
            self._failed_attempts = 0
            return True
        self._failed_attempts += 1
        if self._failed_attempts >= 3:
            self._status = ClientStatus.BLOCKED
        return False

    def _add_account(self, account_number: str) -> None:
        self._account_numbers.append(account_number)

    def _mark_suspicious(self, event: SecurityEvent) -> None:
        self._suspicious_actions.append(event)


class Bank:
    """Управлять счетами клиентов через проверяемые операции."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clients: dict[str, Client] = {}
        self._accounts: dict[str, BankAccount] = {}
        self._clock = clock or datetime.now
        self._security_events: list[SecurityEvent] = []
        self._balance_history: list[BalancePoint] = []

    @property
    def security_events(self) -> tuple[SecurityEvent, ...]:
        return tuple(self._security_events)

    def current_time(self) -> datetime:
        return self._clock()

    def _record_balance(self, account: BankAccount, operation: str) -> None:
        self._balance_history.append(
            BalancePoint(
                self._clock(), account.account_number, account.balance,
                account.total_value, account.currency, operation,
            )
        )

    def get_balance_history(
        self, client_id: str, password: str, account_number: str
    ) -> tuple[BalancePoint, ...]:
        """Показать клиенту снимки его счёта после проверки пароля."""
        client = self._authorized_client(client_id, password)
        self._owned_account(client, account_number)
        return tuple(
            point for point in self._balance_history
            if point.account_number == account_number
        )

    def get_client_profile(self, client_id: str, password: str) -> dict[str, str]:
        """Вернуть основные данные вошедшего клиента для его отчёта."""
        client = self._authorized_client(client_id, password)
        return {
            "client_id": client.client_id,
            "full_name": client.full_name,
            "status": client.status.value,
        }

    def get_account_statistics(self) -> dict[str, object]:
        """Сводка без персональных данных для внутренних отчётов банка."""
        accounts = tuple(self._accounts.values())
        return {
            "clients": len(self._clients),
            "accounts": len(accounts),
            "by_type": dict(sorted(Counter(
                type(account).__name__ for account in accounts
            ).items())),
            "by_status": dict(sorted(Counter(
                account.status.value for account in accounts
            ).items())),
            "by_currency": dict(sorted(Counter(
                account.currency.value for account in accounts
            ).items())),
        }

    def add_client(
        self,
        full_name: str,
        age: int,
        contacts: Mapping[str, str],
        password: str,
    ) -> Client:
        client = Client(full_name, age, contacts, password)
        self._clients[client.client_id] = client
        return client

    def authenticate_client(self, client_id: str, password: str) -> bool:
        client = self._clients.get(client_id)
        if client is None:
            return False
        was_blocked = client.status is ClientStatus.BLOCKED
        valid = client._authenticate(password)
        if not valid and not was_blocked:
            reason = (
                "Три неверных пароля: клиент заблокирован"
                if client.status is ClientStatus.BLOCKED
                else "Неверный пароль"
            )
            self._record_suspicious(client, "authenticate", reason)
        return valid

    def _record_suspicious(
        self, client: Client, action: str, reason: str
    ) -> None:
        event = SecurityEvent(self._clock(), client.client_id, action, reason)
        client._mark_suspicious(event)
        self._security_events.append(event)

    def _authorized_client(self, client_id: str, password: str) -> Client:
        if not self.authenticate_client(client_id, password):
            raise AuthenticationError("Неверный пароль или клиент заблокирован")
        return self._clients[client_id]

    def _ensure_operating_hours(self, client: Client, action: str) -> None:
        hour = self._clock().hour
        if 0 <= hour < 5:
            self._record_suspicious(
                client, action, "Операция запрошена с 00:00 до 05:00"
            )
            raise OperatingHoursError("Операции запрещены с 00:00 до 05:00")

    def _owned_account(self, client: Client, account_number: str) -> BankAccount:
        account = self._accounts.get(account_number)
        if account is None:
            raise InvalidOperationError("Счёт не найден")
        if account_number not in client.account_numbers:
            self._record_suspicious(
                client, "account_access", "Попытка доступа к чужому счёту"
            )
            raise AccessDeniedError("Счёт принадлежит другому клиенту")
        return account

    def _account_operation(
        self, client_id: str, password: str, account_number: str, action: str
    ) -> BankAccount:
        client = self._authorized_client(client_id, password)
        self._ensure_operating_hours(client, action)
        return self._owned_account(client, account_number)

    def open_account(
        self,
        client_id: str,
        password: str,
        account_type: type[BankAccount] = BankAccount,
        **options: object,
    ) -> str:
        client = self._authorized_client(client_id, password)
        self._ensure_operating_hours(client, "open_account")
        if not isinstance(account_type, type) or not issubclass(
            account_type, BankAccount
        ):
            raise InvalidOperationError("Нужен конкретный тип банковского счёта")
        if "owner" in options or "status" in options:
            raise InvalidOperationError("Владельца и статус задаёт банк")
        account = account_type(client.full_name, **options)
        if account.account_number in self._accounts:
            raise InvalidOperationError("Номер счёта уже используется")
        self._accounts[account.account_number] = account
        client._add_account(account.account_number)
        self._record_balance(account, "open")
        return account.account_number

    def close_account(
        self, client_id: str, password: str, account_number: str
    ) -> Decimal:
        account = self._account_operation(
            client_id, password, account_number, "close_account"
        )
        payout = account.close()
        self._record_balance(account, "close")
        return payout

    def freeze_account(
        self, client_id: str, password: str, account_number: str
    ) -> None:
        account = self._account_operation(
            client_id, password, account_number, "freeze_account"
        )
        account.freeze()

    def unfreeze_account(
        self, client_id: str, password: str, account_number: str
    ) -> None:
        account = self._account_operation(
            client_id, password, account_number, "unfreeze_account"
        )
        account.unfreeze()

    def deposit(
        self, client_id: str, password: str, account_number: str, amount: Amount
    ) -> Decimal:
        account = self._account_operation(
            client_id, password, account_number, "deposit"
        )
        balance = account.deposit(amount)
        self._record_balance(account, "deposit")
        return balance

    def withdraw(
        self, client_id: str, password: str, account_number: str, amount: Amount
    ) -> Decimal:
        account = self._account_operation(
            client_id, password, account_number, "withdraw"
        )
        balance = account.withdraw(amount)
        self._record_balance(account, "withdraw")
        return balance

    def _transfer(
        self,
        sender_number: str,
        recipient_number: str,
        amount: Decimal,
        received: Decimal,
        fee: Decimal,
    ) -> None:
        """Проверить оба счёта, затем записать их новые остатки."""
        sender = self._accounts.get(sender_number)
        recipient = self._accounts.get(recipient_number)
        if sender is None or recipient is None or sender is recipient:
            raise InvalidOperationError("Неверные счета перевода")
        sender._check_transfer_out(amount, fee)
        recipient._check_transfer_in()
        # После проверок вычисления не вызывают внешних действий; оба остатка
        # меняются только на завершающем шаге учебной модели в памяти.
        new_sender_balance = sender.balance - amount - fee
        new_recipient_balance = recipient.balance + received
        sender._balance = new_sender_balance
        recipient._balance = new_recipient_balance
        self._record_balance(sender, "transfer_out")
        self._record_balance(recipient, "transfer_in")

    def allocate_to_asset(
        self,
        client_id: str,
        password: str,
        account_number: str,
        asset_type: str,
        amount: Amount,
    ) -> Decimal:
        account = self._account_operation(
            client_id, password, account_number, "allocate_to_asset"
        )
        if not isinstance(account, InvestmentAccount):
            raise InvalidOperationError("Это не инвестиционный счёт")
        allocated = account.allocate_to_asset(asset_type, amount)
        self._record_balance(account, "allocate_to_asset")
        return allocated

    def release_from_asset(
        self,
        client_id: str,
        password: str,
        account_number: str,
        asset_type: str,
        amount: Amount,
    ) -> Decimal:
        account = self._account_operation(
            client_id, password, account_number, "release_from_asset"
        )
        if not isinstance(account, InvestmentAccount):
            raise InvalidOperationError("Это не инвестиционный счёт")
        remaining = account.release_from_asset(asset_type, amount)
        self._record_balance(account, "release_from_asset")
        return remaining

    def apply_monthly_interest(
        self, client_id: str, password: str, account_number: str
    ) -> Decimal:
        account = self._account_operation(
            client_id, password, account_number, "apply_monthly_interest"
        )
        if not isinstance(account, SavingsAccount):
            raise InvalidOperationError("Это не сберегательный счёт")
        interest = account.apply_monthly_interest()
        self._record_balance(account, "interest")
        return interest

    def search_accounts(
        self,
        client_id: str,
        password: str,
        *,
        status: AccountStatus | str | None = None,
        currency: Currency | str | None = None,
    ) -> list[dict[str, object]]:
        client = self._authorized_client(client_id, password)
        try:
            selected_status = AccountStatus(status) if status is not None else None
            selected_currency = Currency(currency) if currency is not None else None
        except (TypeError, ValueError) as exc:
            raise InvalidOperationError("Неизвестный статус или валюта") from exc
        result = []
        for number in client.account_numbers:
            account = self._accounts[number]
            if selected_status is not None and account.status is not selected_status:
                continue
            if (
                selected_currency is not None
                and account.currency is not selected_currency
            ):
                continue
            result.append(account.get_account_info())
        return result

    def get_total_balance(self) -> dict[str, Decimal]:
        """Суммировать стоимость счетов отдельно по каждой валюте."""
        totals: dict[str, Decimal] = {}
        for account in self._accounts.values():
            currency = account.currency.value
            totals[currency] = totals.get(currency, Decimal(0)) + account.total_value
        return totals

    def get_clients_ranking(self) -> dict[str, list[dict[str, object]]]:
        """Отсортировать клиентов по стоимости счетов внутри каждой валюты."""
        ranking: dict[str, list[dict[str, object]]] = {}
        for client in self._clients.values():
            totals: dict[str, Decimal] = {}
            for number in client.account_numbers:
                account = self._accounts[number]
                currency = account.currency.value
                totals[currency] = totals.get(
                    currency, Decimal(0)
                ) + account.total_value
            for currency, total in totals.items():
                ranking.setdefault(currency, []).append(
                    {
                        "client_id": client.client_id,
                        "full_name": client.full_name,
                        "total": total,
                    }
                )
        for rows in ranking.values():
            rows.sort(key=lambda row: (-row["total"], row["full_name"]))
        return ranking
