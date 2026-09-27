"""Десять учебных переводов: очередь, комиссии, отказы и повтор."""

from datetime import datetime, timedelta

from bank_system import (
    Bank,
    Currency,
    PremiumAccount,
    RetryableTransactionError,
    RiskAnalyzer,
    SavingsAccount,
    TransactionProcessor,
    TransactionQueue,
    TransactionType,
)


def main() -> None:
    current_time = [datetime(2026, 1, 1, 12, 0)]
    # В демонстрации очереди пороги риска выше сумм этого сценария.
    bank = Bank(
        clock=lambda: current_time[0],
        risk_analyzer=RiskAnalyzer(
            large_amounts={currency: "100000" for currency in Currency},
            frequent_count=100,
        ),
    )
    anna = bank.add_client("Анна Иванова", 30, {"email": "a@example.test"}, "a")
    boris = bank.add_client("Борис Петров", 30, {"email": "b@example.test"}, "b")

    source = bank.open_account(
        anna.client_id, "a", initial_balance="1000", account_number="10001"
    )
    premium = bank.open_account(
        anna.client_id, "a", PremiumAccount,
        initial_balance="50", account_number="10002",
    )
    savings = bank.open_account(
        anna.client_id, "a", SavingsAccount,
        initial_balance="100", min_balance="80", account_number="10003",
    )
    frozen_source = bank.open_account(
        anna.client_id, "a", initial_balance="100", account_number="10004"
    )
    target = bank.open_account(
        boris.client_id, "b", initial_balance="10", account_number="20001"
    )
    target_usd = bank.open_account(
        boris.client_id, "b", currency="USD", account_number="20002"
    )
    frozen_target = bank.open_account(
        boris.client_id, "b", account_number="20003"
    )
    bank.withdraw(anna.client_id, "a", premium, "70")  # Баланс -30.
    bank.freeze_account(anna.client_id, "a", frozen_source)
    bank.freeze_account(boris.client_id, "b", frozen_target)

    queue = TransactionQueue()
    retry_id = [None]

    def temporary_failure(transaction) -> None:
        if transaction.transaction_id == retry_id[0] and transaction.attempts == 1:
            raise RetryableTransactionError("Учебный временный сбой")

    processor = TransactionProcessor(
        bank, queue,
        exchange_rates={(Currency.RUB, Currency.USD): "0.02"},
        before_transfer=temporary_failure,
    )

    def put(sender: str, recipient: str, amount: str, **options: object):
        return processor.submit(
            anna.client_id, "a", sender, recipient, amount, **options
        )

    transactions = [
        put(source, target, "20", priority=1),
        put(source, target, "30", kind=TransactionType.EXTERNAL, priority=5),
        put(source, target_usd, "40", priority=10),
        put(premium, target, "60"),
        put(savings, target, "30"),
        put(frozen_source, target, "10"),
        put(source, frozen_target, "10"),
        put(source, target, "10"),
        put(
            source, target, "15",
            scheduled_at=current_time[0] + timedelta(minutes=30),
        ),
        put(source, target, "25"),
    ]
    retry_id[0] = transactions[9].transaction_id
    processor.cancel(anna.client_id, "a", transactions[7].transaction_id)

    first_batch = processor.process_ready()
    current_time[0] += timedelta(minutes=1)
    retry_batch = processor.process_ready()
    current_time[0] += timedelta(minutes=29)
    delayed_batch = processor.process_ready()

    print(f"В очереди создано заявок: {len(transactions)}")
    print(
        "Попыток обработки по этапам: "
        f"{len(first_batch)}, {len(retry_batch)}, {len(delayed_batch)}"
    )
    for index, transaction in enumerate(transactions, start=1):
        print(
            f"{index:2}. {transaction.kind.value:8} "
            f"{transaction.amount} {transaction.currency.value:3} "
            f"→ {transaction.status.value:9} "
            f"комиссия={transaction.fee} "
            f"попыток={transaction.attempts} "
            f"причина={transaction.rejection_reason or '-'}"
        )
    print("Итоговые остатки по валютам:", bank.get_total_balance())


if __name__ == "__main__":
    main()
