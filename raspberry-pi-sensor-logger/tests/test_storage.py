import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

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
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_writes_one_wide_row_for_four_gases(tmp_path) -> None:
    path = GasCsvWriter(tmp_path).append(NOW, 1, four_readings())

    assert path == tmp_path / "gas" / "gas_2026-08-03.csv"
    rows = read_rows(path)
    assert len(rows) == 1
    assert list(rows[0]) == FIELDS
    assert rows[0]["时间"] == "2026-08-03T15:30:05+08:00"
    assert rows[0]["编号"] == "000001"
    assert rows[0]["CH4(%LEL)"] == "12.3"
    assert rows[0]["O2(%VOL)"] == "20.9"
    assert rows[0]["CO(ppm)"] == "5"
    assert rows[0]["H2S(ppm)"] == "0"
    assert rows[0]["状态"] == "正常"
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_appends_without_repeating_header(tmp_path) -> None:
    writer = GasCsvWriter(tmp_path)

    writer.append(NOW, 1, four_readings())
    writer.append(NOW + timedelta(seconds=5), 2, four_readings())

    path = tmp_path / "gas" / "gas_2026-08-03.csv"
    assert len(read_rows(path)) == 2
    assert path.read_text(encoding="utf-8-sig").count("时间,") == 1


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
    assert row["O2(%VOL)"] == ""
    assert row["状态"] == "O2：o2 读取失败：串口响应超时；热像读取失败"


def test_missing_channel_still_has_fixed_columns(tmp_path) -> None:
    path = GasCsvWriter(tmp_path).append(NOW, 1, four_readings()[:3])

    row = read_rows(path)[0]
    assert row["H2S(ppm)"] == ""
    assert row["状态"] == "H2S缺少读数"


def test_rejects_appending_to_legacy_header(tmp_path) -> None:
    directory = tmp_path / "gas"
    directory.mkdir()
    path = directory / "gas_2026-08-03.csv"
    path.write_text("timestamp,sample_id,error\n", encoding="utf-8")

    with pytest.raises(ValueError, match="旧表格格式"):
        GasCsvWriter(tmp_path).append(NOW, 1, four_readings())
