from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from sensor_logger.models import GasReading
from sensor_logger.service import SensorLogger, run_periodic

NOW = datetime(2026, 8, 3, 15, 30, 5, tzinfo=timezone(timedelta(hours=8)))


def gas_readings() -> tuple[GasReading, ...]:
    return (
        GasReading("ch4", 1, Decimal("1"), "%LEL", "normal"),
        GasReading("o2", 2, Decimal("20.9"), "%VOL", "normal"),
        GasReading("co", 3, Decimal("2"), "ppm", "normal"),
        GasReading("h2s", 4, Decimal("0"), "ppm", "normal"),
    )


class GoodGasReader:
    def read_all(self):
        return gas_readings()


class BrokenGasReader:
    def read_all(self):
        raise OSError("串口已断开")


class GoodCamera:
    def read_frame(self):
        return [25.0] * 768


class BrokenCamera:
    def read_frame(self):
        raise OSError("I2C 无响应")


class RecordingCsvWriter:
    def __init__(self) -> None:
        self.calls = []

    def append(self, timestamp, sample_id, readings, errors=()):
        self.calls.append((timestamp, sample_id, tuple(readings), tuple(errors)))
        return Path("data/gas/gas_2026-08-03.csv")


class RecordingImageWriter:
    def __init__(self) -> None:
        self.calls = []

    def write(self, temperatures, timestamp, sample_id):
        self.calls.append((list(temperatures), timestamp, sample_id))
        return Path("data/thermal/thermal.png")


def test_successful_capture_uses_shared_timestamp_and_sample_id() -> None:
    csv_writer = RecordingCsvWriter()
    image_writer = RecordingImageWriter()
    logger = SensorLogger(GoodGasReader(), GoodCamera(), csv_writer, image_writer)

    result = logger.capture(7, NOW)

    assert result.sample_id == 7
    assert result.timestamp == NOW
    assert result.csv_path == Path("data/gas/gas_2026-08-03.csv")
    assert result.png_path == Path("data/thermal/thermal.png")
    assert result.errors == ()
    assert csv_writer.calls[0][0:2] == (NOW, 7)
    assert image_writer.calls[0][1:3] == (NOW, 7)


def test_thermal_failure_still_saves_gas_row() -> None:
    csv_writer = RecordingCsvWriter()
    image_writer = RecordingImageWriter()
    logger = SensorLogger(GoodGasReader(), BrokenCamera(), csv_writer, image_writer)

    result = logger.capture(1, NOW)

    assert result.csv_path == Path("data/gas/gas_2026-08-03.csv")
    assert result.png_path is None
    assert result.errors == ("热像读取或保存失败：I2C 无响应",)
    assert csv_writer.calls[0][3] == result.errors


def test_gas_reader_failure_still_saves_thermal_image_and_blank_gases() -> None:
    csv_writer = RecordingCsvWriter()
    image_writer = RecordingImageWriter()
    logger = SensorLogger(BrokenGasReader(), GoodCamera(), csv_writer, image_writer)

    result = logger.capture(2, NOW)

    assert result.png_path == Path("data/thermal/thermal.png")
    assert "气体读取失败：串口已断开" in result.errors
    saved_readings = csv_writer.calls[0][2]
    assert [reading.channel for reading in saved_readings] == [
        "ch4",
        "o2",
        "co",
        "h2s",
    ]
    assert all(reading.value is None for reading in saved_readings)


class FakeTime:
    def __init__(self) -> None:
        self.current = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds


def test_periodic_loop_does_not_accumulate_capture_time() -> None:
    fake = FakeTime()
    captured_ids = []

    def capture(sample_id, timestamp):
        captured_ids.append(sample_id)
        fake.advance(1.0)

    run_periodic(
        capture,
        interval=5.0,
        clock=fake.monotonic,
        sleep=fake.sleep,
        now=lambda: NOW,
        max_samples=3,
    )

    assert captured_ids == [1, 2, 3]
    assert fake.sleeps == [4.0, 4.0]


def test_periodic_loop_rejects_non_positive_interval() -> None:
    with pytest.raises(ValueError, match="采样间隔"):
        run_periodic(lambda *_: None, interval=0, max_samples=1)


def test_periodic_loop_skips_missed_deadlines_instead_of_catching_up() -> None:
    fake = FakeTime()

    def capture(_sample_id, _timestamp):
        fake.advance(5.0)

    run_periodic(
        capture,
        interval=2.0,
        clock=fake.monotonic,
        sleep=fake.sleep,
        now=lambda: NOW,
        max_samples=2,
    )

    assert fake.sleeps == [1.0]
