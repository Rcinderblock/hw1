"""Журнал событий, учебная оценка риска и отчёты по переводам."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from json import dumps
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

from .accounts import Amount, Currency, _valid_amount
from .exceptions import InvalidOperationError


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AuditEventType(str, Enum):
    RISK_ASSESSED = "risk_assessed"
    TRANSACTION_QUEUED = "transaction_queued"
    TRANSACTION_BLOCKED = "transaction_blocked"
    TRANSACTION_COMPLETED = "transaction_completed"
    TRANSACTION_FAILED = "transaction_failed"
    RETRY_SCHEDULED = "retry_scheduled"
    TRANSACTION_CANCELLED = "transaction_cancelled"
    WITHDRAWAL_COMPLETED = "withdrawal_completed"
    WITHDRAWAL_FAILED = "withdrawal_failed"
    WITHDRAWAL_BLOCKED = "withdrawal_blocked"


_SEVERITY_ORDER = {
    AuditSeverity.INFO: 0,
    AuditSeverity.WARNING: 1,
    AuditSeverity.CRITICAL: 2,
}


@dataclass(frozen=True)
class AuditEntry:
    occurred_at: datetime
    severity: AuditSeverity
    event_type: AuditEventType
    client_id: str
    transaction_id: str
    details: Mapping[str, str] = field(default_factory=dict)
    entry_id: str = field(default_factory=lambda: str(uuid4()))

    def as_dict(self) -> dict[str, object]:
        return {
            "entry_id": self.entry_id,
            "occurred_at": self.occurred_at.isoformat(),
            "severity": self.severity.value,
            "event_type": self.event_type.value,
            "client_id": self.client_id,
            "transaction_id": self.transaction_id,
            "details": dict(self.details),
        }


class AuditLog:
    """Сохранять события в памяти и, если указан путь, в JSON Lines файл."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._entries: list[AuditEntry] = []
        self._persistence_errors: list[str] = []

    @property
    def entries(self) -> tuple[AuditEntry, ...]:
        return tuple(self._entries)

    @property
    def persistence_errors(self) -> tuple[str, ...]:
        return tuple(self._persistence_errors)

    def record(
        self,
        event_type: AuditEventType,
        severity: AuditSeverity,
        client_id: str,
        transaction_id: str,
        occurred_at: datetime,
        details: Mapping[str, str] | None = None,
    ) -> AuditEntry:
        if not isinstance(event_type, AuditEventType) or not isinstance(
            severity, AuditSeverity
        ):
            raise InvalidOperationError("Неизвестный тип или важность события")
        if not isinstance(occurred_at, datetime):
            raise InvalidOperationError("Нужно время события")
        if details is not None and (
            not isinstance(details, Mapping)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in details.items()
            )
        ):
            raise InvalidOperationError("Детали аудита должны быть строками")
        entry = AuditEntry(
            occurred_at=occurred_at,
            severity=severity,
            event_type=event_type,
            client_id=client_id,
            transaction_id=transaction_id,
            details=MappingProxyType(dict(details or {})),
        )
        self._entries.append(entry)
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as output:
                    output.write(dumps(entry.as_dict(), ensure_ascii=False) + "\n")
            except OSError as exc:
                # Сбой файла не должен превращать уже проведённый перевод в отказ.
                self._persistence_errors.append(str(exc))
        return entry

    def filter(
        self,
        *,
        event_type: AuditEventType | None = None,
        min_severity: AuditSeverity | None = None,
        client_id: str | None = None,
        transaction_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[AuditEntry, ...]:
        """Вернуть события, удовлетворяющие всем указанным условиям."""
        if min_severity is not None and not isinstance(
            min_severity, AuditSeverity
        ):
            raise InvalidOperationError("Неизвестный уровень важности")
        return tuple(
            entry
            for entry in self._entries
            if (event_type is None or entry.event_type is event_type)
            and (
                min_severity is None
                or _SEVERITY_ORDER[entry.severity]
                >= _SEVERITY_ORDER[min_severity]
            )
            and (client_id is None or entry.client_id == client_id)
            and (
                transaction_id is None
                or entry.transaction_id == transaction_id
            )
            and (since is None or entry.occurred_at >= since)
            and (until is None or entry.occurred_at <= until)
        )


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskReason(str, Enum):
    LARGE_AMOUNT = "large_amount"
    FREQUENT_OPERATIONS = "frequent_operations"
    NEW_RECIPIENT = "new_recipient"
    NIGHT_OPERATION = "night_operation"


@dataclass(frozen=True)
class RiskAssessment:
    level: RiskLevel
    reasons: tuple[RiskReason, ...]

    @property
    def severity(self) -> AuditSeverity:
        return {
            RiskLevel.LOW: AuditSeverity.INFO,
            RiskLevel.MEDIUM: AuditSeverity.WARNING,
            RiskLevel.HIGH: AuditSeverity.CRITICAL,
        }[self.level]


class RiskAnalyzer:
    """Оценивать каждую попытку по четырём учебным признакам риска."""

    def __init__(
        self,
        *,
        large_amounts: Mapping[Currency, Amount] | None = None,
        frequent_count: int = 3,
        window: timedelta = timedelta(minutes=10),
    ) -> None:
        if isinstance(frequent_count, bool) or not isinstance(
            frequent_count, int
        ) or frequent_count < 2:
            raise InvalidOperationError("Порог частоты должен быть не меньше двух")
        if not isinstance(window, timedelta) or window <= timedelta(0):
            raise InvalidOperationError("Окно частоты должно быть положительным")
        if large_amounts is not None and not isinstance(large_amounts, Mapping):
            raise InvalidOperationError("Пороги сумм должны быть словарём валют")
        self.large_amounts = {
            currency: Decimal("1000") for currency in Currency
        }
        for currency, amount in (large_amounts or {}).items():
            if not isinstance(currency, Currency):
                raise InvalidOperationError("Неизвестная валюта порога")
            self.large_amounts[currency] = _valid_amount(amount)
        self.frequent_count = frequent_count
        self.window = window
        self._attempts: dict[str, list[tuple[datetime, str]]] = {}
        self._known_recipients: dict[str, set[str]] = {}

    def assess(
        self,
        client_id: str,
        transaction_id: str,
        amount: Amount,
        currency: Currency,
        recipient: str,
        at: datetime,
    ) -> RiskAssessment:
        value = _valid_amount(amount)
        if not isinstance(currency, Currency) or not isinstance(at, datetime):
            raise InvalidOperationError("Неверная валюта или время оценки")
        reasons: list[RiskReason] = []
        if value >= self.large_amounts[currency]:
            reasons.append(RiskReason.LARGE_AMOUNT)
        cutoff = at - self.window
        recent_others = sum(
            1
            for when, other_id in self._attempts.get(client_id, ())
            if other_id != transaction_id and cutoff <= when <= at
        )
        if recent_others + 1 >= self.frequent_count:
            reasons.append(RiskReason.FREQUENT_OPERATIONS)
        if recipient not in self._known_recipients.get(client_id, set()):
            reasons.append(RiskReason.NEW_RECIPIENT)
        if 0 <= at.hour < 5:
            reasons.append(RiskReason.NIGHT_OPERATION)
        if RiskReason.NIGHT_OPERATION in reasons or len(reasons) >= 2:
            level = RiskLevel.HIGH
        elif reasons:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.LOW
        return RiskAssessment(level, tuple(reasons))

    def assess_withdrawal(
        self,
        client_id: str,
        operation_id: str,
        amount: Amount,
        currency: Currency,
        at: datetime,
    ) -> RiskAssessment:
        """Оценить прямое снятие без вымышленного получателя перевода."""
        value = _valid_amount(amount)
        if not isinstance(currency, Currency) or not isinstance(at, datetime):
            raise InvalidOperationError("Неверная валюта или время оценки")
        reasons: list[RiskReason] = []
        if value >= self.large_amounts[currency]:
            reasons.append(RiskReason.LARGE_AMOUNT)
        cutoff = at - self.window
        recent = sum(
            1 for when, other_id in self._attempts.get(client_id, ())
            if other_id != operation_id and cutoff <= when <= at
        )
        if recent + 1 >= self.frequent_count:
            reasons.append(RiskReason.FREQUENT_OPERATIONS)
        if 0 <= at.hour < 5:
            reasons.append(RiskReason.NIGHT_OPERATION)
        # Выдача крупной суммы наличными получает высокий риск без получателя.
        if RiskReason.LARGE_AMOUNT in reasons or RiskReason.NIGHT_OPERATION in reasons:
            level = RiskLevel.HIGH
        elif reasons:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.LOW
        return RiskAssessment(level, tuple(reasons))

    def record_attempt(
        self, client_id: str, transaction_id: str, at: datetime
    ) -> None:
        cutoff = at - self.window
        recent = [
            item for item in self._attempts.get(client_id, ())
            if item[0] >= cutoff
        ]
        recent.append((at, transaction_id))
        self._attempts[client_id] = recent

    def record_success(self, client_id: str, recipient: str) -> None:
        self._known_recipients.setdefault(client_id, set()).add(recipient)


class AuditReporter:
    """Строить отчёты из уже записанных событий, не меняя их."""

    def __init__(self, log: AuditLog) -> None:
        self.log = log

    def suspicious_operations(
        self, client_id: str | None = None
    ) -> tuple[AuditEntry, ...]:
        assessments = self.log.filter(
            event_type=AuditEventType.RISK_ASSESSED,
            min_severity=AuditSeverity.WARNING,
            client_id=client_id,
        )
        by_transaction: dict[str, AuditEntry] = {}
        for entry in assessments:
            previous = by_transaction.get(entry.transaction_id)
            if (
                previous is None
                or _SEVERITY_ORDER[entry.severity]
                > _SEVERITY_ORDER[previous.severity]
            ):
                by_transaction[entry.transaction_id] = entry
        return tuple(by_transaction.values())

    def client_risk_profile(self, client_id: str) -> dict[str, object]:
        assessments = self.log.filter(
            event_type=AuditEventType.RISK_ASSESSED, client_id=client_id
        )
        counts = Counter(
            entry.details.get("risk_level", "unknown") for entry in assessments
        )
        highest = next(
            (
                level.value for level in (
                    RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW
                ) if counts[level.value]
            ),
            None,
        )
        reasons = sorted({
            reason
            for entry in assessments
            for reason in entry.details.get("reasons", "").split(",")
            if reason
        })
        return {
            "client_id": client_id,
            "assessments": len(assessments),
            "transactions": len({entry.transaction_id for entry in assessments}),
            "highest_risk": highest,
            "levels": {level.value: counts[level.value] for level in RiskLevel},
            "reasons": reasons,
        }

    def error_statistics(self) -> dict[str, object]:
        failures = (
            *self.log.filter(event_type=AuditEventType.TRANSACTION_FAILED),
            *self.log.filter(event_type=AuditEventType.WITHDRAWAL_FAILED),
        )
        retries = self.log.filter(event_type=AuditEventType.RETRY_SCHEDULED)
        blocked = (
            *self.log.filter(event_type=AuditEventType.TRANSACTION_BLOCKED),
            *self.log.filter(event_type=AuditEventType.WITHDRAWAL_BLOCKED),
        )
        by_type = Counter(
            entry.details.get("error_type", "UnknownError")
            for entry in (*failures, *retries, *blocked)
        )
        return {
            "failed": len(failures),
            "retries": len(retries),
            "blocked": len(blocked),
            "by_error_type": dict(sorted(by_type.items())),
        }
