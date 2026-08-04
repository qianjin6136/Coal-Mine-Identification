import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sensor_logger.models import GasReading
from sensor_logger.storage import FIELDS, GasCsvWriter

NOW = datetime(2026, 8, 3, 15, 30, 5, tzinfo=timezone(timedelta(hours=8)))


def four_readings() -> tuple[GasReading, ...]:
    return (
        GasReading("ch4", 1, Decimal("12.3"), "%LEL", "normal"),
        GasReading("o2", 2, Decimal("20.9"), "%VOL", "normal"),
        GasReading("co", 3, Decimal("5"), "ppm", "normal"),
        GasReading("h2s", 4, Decimal("0"), "ppm", "normal"),
    )


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_writes_one_wide_row_for_four_gases(tmp_path) -> None:
    path = GasCsvWriter(tmp_path).append(NOW, 1, four_readings())

    assert path == tmp_path / "gas" / "gas_2026-08-03.csv"
    rows = read_rows(path)
    assert len(rows) == 1
    assert list(rows[0]) == FIELDS
    assert rows[0]["timestamp"] == "2026-08-03T15:30:05+08:00"
    assert rows[0]["sample_id"] == "000001"
    assert rows[0]["ch4_value"] == "12.3"
    assert rows[0]["ch4_unit"] == "%LEL"
    assert rows[0]["o2_value"] == "20.9"
    assert rows[0]["co_value"] == "5"
    assert rows[0]["h2s_value"] == "0"


def test_appends_without_repeating_header(tmp_path) -> None:
    writer = GasCsvWriter(tmp_path)

    writer.append(NOW, 1, four_readings())
    writer.append(NOW + timedelta(seconds=5), 2, four_readings())

    path = tmp_path / "gas" / "gas_2026-08-03.csv"
    assert len(read_rows(path)) == 2
    assert path.read_text(encoding="utf-8").count("timestamp,") == 1


def test_creates_a_new_file_on_the_next_day(tmp_path) -> None:
    writer = GasCsvWriter(tmp_path)

    first = writer.append(NOW, 1, four_readings())
    second = writer.append(NOW + timedelta(days=1), 2, four_readings())

    assert first.name == "gas_2026-08-03.csv"
    assert second.name == "gas_2026-08-04.csv"


def test_failed_channel_is_blank_and_error_is_recorded(tmp_path) -> None:
    readings = list(four_readings())
    readings[1] = GasReading(
        "o2", 2, None, status="read_error", error="o2 读取失败：串口响应超时"
    )

    path = GasCsvWriter(tmp_path).append(
        NOW, 1, readings, errors=("热像读取失败",)
    )

    row = read_rows(path)[0]
    assert row["o2_value"] == ""
    assert row["o2_status"] == "read_error"
    assert row["error"] == "o2 读取失败：串口响应超时; 热像读取失败"


def test_missing_channel_still_has_fixed_columns(tmp_path) -> None:
    path = GasCsvWriter(tmp_path).append(NOW, 1, four_readings()[:3])

    row = read_rows(path)[0]
    assert row["h2s_value"] == ""
    assert row["h2s_unit"] == ""
    assert row["h2s_status"] == ""
