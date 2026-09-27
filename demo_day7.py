"""Сохранить отчёты и графики по результатам комплексной симуляции."""

import argparse
from pathlib import Path

from bank_system import ReportBuilder
from demo_day6 import run_simulation


def generate_reports(directory: str | Path) -> list[Path]:
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    result = run_simulation(destination / "audit.jsonl")
    builder = ReportBuilder(result.bank, result.processor, result.audit_log)
    reports = (
        builder.client_report(result.clients[0].client_id, "demo-0"),
        builder.bank_report(),
        builder.risk_report(),
    )
    saved: list[Path] = []
    for report in reports:
        name = report.kind.value
        saved.append(builder.export_to_json(report, destination / f"{name}.json"))
        saved.append(builder.export_to_csv(report, destination / f"{name}.csv"))
        text_path = destination / f"{name}.txt"
        text_path.write_text(builder.to_text(report), encoding="utf-8")
        saved.append(text_path)
        saved.extend(builder.save_charts(report, destination / "charts"))
        print(f"{report.title}: {len(report.data)} разделов")
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Демонстрация отчётов и графиков")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("output/day7"),
        help="каталог для JSON, CSV, текста и PNG (по умолчанию output/day7)",
    )
    args = parser.parse_args()
    for path in generate_reports(args.output_dir):
        print(path)


if __name__ == "__main__":
    main()
