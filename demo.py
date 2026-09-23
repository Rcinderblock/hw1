"""Ручная демонстрация работы счетов."""

from bank_system import AccountFrozenError, AccountStatus, BankAccount


def main() -> None:
    active = BankAccount("Роман", initial_balance="1000.00", currency="RUB")
    frozen = BankAccount("Мария", status=AccountStatus.FROZEN, currency="USD")

    print("Созданы счета:")
    print(active)
    print(frozen)

    active.deposit("250.50")
    active.withdraw("100.25")
    print("\nПосле пополнения и снятия:")
    print(active)

    print("\nПопытка пополнить замороженный счёт:")
    try:
        frozen.deposit("50")
    except AccountFrozenError as exc:
        print(f"Ожидаемая ошибка: {exc}")


if __name__ == "__main__":
    main()
