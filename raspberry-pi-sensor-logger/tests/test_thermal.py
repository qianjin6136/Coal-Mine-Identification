from datetime import datetime, timedelta, timezone
from math import nan

import pytest
from PIL import Image

from sensor_logger.thermal import ThermalImageWriter, thermal_color

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
    assert not list(path.parent.glob("*.tmp"))


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
