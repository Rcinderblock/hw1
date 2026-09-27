"""Воспроизводимая демонстрация всех этапов банковской модели."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from bank_system import (
    AuditEventType,
    AuditLog,
    AuditReporter,
    Bank,
    BankAccount,
    Client,
    Currency,
    InvestmentAccount,
    OperatingHoursError,
    PremiumAccount,
    RetryableTransactionError,
    RiskAnalyzer,
    RiskBlockedError,
    SavingsAccount,
    Transaction,
    TransactionProcessor,
    TransactionQueue,
    TransactionType,
)


@dataclass
class SimulationResult:
    bank: Bank
    clients: tuple[Client, ...]
    account_numbers: tuple[tuple[str, str], ...]
    processor: TransactionProcessor
    transactions: tuple[Transaction, ...]
    audit_log: AuditLog
    attempted: int
    blocked_before_queue: int


def run_simulation(audit_path: str | Path | None = None) -> SimulationResult:
    """Создать 6 клиентов, 12 счетов и 42 попытки перевода."""
    now = [datetime(2026, 1, 1, 9)]
    bank = Bank(clock=lambda: now[0])
    names = (
        "Анна Иванова", "Борис Петров", "Вера Смирнова",
        "Глеб Кузнецов", "Дарья Орлова", "Егор Соколов",
    )
    clients: list[Client] = []
    account_numbers: list[tuple[str, str]] = []
    secondary_types: tuple[type[BankAccount], ...] = (
        SavingsAccount, PremiumAccount, InvestmentAccount,
        BankAccount, SavingsAccount, PremiumAccount,
    )
    secondary_options: tuple[dict[str, str], ...] = (
        {"initial_balance": "300", "min_balance": "200"},
        {"initial_balance": "100", "overdraft_limit": "500"},
        {"initial_balance": "500"},
        {"initial_balance": "100", "currency": "USD"},
        {"initial_balance": "200", "min_balance": "100"},
        {"initial_balance": "100", "overdraft_limit": "500"},
    )
    for index, name in enumerate(names):
        password = f"demo-{index}"
        client = bank.add_client(
            name, 25 + index,
            {"email": f"client{index + 1}@example.test"}, password,
        )
        clients.append(client)
        main_number = bank.open_account(
            client.client_id, password, BankAccount,
            initial_balance=str(10_000 + 1_000 * index),
            account_number=f"{index + 1}0001",
        )
        extra_number = bank.open_account(
            client.client_id, password, secondary_types[index],
            account_number=f"{index + 1}0002",
            **secondary_options[index],
        )
        account_numbers.append((main_number, extra_number))

    bank.apply_monthly_interest(
        clients[0].client_id, "demo-0", account_numbers[0][1]
    )
    bank.allocate_to_asset(
        clients[2].client_id, "demo-2", account_numbers[2][1],
        "stocks", "100",
    )

    retry_id: str | None = None

    def temporary_failure(transaction: Transaction) -> None:
        if transaction.transaction_id == retry_id and transaction.attempts == 1:
            raise RetryableTransactionError("Учебный временный сбой")

    log = AuditLog(audit_path)
    processor = TransactionProcessor(
        bank, TransactionQueue(),
        exchange_rates={(Currency.RUB, Currency.USD): "0.02"},
        audit_log=log, risk_analyzer=RiskAnalyzer(),
        before_transfer=temporary_failure,
    )
    transactions: list[Transaction] = []
    attempted = 0
    blocked = 0

    def submit(
        client_index: int,
        sender: str,
        recipient: str,
        amount: str,
        **options: object,
    ) -> Transaction:
        nonlocal attempted
        attempted += 1
        transaction = processor.submit(
            clients[client_index].client_id, f"demo-{client_index}",
            sender, recipient, amount, **options,
        )
        transactions.append(transaction)
        return transaction

    # Спокойный поток: клиент совершает очередной перевод спустя 66 минут.
    # Одна заявка отложена, остальные обрабатываются после постановки в очередь.
    for index in range(32):
        now[0] = datetime(2026, 1, 1, 9) + timedelta(minutes=11 * index)
        sender_index = index % len(clients)
        recipient_index = (sender_index + 1) % len(clients)
        scheduled_at = (
            now[0] + timedelta(minutes=22) if index == 3 else None
        )
        submit(
            sender_index,
            account_numbers[sender_index][0],
            account_numbers[recipient_index][0],
            str(20 + index % 20),
            priority=index % 3,
            scheduled_at=scheduled_at,
        )
        processor.process_ready()

    def advance() -> None:
        now[0] += timedelta(minutes=11)

    advance()
    submit(0, account_numbers[0][1], account_numbers[3][0], "150")
    processor.process_ready()  # Нарушен минимальный остаток.

    advance()
    submit(2, account_numbers[2][0], account_numbers[4][1], "10")
    bank.freeze_account(clients[4].client_id, "demo-4", account_numbers[4][1])
    processor.process_ready()  # Получатель заморожен после создания заявки.
    bank.unfreeze_account(clients[4].client_id, "demo-4", account_numbers[4][1])

    advance()
    submit(3, account_numbers[3][0], account_numbers[5][1], "10")
    bank.close_account(clients[5].client_id, "demo-5", account_numbers[5][1])
    processor.process_ready()  # Получатель закрыт после создания заявки.

    advance()
    cancelled = submit(
        4, account_numbers[4][0], account_numbers[5][0], "10",
        scheduled_at=now[0] + timedelta(minutes=30),
    )
    processor.cancel(clients[4].client_id, "demo-4", cancelled.transaction_id)

    advance()
    submit(
        3, account_numbers[3][0], account_numbers[3][1], "100",
        kind=TransactionType.EXTERNAL,
    )
    processor.process_ready()  # Конвертация RUB в USD и комиссия.

    advance()
    retrying = submit(5, account_numbers[5][0], account_numbers[0][0], "25")
    retry_id = retrying.transaction_id
    processor.process_ready()
    now[0] += timedelta(minutes=1)
    processor.process_ready()  # Повтор после временного сбоя.

    advance()
    bank.withdraw(clients[1].client_id, "demo-1", account_numbers[1][1], "150")
    submit(1, account_numbers[1][1], account_numbers[2][0], "50")
    processor.process_ready()  # Премиальный счёт переводит из овердрафта.

    advance()
    submit(
        2, account_numbers[2][1], account_numbers[3][0], "50",
        priority=10,
    )
    processor.process_ready()  # Инвестиционный счёт тратит свободные деньги.

    advance()
    try:
        submit(4, account_numbers[4][0], account_numbers[0][1], "1000")
    except RiskBlockedError:
        blocked += 1  # Крупная сумма и новый получатель.

    now[0] = datetime(2026, 1, 2, 1)
    try:
        submit(0, account_numbers[0][0], account_numbers[1][0], "10")
    except OperatingHoursError:
        blocked += 1  # Ночная попытка.

    # Три неверных пароля показывают отдельное правило безопасности Day3.
    for _ in range(3):
        bank.authenticate_client(clients[5].client_id, "wrong")

    return SimulationResult(
        bank=bank,
        clients=tuple(clients),
        account_numbers=tuple(account_numbers),
        processor=processor,
        transactions=tuple(transactions),
        audit_log=log,
        attempted=attempted,
        blocked_before_queue=blocked,
    )


def print_report(result: SimulationResult) -> None:
    log = result.audit_log
    reporter = AuditReporter(log)
    statuses = Counter(item.status.value for item in result.transactions)
    events = Counter(item.event_type.value for item in log.entries)
    print("Клиентов:", len(result.clients))
    print("Счетов:", sum(len(pair) for pair in result.account_numbers))
    print("Попыток перевода:", result.attempted)
    print("Поставлено в очередь:", len(result.transactions))
    print("Заблокировано до очереди:", result.blocked_before_queue)
    print("Статусы заявок:", dict(sorted(statuses.items())))
    print("События аудита:", dict(sorted(events.items())))

    print("\nПримеры событий:")
    for event_type in (
        AuditEventType.TRANSACTION_QUEUED,
        AuditEventType.TRANSACTION_COMPLETED,
        AuditEventType.TRANSACTION_FAILED,
        AuditEventType.TRANSACTION_BLOCKED,
    ):
        matching = log.filter(event_type=event_type)
        if matching:
            entry = matching[0]
            print(
                f"  {entry.occurred_at:%d.%m %H:%M} "
                f"{entry.event_type.value}: "
                f"{entry.transaction_id[:8]} "
                f"{entry.details.get('reason', '')}"
            )

    client = result.clients[0]
    print(f"\nСчета клиента {client.full_name}:")
    for info in result.bank.search_accounts(client.client_id, "demo-0"):
        print(
            f"  {info['account_type']} ****{info['account_number'][-4:]} "
            f"{info['balance']} {info['currency']} ({info['status']})"
        )
    history = result.processor.get_history(client.client_id, "demo-0")
    print("История заявок клиента:")
    for item in history:
        print(
            f"  {item.transaction_id[:8]} {item.amount} "
            f"{item.currency.value} → {item.status.value}"
        )
    suspicious = result.processor.get_suspicious_operations(
        client.client_id, "demo-0"
    )
    print("Подозрительные попытки клиента:")
    for entry in suspicious:
        print(
            f"  {entry.transaction_id[:8]} "
            f"{entry.details['risk_level']}: {entry.details['reasons']}"
        )

    print("\nТоп-3 клиентов по RUB:")
    for place, row in enumerate(
        result.bank.get_clients_ranking()["RUB"][:3], start=1
    ):
        print(f"  {place}. {row['full_name']}: {row['total']} RUB")
    print("Статистика ошибок:", reporter.error_statistics())
    print("Общий баланс по валютам:", result.bank.get_total_balance())
    print("Пометок безопасности:", len(result.bank.security_events))


def main() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "audit.jsonl"
        result = run_simulation(path)
        print_report(result)
        print("Строк аудита в файле:", len(path.read_text(
            encoding="utf-8"
        ).splitlines()))


if __name__ == "__main__":
    main()
