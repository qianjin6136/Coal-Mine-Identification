from datetime import datetime, timedelta, timezone
import json
from math import nan

import pytest
from PIL import Image

from sensor_logger.thermal import (
    THERMAL_STATS_METADATA_KEY,
    ThermalImageWriter,
    _median_filter_3x3,
    _percentile,
    thermal_color,
)

NOW = datetime(2026, 8, 3, 15, 30, 5, tzinfo=timezone(timedelta(hours=8)))


def test_color_map_has_cold_and_hot_endpoints() -> None:
    assert thermal_color(0.0) == (0, 0, 128)
    assert thermal_color(1.0) == (255, 0, 0)
    assert thermal_color(-1.0) == thermal_color(0.0)
    assert thermal_color(2.0) == thermal_color(1.0)


def test_writes_timestamped_png_atomically(tmp_path) -> None:
    temperatures = [20.0 + index / 100 for index in range(32 * 24)]

    path = ThermalImageWriter(tmp_path).write(temperatures, NOW, 7)

    assert path == (
        tmp_path / "thermal" / "thermal_20260803_153005_000007.png"
    )
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.size == (640, 544)
        assert image.getbbox() is not None
        stats = json.loads(image.text[THERMAL_STATS_METADATA_KEY])
    assert stats == {
        "schema_version": 1,
        "captured_at": "2026-08-03T15:30:05+08:00",
        "sample_id": 7,
        "width": 32,
        "height": 24,
        "minimum_c": 20.0,
        "maximum_c": 27.67,
        "average_c": pytest.approx(sum(temperatures) / len(temperatures)),
    }
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("maximum", [64.99, 65.0, 65.01])
def test_writes_all_frames_regardless_of_temperature(tmp_path, maximum) -> None:
    temperatures = [25.0] * 768
    temperatures[-1] = maximum

    path = ThermalImageWriter(tmp_path).write(temperatures, NOW, 1)

    assert path.is_file()
    with Image.open(path) as image:
        stats = json.loads(image.text[THERMAL_STATS_METADATA_KEY])
    assert stats["maximum_c"] == maximum


def test_renders_constant_temperature_frame(tmp_path) -> None:
    path = ThermalImageWriter(tmp_path).write([25.0] * 768, NOW, 1)

    with Image.open(path) as image:
        heatmap = image.crop((0, 64, 640, 544))
        assert len(heatmap.getcolors(maxcolors=640 * 480)) == 1


@pytest.mark.parametrize("temperatures", [[25.0], [25.0] * 767, [25.0] * 769])
def test_rejects_wrong_pixel_count(tmp_path, temperatures) -> None:
    with pytest.raises(ValueError, match="768"):
        ThermalImageWriter(tmp_path).write(temperatures, NOW, 1)


def test_rejects_non_finite_temperature(tmp_path) -> None:
    temperatures = [25.0] * 768
    temperatures[10] = nan

    with pytest.raises(ValueError, match="有限"):
        ThermalImageWriter(tmp_path).write(temperatures, NOW, 1)


def test_display_filters_isolated_hot_pixel() -> None:
    temperatures = [20.0] * 768
    center = 12 * 32 + 16
    temperatures[center] = 100.0

    filtered = _median_filter_3x3(temperatures)

    assert filtered[center] == 20.0
    assert _percentile([0.0, 10.0, 20.0], 0.5) == 10.0
