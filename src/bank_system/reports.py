"""Снимки данных банка, экспорт отчётов и графики учебной модели."""

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from .audit import AuditEventType, AuditLog, AuditReporter
from .bank import Bank
from .exceptions import InvalidOperationError
from .transactions import Transaction, TransactionProcessor


class ReportKind(str, Enum):
    CLIENT = "client"
    BANK = "bank"
    RISK = "risk"


@dataclass(frozen=True)
class Report:
    """Данные отчёта в состоянии на момент его создания."""

    kind: ReportKind
    title: str
    generated_at: str
    data: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "title": self.title,
            "generated_at": self.generated_at,
            "data": self.data,
        }


def _json_value(value: Any) -> Any:
    """Сохранить точные денежные значения строками во всех форматах."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _transaction_data(transaction: Transaction) -> dict[str, Any]:
    return _json_value({
        "transaction_id": transaction.transaction_id,
        "kind": transaction.kind,
        "amount": transaction.amount,
        "currency": transaction.currency,
        "sender": transaction.sender,
        "recipient": transaction.recipient,
        "fee": transaction.fee,
        "converted_amount": transaction.converted_amount,
        "status": transaction.status,
        "rejection_reason": transaction.rejection_reason,
        "created_at": transaction.created_at,
        "scheduled_at": transaction.scheduled_at,
        "finished_at": transaction.finished_at,
        "attempts": transaction.attempts,
        "errors": [
            {"occurred_at": error.occurred_at,
             "attempt": error.attempt, "reason": error.reason}
            for error in transaction.errors
        ],
    })


def _rows(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Развернуть вложенные поля отчёта в пары «путь, значение» для CSV."""
    if isinstance(value, dict):
        if not value:
            return [(path, "{}")]
        return [
            row for key, item in value.items()
            for row in _rows(item, f"{path}.{key}" if path else key)
        ]
    if isinstance(value, list):
        if not value:
            return [(path, "[]")]
        return [
            row for index, item in enumerate(value)
            for row in _rows(item, f"{path}[{index}]")
        ]
    return [(path, "" if value is None else str(value))]


class ReportBuilder:
    """Собрать данные из банка и журнала, затем представить их по-разному."""

    def __init__(
        self, bank: Bank, processor: TransactionProcessor, audit_log: AuditLog
    ) -> None:
        if not isinstance(bank, Bank) or not isinstance(
            processor, TransactionProcessor
        ) or not isinstance(audit_log, AuditLog):
            raise InvalidOperationError("Нужны банк, обработчик и журнал аудита")
        if processor.bank is not bank or processor.audit_log is not audit_log:
            raise InvalidOperationError("Отчёт должен использовать связанные данные")
        self.bank = bank
        self.processor = processor
        self.audit_log = audit_log
        self.audit_reporter = AuditReporter(audit_log)

    def _report(self, kind: ReportKind, title: str, data: dict[str, Any]) -> Report:
        return Report(kind, title, self.bank.current_time().isoformat(), data)

    def client_report(self, client_id: str, password: str) -> Report:
        """Создать отчёт только по счетам и заявкам вошедшего клиента."""
        profile = self.bank.get_client_profile(client_id, password)
        accounts = _json_value(self.bank.search_accounts(client_id, password))
        balance_history = {
            account["account_number"]: [
                _json_value({
                    "occurred_at": point.occurred_at,
                    "balance": point.balance,
                    "total_value": point.total_value,
                    "currency": point.currency,
                    "operation": point.operation,
                })
                for point in self.bank.get_balance_history(
                    client_id, password, account["account_number"]
                )
            ]
            for account in accounts
        }
        transactions = [
            _transaction_data(item)
            for item in self.processor.get_history(client_id, password)
        ]
        suspicious = [
            entry.as_dict() for entry in self.processor.get_suspicious_operations(
                client_id, password
            )
        ]
        data = {
            "client": profile,
            "accounts": accounts,
            "transactions": transactions,
            "balance_history": balance_history,
            "suspicious_operations": suspicious,
            "risk_profile": self.audit_reporter.client_risk_profile(client_id),
        }
        return self._report(
            ReportKind.CLIENT, f"Отчёт по клиенту: {profile['full_name']}", data
        )

    def bank_report(self) -> Report:
        """Собрать общие показатели отдельно по каждой валюте."""
        event_counts = Counter(
            entry.event_type.value for entry in self.audit_log.entries
        )
        data = {
            "accounts": self.bank.get_account_statistics(),
            "total_balance_by_currency": _json_value(self.bank.get_total_balance()),
            "top_clients_by_currency": _json_value({
                currency: rows[:3]
                for currency, rows in self.bank.get_clients_ranking().items()
            }),
            "audit_events": dict(sorted(event_counts.items())),
            "security_events": len(self.bank.security_events),
        }
        return self._report(ReportKind.BANK, "Отчёт по банку", data)

    def risk_report(self) -> Report:
        """Собрать подозрительные операции и статистику ошибок аудита."""
        assessments = self.audit_log.filter(
            event_type=AuditEventType.RISK_ASSESSED
        )
        clients = sorted({entry.client_id for entry in assessments})
        suspicious = self.audit_reporter.suspicious_operations()
        levels = Counter(
            entry.details.get("risk_level", "unknown") for entry in suspicious
        )
        reasons = Counter(
            reason
            for entry in suspicious
            for reason in entry.details.get("reasons", "").split(",")
            if reason
        )
        data = {
            "suspicious_operations": [entry.as_dict() for entry in suspicious],
            "risk_levels": dict(sorted(levels.items())),
            "risk_reasons": dict(sorted(reasons.items())),
            "client_profiles": [
                self.audit_reporter.client_risk_profile(client_id)
                for client_id in clients
            ],
            "error_statistics": self.audit_reporter.error_statistics(),
        }
        return self._report(ReportKind.RISK, "Отчёт по рискам", data)

    @staticmethod
    def to_text(report: Report) -> str:
        """Показать тот же снимок данных читаемыми строками."""
        lines = [report.title, f"Время: {report.generated_at}"]
        lines.extend(f"{path}: {value}" for path, value in _rows(report.data))
        return "\n".join(lines) + "\n"

    @staticmethod
    def export_to_json(report: Report, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return destination

    @staticmethod
    def export_to_csv(report: Report, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("field", "value"))
            writer.writerows(_rows(report.as_dict()))
        return destination

    @staticmethod
    def save_charts(report: Report, directory: str | Path) -> tuple[Path, ...]:
        """Сохранить PNG; Matplotlib загружается только при построении графиков."""
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            from matplotlib.figure import Figure
        except ImportError as exc:
            raise RuntimeError(
                "Для графиков установите зависимости из requirements.txt"
            ) from exc

        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []

        def save(figure: Figure, filename: str) -> None:
            FigureCanvasAgg(figure)
            figure.tight_layout()
            path = destination / filename
            figure.savefig(path, dpi=130)
            figure.clear()
            saved.append(path)

        if report.kind is ReportKind.CLIENT:
            accounts = report.data["accounts"]
            for index, account in enumerate(accounts, start=1):
                points = report.data["balance_history"][account["account_number"]]
                if not points:
                    continue
                figure = Figure(figsize=(8, 4))
                axis = figure.subplots()
                positions = list(range(len(points)))
                axis.plot(positions, [float(point["balance"]) for point in points],
                          marker="o", label="Available balance")
                if account["account_type"] == "InvestmentAccount":
                    axis.plot(
                        positions,
                        [float(point["total_value"]) for point in points],
                        marker="s", label="Total with portfolio",
                    )
                axis.set_title(f"Account {index} ({account['currency']})")
                tick_step = max(1, (len(points) + 7) // 8)
                ticks = list(range(0, len(points), tick_step))
                if ticks[-1] != len(points) - 1:
                    ticks.append(len(points) - 1)
                axis.set_xticks(ticks, [
                    datetime.fromisoformat(points[position]["occurred_at"])
                    .strftime("%d.%m %H:%M")
                    for position in ticks
                ], rotation=35, ha="right")
                axis.set_xlabel("Operation order")
                axis.set_ylabel(account["currency"])
                axis.grid(alpha=0.25)
                axis.legend()
                save(figure, f"client_balance_{index}.png")
        elif report.kind is ReportKind.BANK:
            counts = report.data["accounts"]["by_type"]
            figure = Figure(figsize=(6, 5))
            axis = figure.subplots()
            if counts:
                axis.pie(list(counts.values()), labels=list(counts),
                         autopct="%1.0f%%")
            else:
                axis.text(0.5, 0.5, "No accounts", ha="center", va="center")
            axis.set_title("Accounts by type")
            save(figure, "bank_account_types.png")

            for currency, rows in report.data["top_clients_by_currency"].items():
                figure = Figure(figsize=(8, 4))
                axis = figure.subplots()
                axis.bar([row["full_name"] for row in rows],
                         [float(row["total"]) for row in rows])
                axis.set_title(f"Top clients ({currency})")
                axis.set_ylabel(currency)
                axis.tick_params(axis="x", labelrotation=15)
                save(figure, f"bank_top_clients_{currency}.png")
        elif report.kind is ReportKind.RISK:
            counts = report.data["risk_levels"]
            figure = Figure(figsize=(6, 4))
            axis = figure.subplots()
            if counts:
                axis.bar(list(counts), list(counts.values()))
            else:
                axis.text(0.5, 0.5, "No suspicious operations",
                          ha="center", va="center")
            axis.set_title("Suspicious operations by risk level")
            axis.set_ylabel("Operations")
            save(figure, "risk_levels.png")
        else:
            raise InvalidOperationError("Неизвестный тип отчёта")
        return tuple(saved)
