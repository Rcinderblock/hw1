"""Очередь и обработка учебных переводов между счетами банка."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from heapq import heappop, heappush
from uuid import uuid4

from .accounts import Amount, Currency, _valid_amount
from .audit import (
    AuditEntry,
    AuditEventType,
    AuditLog,
    AuditReporter,
    AuditSeverity,
    RiskAnalyzer,
    RiskAssessment,
    RiskLevel,
)
from .bank import Bank, ClientStatus
from .exceptions import (
    AuthenticationError,
    BankAccountError,
    InvalidOperationError,
    OperatingHoursError,
    RetryableTransactionError,
    RiskBlockedError,
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


class Transaction:
    """Заявка с доступными только для чтения данными и результатом обработки."""

    def __init__(
        self,
        kind: TransactionType,
        amount: Amount,
        currency: Currency,
        sender: str,
        recipient: str,
        created_at: datetime,
        scheduled_at: datetime,
        priority: int = 0,
        client_id: str = "",
        transaction_id: str | None = None,
    ) -> None:
        if not isinstance(kind, TransactionType):
            raise InvalidOperationError("Неизвестный тип транзакции")
        if not isinstance(currency, Currency):
            raise InvalidOperationError("Неизвестная валюта транзакции")
        if (
            not isinstance(sender, str) or not sender
            or not isinstance(recipient, str) or not recipient
            or sender == recipient
        ):
            raise InvalidOperationError("Нужны разные счета отправителя и получателя")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise InvalidOperationError("Приоритет должен быть целым числом")
        if (
            not isinstance(created_at, datetime)
            or not isinstance(scheduled_at, datetime)
            or created_at.tzinfo is not None
            or scheduled_at.tzinfo is not None
        ):
            raise InvalidOperationError(
                "Время транзакции должно быть без часового пояса"
            )
        if not isinstance(client_id, str):
            raise InvalidOperationError("ID клиента должен быть строкой")
        if transaction_id is not None and (
            not isinstance(transaction_id, str) or not transaction_id
        ):
            raise InvalidOperationError("ID транзакции должен быть непустой строкой")
        self._kind = kind
        self._amount = _valid_amount(amount)
        self._currency = currency
        self._sender = sender
        self._recipient = recipient
        self._created_at = created_at
        self._scheduled_at = scheduled_at
        self._priority = priority
        self._client_id = client_id
        self._transaction_id = transaction_id or str(uuid4())
        self._fee = Decimal(0)
        self._converted_amount: Decimal | None = None
        self._status = TransactionStatus.PENDING
        self._rejection_reason: str | None = None
        self._updated_at = created_at
        self._finished_at: datetime | None = None
        self._attempts = 0
        self._errors: list[ProcessingError] = []

    @property
    def kind(self) -> TransactionType:
        return self._kind

    @property
    def amount(self) -> Decimal:
        return self._amount

    @property
    def currency(self) -> Currency:
        return self._currency

    @property
    def sender(self) -> str:
        return self._sender

    @property
    def recipient(self) -> str:
        return self._recipient

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def scheduled_at(self) -> datetime:
        return self._scheduled_at

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def transaction_id(self) -> str:
        return self._transaction_id

    @property
    def fee(self) -> Decimal:
        return self._fee

    @property
    def converted_amount(self) -> Decimal | None:
        return self._converted_amount

    @property
    def status(self) -> TransactionStatus:
        return self._status

    @property
    def rejection_reason(self) -> str | None:
        return self._rejection_reason

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def finished_at(self) -> datetime | None:
        return self._finished_at

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def errors(self) -> tuple[ProcessingError, ...]:
        return tuple(self._errors)

    def _start(self, at: datetime) -> None:
        self._status = TransactionStatus.PROCESSING
        self._updated_at = at
        self._attempts += 1

    def _cancel(self, at: datetime) -> None:
        self._status = TransactionStatus.CANCELLED
        self._updated_at = at
        self._finished_at = at

    def _retry(self, at: datetime, scheduled_at: datetime) -> None:
        self._status = TransactionStatus.RETRYING
        self._scheduled_at = scheduled_at
        self._updated_at = at

    def _complete(self, at: datetime, received: Decimal) -> None:
        self._converted_amount = received
        self._status = TransactionStatus.COMPLETED
        self._updated_at = at
        self._finished_at = at

    def _record_error(self, at: datetime, error: Exception) -> None:
        self._errors.append(ProcessingError(at, self.attempts, str(error)))

    def _fail(self, at: datetime, reason: str) -> None:
        self._status = TransactionStatus.FAILED
        self._rejection_reason = reason
        self._updated_at = at
        self._finished_at = at


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

    def for_client(self, client_id: str) -> tuple[Transaction, ...]:
        """Вернуть принятые заявки клиента в порядке постановки в очередь."""
        return tuple(
            transaction
            for transaction in self._transactions.values()
            if transaction.client_id == client_id
        )

    def cancel(self, transaction_id: str, at: datetime) -> Transaction:
        transaction = self.get(transaction_id)
        if transaction.status not in (
            TransactionStatus.PENDING, TransactionStatus.RETRYING
        ):
            raise InvalidOperationError("Отменить можно только ожидающую транзакцию")
        transaction._cancel(at)
        return transaction

    def retry(
        self, transaction: Transaction, at: datetime, scheduled_at: datetime
    ) -> None:
        if self._transactions.get(transaction.transaction_id) is not transaction:
            raise InvalidOperationError("Заявка не принадлежит этой очереди")
        if transaction.status is not TransactionStatus.PROCESSING:
            raise InvalidOperationError("Повторить можно только обрабатываемую заявку")
        transaction._retry(at, scheduled_at)
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
                transaction._start(now)
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
        audit_log: AuditLog | None = None,
        risk_analyzer: RiskAnalyzer | None = None,
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
        if audit_log is not None and not isinstance(audit_log, AuditLog):
            raise InvalidOperationError("Нужен журнал аудита AuditLog")
        if risk_analyzer is not None and not isinstance(
            risk_analyzer, RiskAnalyzer
        ):
            raise InvalidOperationError("Нужен анализатор риска RiskAnalyzer")
        self.bank = bank
        self.queue = queue
        self._approved_transactions: dict[str, Transaction] = {}
        self.external_fee = _valid_amount(external_fee, allow_zero=True)
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self.before_transfer = before_transfer
        selected_log = audit_log if audit_log is not None else bank.audit_log
        selected_risk = (
            risk_analyzer if risk_analyzer is not None else bank.risk_analyzer
        )
        self.exchange_rates: dict[tuple[Currency, Currency], Decimal] = {}
        for pair, value in (exchange_rates or {}).items():
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or not all(isinstance(currency, Currency) for currency in pair)
            ):
                raise InvalidOperationError("Ключ курса: пара валют Currency")
            self.exchange_rates[pair] = _valid_amount(value)
        bank._bind_risk_context(selected_log, selected_risk)

    @property
    def audit_log(self) -> AuditLog:
        return self.bank.audit_log

    @property
    def risk_analyzer(self) -> RiskAnalyzer:
        return self.bank.risk_analyzer

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
            client_id=client_id,
        )
        assessment = self.risk_analyzer.assess(
            client_id, transaction.transaction_id, transaction.amount,
            transaction.currency, recipient, now,
        )
        self.risk_analyzer.record_attempt(
            client_id, transaction.transaction_id, now
        )
        self._record_assessment(transaction, assessment, now, "submission")
        try:
            self.bank._ensure_operating_hours(client, "submit_transaction")
        except OperatingHoursError as exc:
            self._record_block(transaction, now, exc)
            raise
        if assessment.level is RiskLevel.HIGH:
            error = RiskBlockedError("Перевод заблокирован: высокий риск")
            self._record_block(transaction, now, error)
            raise error
        self.queue.add(transaction)
        self._approved_transactions[transaction.transaction_id] = transaction
        self._audit(
            AuditEventType.TRANSACTION_QUEUED, AuditSeverity.INFO,
            transaction, now,
        )
        return transaction

    def cancel(
        self, client_id: str, password: str, transaction_id: str
    ) -> Transaction:
        """Отменить собственную ожидающую заявку после проверки клиента."""
        client = self.bank._authorized_client(client_id, password)
        self.bank._ensure_operating_hours(client, "cancel_transaction")
        transaction = self.queue.get(transaction_id)
        self.bank._owned_account(client, transaction.sender)
        now = self.bank._clock()
        cancelled = self.queue.cancel(transaction_id, now)
        self._audit(
            AuditEventType.TRANSACTION_CANCELLED, AuditSeverity.INFO,
            cancelled, now,
        )
        return cancelled

    def get_history(
        self, client_id: str, password: str
    ) -> tuple[Transaction, ...]:
        """Показать клиенту его заявки после проверки пароля."""
        self.bank._authorized_client(client_id, password)
        return self.queue.for_client(client_id)

    def get_suspicious_operations(
        self, client_id: str, password: str
    ) -> tuple[AuditEntry, ...]:
        """Показать клиенту его подозрительные попытки после проверки пароля."""
        self.bank._authorized_client(client_id, password)
        return AuditReporter(self.audit_log).suspicious_operations(client_id)

    def process_ready(self, limit: int | None = None) -> list[Transaction]:
        """Выполнить готовые заявки; ночью оставить их в очереди до утра."""
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise InvalidOperationError("Лимит должен быть положительным целым")
        processed: list[Transaction] = []
        while limit is None or len(processed) < limit:
            now = self.bank.current_time()
            if 0 <= now.hour < 5:
                break
            transaction = self.queue.pop_ready(now)
            if transaction is None:
                break
            self._process_one(transaction, now)
            processed.append(transaction)
        return processed

    def _process_one(self, transaction: Transaction, now: datetime) -> None:
        try:
            if self._approved_transactions.get(
                transaction.transaction_id
            ) is not transaction:
                raise AuthenticationError("Заявка не прошла проверку отправителя")
            client = self.bank._clients.get(transaction.client_id)
            if client is None or client.status is not ClientStatus.ACTIVE:
                raise AuthenticationError("Отправитель не найден или заблокирован")
            sender = self.bank._owned_account(client, transaction.sender)
            assessment = self.risk_analyzer.assess(
                transaction.client_id, transaction.transaction_id,
                transaction.amount, transaction.currency,
                transaction.recipient, now,
            )
            if assessment.level is RiskLevel.HIGH:
                self._record_assessment(
                    transaction, assessment, now, "execution"
                )
                error = RiskBlockedError(
                    "Перевод заблокирован перед исполнением: высокий риск"
                )
                self._record_block(transaction, now, error)
                raise error
            recipient = self.bank._accounts[transaction.recipient]
            if sender.currency is not transaction.currency:
                raise InvalidOperationError("Валюта отправителя изменилась")
            fee = (
                _valid_amount(self.external_fee, allow_zero=True)
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
                received = transaction.amount * _valid_amount(rate)
            transaction._fee = fee
            if self.before_transfer is not None:
                self.before_transfer(transaction)
            self.bank._transfer(
                transaction.sender, transaction.recipient,
                transaction.amount, received, fee,
            )
            transaction._complete(now, received)
            self.risk_analyzer.record_success(
                transaction.client_id, transaction.recipient
            )
            self._audit(
                AuditEventType.TRANSACTION_COMPLETED, AuditSeverity.INFO,
                transaction, now,
                {"amount": str(transaction.amount),
                 "currency": transaction.currency.value},
            )
        except RetryableTransactionError as exc:
            transaction._record_error(now, exc)
            if transaction.attempts < self.max_attempts:
                self.queue.retry(transaction, now, now + self.retry_delay)
                self._audit_error(
                    AuditEventType.RETRY_SCHEDULED, transaction, now, exc
                )
            else:
                transaction._fail(now, str(exc))
                self._audit_error(
                    AuditEventType.TRANSACTION_FAILED, transaction, now, exc
                )
        except BankAccountError as exc:
            transaction._record_error(now, exc)
            transaction._fail(now, str(exc))
            self._audit_error(
                AuditEventType.TRANSACTION_FAILED, transaction, now, exc
            )
        except Exception as exc:
            transaction._record_error(now, exc)
            transaction._fail(now, str(exc))
            self._audit_error(
                AuditEventType.TRANSACTION_FAILED, transaction, now, exc
            )
            raise

    def _audit(
        self,
        event_type: AuditEventType,
        severity: AuditSeverity,
        transaction: Transaction,
        now: datetime,
        details: Mapping[str, str] | None = None,
    ) -> None:
        self.audit_log.record(
            event_type, severity, transaction.client_id,
            transaction.transaction_id, now, details,
        )

    def _record_assessment(
        self,
        transaction: Transaction,
        assessment: RiskAssessment,
        now: datetime,
        phase: str,
    ) -> None:
        self._audit(
            AuditEventType.RISK_ASSESSED, assessment.severity,
            transaction, now,
            {
                "risk_level": assessment.level.value,
                "reasons": ",".join(reason.value for reason in assessment.reasons),
                "amount": str(transaction.amount),
                "currency": transaction.currency.value,
                "phase": phase,
            },
        )

    def _record_block(
        self, transaction: Transaction, now: datetime, error: Exception
    ) -> None:
        self._audit(
            AuditEventType.TRANSACTION_BLOCKED, AuditSeverity.CRITICAL,
            transaction, now,
            {"error_type": type(error).__name__, "reason": str(error)},
        )

    def _audit_error(
        self,
        event_type: AuditEventType,
        transaction: Transaction,
        now: datetime,
        error: Exception,
    ) -> None:
        self._audit(
            event_type, AuditSeverity.WARNING, transaction, now,
            {"error_type": type(error).__name__,
             "reason": str(error),
             "attempt": str(transaction.attempts)},
        )
