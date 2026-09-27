"""Показать клиентов, управление счетами и правила безопасности банка."""

from datetime import datetime

from bank_system import (
    AccountFrozenError,
    Bank,
    ClientStatus,
    InvestmentAccount,
    OperatingHoursError,
    PremiumAccount,
    SavingsAccount,
)


def main() -> None:
    current_time = [datetime(2026, 1, 1, 12, 0)]
    bank = Bank(clock=lambda: current_time[0])
    anna = bank.add_client("Анна Иванова", 30, {"email": "anna@example.test"}, "demo-a")
    boris = bank.add_client(
        "Борис Петров", 27, {"phone": "+70000000000"}, "demo-b"
    )
    vera = bank.add_client(
        "Вера Смирнова", 35, {"email": "vera@example.test"}, "demo-v"
    )

    savings = bank.open_account(
        anna.client_id, "demo-a", SavingsAccount,
        initial_balance="1000", min_balance="200",
    )
    investment = bank.open_account(
        anna.client_id, "demo-a", InvestmentAccount,
        initial_balance="500", currency="USD",
    )
    premium = bank.open_account(
        boris.client_id, "demo-b", PremiumAccount,
        initial_balance="100", overdraft_limit="500",
    )
    bank.apply_monthly_interest(anna.client_id, "demo-a", savings)
    bank.allocate_to_asset(anna.client_id, "demo-a", investment, "etf", "200")
    bank.withdraw(boris.client_id, "demo-b", premium, "150")

    bank.freeze_account(anna.client_id, "demo-a", savings)
    try:
        bank.withdraw(anna.client_id, "demo-a", savings, "10")
    except AccountFrozenError as exc:
        print("Ожидаемый запрет замороженного счёта:", exc)
    bank.unfreeze_account(anna.client_id, "demo-a", savings)

    for _ in range(3):
        bank.authenticate_client(vera.client_id, "неверный пароль")
    print("Статус Веры после трёх ошибок:", vera.status.value)
    assert vera.status is ClientStatus.BLOCKED

    current_time[0] = datetime(2026, 1, 2, 2, 30)
    try:
        bank.deposit(boris.client_id, "demo-b", premium, "20")
    except OperatingHoursError as exc:
        print("Ожидаемый ночной запрет:", exc)

    print("Счета Анны:", bank.search_accounts(anna.client_id, "demo-a"))
    print("Итог по валютам:", bank.get_total_balance())
    print("Рейтинг по валютам:", bank.get_clients_ranking())
    print("Подозрительных событий:", len(bank.security_events))


if __name__ == "__main__":
    main()
