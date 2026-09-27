"""Очередь и обработка учебных переводов между счетами банка."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from heapq import heappop, heappush
from uuid import uuid4

from .accounts import Amount, Currency, _valid_amount
from .bank import Bank
from .exceptions import (
    BankAccountError,
    InvalidOperationError,
    RetryableTransactionError,
)


class TransactionType(str, Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class TransactionStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ProcessingError:
    occurred_at: datetime
    attempt: int
    reason: str


@dataclass
class Transaction:
    """Запрос на перевод и результат его обработки."""

    kind: TransactionType
    amount: Decimal
    currency: Currency
    sender: str
    recipient: str
    created_at: datetime
    scheduled_at: datetime
    priority: int = 0
    transaction_id: str = field(default_factory=lambda: str(uuid4()))
    fee: Decimal = field(default=Decimal(0), init=False)
    converted_amount: Decimal | None = field(default=None, init=False)
    status: TransactionStatus = field(
        default=TransactionStatus.PENDING, init=False
    )
    rejection_reason: str | None = field(default=None, init=False)
    updated_at: datetime = field(init=False)
    finished_at: datetime | None = field(default=None, init=False)
    attempts: int = field(default=0, init=False)
    errors: list[ProcessingError] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TransactionType):
            raise InvalidOperationError("Неизвестный тип транзакции")
        self.amount = _valid_amount(self.amount)
        if not isinstance(self.currency, Currency):
            raise InvalidOperationError("Неизвестная валюта транзакции")
        if not self.sender or not self.recipient or self.sender == self.recipient:
            raise InvalidOperationError("Нужны разные счета отправителя и получателя")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise InvalidOperationError("Приоритет должен быть целым числом")
        if (
            not isinstance(self.created_at, datetime)
            or not isinstance(self.scheduled_at, datetime)
            or self.created_at.tzinfo is not None
            or self.scheduled_at.tzinfo is not None
        ):
            raise InvalidOperationError(
                "Время транзакции должно быть без часового пояса"
            )
        self.updated_at = self.created_at


class TransactionQueue:
    """Держать будущие заявки отдельно от готовых, где важен приоритет."""

    def __init__(self) -> None:
        self._transactions: dict[str, Transaction] = {}
        self._scheduled: list[tuple[datetime, int, str]] = []
        self._ready: list[tuple[int, int, str]] = []
        self._sequence = 0

    def add(self, transaction: Transaction) -> None:
        if not isinstance(transaction, Transaction):
            raise InvalidOperationError("В очередь можно добавить только транзакцию")
        if transaction.transaction_id in self._transactions:
            raise InvalidOperationError("Транзакция уже добавлена")
        if transaction.status is not TransactionStatus.PENDING:
            raise InvalidOperationError("Транзакция уже обработана")
        self._transactions[transaction.transaction_id] = transaction
        self._schedule(transaction)

    def _schedule(self, transaction: Transaction) -> None:
        self._sequence += 1
        heappush(
            self._scheduled,
            (transaction.scheduled_at, self._sequence, transaction.transaction_id),
        )

    def get(self, transaction_id: str) -> Transaction:
        try:
            return self._transactions[transaction_id]
        except KeyError as exc:
            raise InvalidOperationError("Транзакция не найдена") from exc

    def cancel(self, transaction_id: str, at: datetime) -> Transaction:
        transaction = self.get(transaction_id)
        if transaction.status not in (
            TransactionStatus.PENDING, TransactionStatus.RETRYING
        ):
            raise InvalidOperationError("Отменить можно только ожидающую транзакцию")
        transaction.status = TransactionStatus.CANCELLED
        transaction.updated_at = at
        transaction.finished_at = at
        return transaction

    def retry(
        self, transaction: Transaction, at: datetime, scheduled_at: datetime
    ) -> None:
        if transaction.status is not TransactionStatus.PROCESSING:
            raise InvalidOperationError("Повторить можно только обрабатываемую заявку")
        transaction.status = TransactionStatus.RETRYING
        transaction.scheduled_at = scheduled_at
        transaction.updated_at = at
        self._schedule(transaction)

    def pop_ready(self, now: datetime) -> Transaction | None:
        while self._scheduled and self._scheduled[0][0] <= now:
            _, sequence, transaction_id = heappop(self._scheduled)
            transaction = self._transactions[transaction_id]
            if transaction.status in (
                TransactionStatus.PENDING, TransactionStatus.RETRYING
            ):
                heappush(
                    self._ready,
                    (-transaction.priority, sequence, transaction_id),
                )
        while self._ready:
            _, _, transaction_id = heappop(self._ready)
            transaction = self._transactions[transaction_id]
            if transaction.status in (
                TransactionStatus.PENDING, TransactionStatus.RETRYING
            ):
                transaction.status = TransactionStatus.PROCESSING
                transaction.updated_at = now
                return transaction
        return None


class TransactionProcessor:
    """Проводить переводы и повторять только явно временные ошибки."""

    def __init__(
        self,
        bank: Bank,
        queue: TransactionQueue,
        *,
        exchange_rates: Mapping[tuple[Currency, Currency], Amount] | None = None,
        external_fee: Amount = "10",
        max_attempts: int = 3,
        retry_delay: timedelta = timedelta(minutes=1),
        before_transfer: Callable[[Transaction], None] | None = None,
    ) -> None:
        if not isinstance(bank, Bank) or not isinstance(queue, TransactionQueue):
            raise InvalidOperationError("Нужны банк и очередь транзакций")
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or max_attempts < 1
        ):
            raise InvalidOperationError("Число попыток должно быть положительным")
        if not isinstance(retry_delay, timedelta) or retry_delay <= timedelta(0):
            raise InvalidOperationError("Задержка повтора должна быть положительной")
        if exchange_rates is not None and not isinstance(exchange_rates, Mapping):
            raise InvalidOperationError("Курсы должны быть словарём пар валют")
        self.bank = bank
        self.queue = queue
        self.external_fee = _valid_amount(external_fee, allow_zero=True)
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self.before_transfer = before_transfer
        self.exchange_rates: dict[tuple[Currency, Currency], Decimal] = {}
        for pair, value in (exchange_rates or {}).items():
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or not all(isinstance(currency, Currency) for currency in pair)
            ):
                raise InvalidOperationError("Ключ курса: пара валют Currency")
            self.exchange_rates[pair] = _valid_amount(value)

    def submit(
        self,
        client_id: str,
        password: str,
        sender: str,
        recipient: str,
        amount: Amount,
        *,
        kind: TransactionType = TransactionType.INTERNAL,
        priority: int = 0,
        scheduled_at: datetime | None = None,
    ) -> Transaction:
        """Проверить владельца отправляющего счёта и поставить перевод в очередь."""
        client = self.bank._authorized_client(client_id, password)
        self.bank._ensure_operating_hours(client, "submit_transaction")
        source = self.bank._owned_account(client, sender)
        if recipient not in self.bank._accounts:
            raise InvalidOperationError("Счёт получателя не найден")
        try:
            selected_kind = TransactionType(kind)
        except (TypeError, ValueError) as exc:
            raise InvalidOperationError("Неизвестный тип транзакции") from exc
        now = self.bank._clock()
        transaction = Transaction(
            kind=selected_kind,
            amount=_valid_amount(amount),
            currency=source.currency,
            sender=sender,
            recipient=recipient,
            created_at=now,
            scheduled_at=now if scheduled_at is None else scheduled_at,
            priority=priority,
        )
        self.queue.add(transaction)
        return transaction

    def cancel(
        self, client_id: str, password: str, transaction_id: str
    ) -> Transaction:
        """Отменить собственную ожидающую заявку после проверки клиента."""
        client = self.bank._authorized_client(client_id, password)
        self.bank._ensure_operating_hours(client, "cancel_transaction")
        transaction = self.queue.get(transaction_id)
        self.bank._owned_account(client, transaction.sender)
        return self.queue.cancel(transaction_id, self.bank._clock())

    def process_ready(self, limit: int | None = None) -> list[Transaction]:
        """Выполнить готовые заявки; ночью оставить их в очереди до утра."""
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise InvalidOperationError("Лимит должен быть положительным целым")
        now = self.bank._clock()
        if 0 <= now.hour < 5:
            return []
        processed: list[Transaction] = []
        while limit is None or len(processed) < limit:
            transaction = self.queue.pop_ready(now)
            if transaction is None:
                break
            self._process_one(transaction, now)
            processed.append(transaction)
        return processed

    def _process_one(self, transaction: Transaction, now: datetime) -> None:
        transaction.attempts += 1
        try:
            sender = self.bank._accounts[transaction.sender]
            recipient = self.bank._accounts[transaction.recipient]
            if sender.currency is not transaction.currency:
                raise InvalidOperationError("Валюта отправителя изменилась")
            fee = (
                self.external_fee
                if transaction.kind is TransactionType.EXTERNAL
                else Decimal(0)
            )
            if sender.currency is recipient.currency:
                received = transaction.amount
            else:
                pair = (sender.currency, recipient.currency)
                rate = self.exchange_rates.get(pair)
                if rate is None:
                    raise InvalidOperationError("Нет курса для пары валют")
                received = transaction.amount * rate
            transaction.fee = fee
            if self.before_transfer is not None:
                self.before_transfer(transaction)
            self.bank._transfer(
                transaction.sender, transaction.recipient,
                transaction.amount, received, fee,
            )
            transaction.converted_amount = received
            transaction.status = TransactionStatus.COMPLETED
            transaction.updated_at = now
            transaction.finished_at = now
        except RetryableTransactionError as exc:
            self._record_error(transaction, now, exc)
            if transaction.attempts < self.max_attempts:
                self.queue.retry(transaction, now, now + self.retry_delay)
            else:
                self._fail(transaction, now, str(exc))
        except BankAccountError as exc:
            self._record_error(transaction, now, exc)
            self._fail(transaction, now, str(exc))
        except Exception as exc:
            self._record_error(transaction, now, exc)
            self._fail(transaction, now, str(exc))
            raise

    @staticmethod
    def _record_error(
        transaction: Transaction, now: datetime, error: Exception
    ) -> None:
        transaction.errors.append(
            ProcessingError(now, transaction.attempts, str(error))
        )

    @staticmethod
    def _fail(transaction: Transaction, now: datetime, reason: str) -> None:
        transaction.status = TransactionStatus.FAILED
        transaction.rejection_reason = reason
        transaction.updated_at = now
        transaction.finished_at = now
