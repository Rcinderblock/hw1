"""Показать разные типы счетов и общий способ работы с ними."""

from bank_system import (
    AbstractAccount,
    InvestmentAccount,
    PremiumAccount,
    SavingsAccount,
)


def show_accounts(accounts: list[AbstractAccount]) -> None:
    """Одинаково вывести счета разных конкретных классов."""
    for account in accounts:
        print(account)


def main() -> None:
    savings_a = SavingsAccount(
        "Анна", initial_balance="1000", min_balance="200",
        monthly_interest_rate="0.01",
    )
    savings_b = SavingsAccount(
        "Борис", initial_balance="700", min_balance="100",
        monthly_interest_rate="0.02",
    )
    savings_a.apply_monthly_interest()
    savings_b.withdraw("50")

    premium_a = PremiumAccount(
        "Вера", initial_balance="100", overdraft_limit="500",
        withdrawal_fee="10",
    )
    premium_b = PremiumAccount(
        "Глеб", initial_balance="30000", withdrawal_fee="5"
    )
    premium_a.withdraw("300")
    premium_b.deposit("20000")
    premium_b.withdraw("6000")

    investment_a = InvestmentAccount("Дина", initial_balance="1000")
    investment_b = InvestmentAccount("Егор", initial_balance="500")
    investment_a.allocate_to_asset("stocks", "400")
    investment_a.allocate_to_asset("bonds", "300")
    investment_a.allocate_to_asset("etf", "200")
    investment_b.allocate_to_asset("etf", "250")
    investment_b.withdraw("100")

    rates = {"stocks": "0.10", "bonds": "0.04", "etf": "0.07"}
    print("Счета после операций:")
    show_accounts([
        savings_a, savings_b, premium_a, premium_b,
        investment_a, investment_b,
    ])
    print(
        "Ожидаемый годовой прирост портфеля Дины:",
        investment_a.project_yearly_growth(rates),
        investment_a.currency.value,
    )


if __name__ == "__main__":
    main()
