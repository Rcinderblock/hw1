"""Обычные и подозрительные переводы с журналом аудита."""

from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from bank_system import (
    AuditLog,
    AuditReporter,
    Bank,
    OperatingHoursError,
    RiskAnalyzer,
    RiskBlockedError,
    TransactionProcessor,
    TransactionQueue,
)


def main() -> None:
    now = [datetime(2026, 1, 1, 12)]
    bank = Bank(clock=lambda: now[0])
    anna = bank.add_client("Анна Иванова", 30, {"email": "a@example.test"}, "a")
    boris = bank.add_client("Борис Петров", 30, {"email": "b@example.test"}, "b")
    source = bank.open_account(
        anna.client_id, "a", initial_balance="5000", account_number="10001"
    )
    familiar = bank.open_account(
        boris.client_id, "b", account_number="20001"
    )
    new_target = bank.open_account(
        boris.client_id, "b", account_number="20002"
    )
    frozen_target = bank.open_account(
        boris.client_id, "b", account_number="20003"
    )

    with TemporaryDirectory() as directory:
        audit_file = Path(directory) / "audit.jsonl"
        log = AuditLog(audit_file)
        processor = TransactionProcessor(
            bank, TransactionQueue(), audit_log=log,
            risk_analyzer=RiskAnalyzer(),
        )

        def submit(recipient: str, amount: str):
            return processor.submit(
                anna.client_id, "a", source, recipient, amount
            )

        first = submit(familiar, "20")  # Первый получатель: средний риск.
        processor.process_ready()
        repeat = submit(familiar, "10")  # Знакомый получатель: низкий риск.
        processor.process_ready()

        now[0] += timedelta(minutes=11)
        large = submit(familiar, "1000")  # Только крупная сумма: средний риск.
        processor.process_ready()
        try:
            submit(new_target, "1200")  # Крупная сумма + новый счёт.
        except RiskBlockedError:
            print("Крупный перевод на новый счёт заблокирован")

        frequent = submit(familiar, "10")  # Третья попытка за 10 минут.
        processor.process_ready()
        now[0] += timedelta(minutes=11)
        failed = submit(frozen_target, "10")
        bank.freeze_account(boris.client_id, "b", frozen_target)
        processor.process_ready()

        now[0] = datetime(2026, 1, 2, 1)
        try:
            submit(familiar, "10")
        except OperatingHoursError:
            print("Ночная попытка записана и отклонена")

        reporter = AuditReporter(log)
        print("Статусы заявок:", [
            item.status.value for item in (first, repeat, large, frequent, failed)
        ])
        print("Риск-профиль:", reporter.client_risk_profile(anna.client_id))
        print("Подозрительных заявок:", len(reporter.suspicious_operations()))
        print("Ошибки:", reporter.error_statistics())
        print("Записей в памяти:", len(log.entries))
        print("Строк в файле:", len(audit_file.read_text(
            encoding="utf-8"
        ).splitlines()))


if __name__ == "__main__":
    main()
