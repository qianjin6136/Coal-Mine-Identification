"""MLX90640 帧读取与伪彩色 PNG 生成。"""

import math
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH = 32
HEIGHT = 24
PIXEL_COUNT = WIDTH * HEIGHT
OUTPUT_WIDTH = 640
OUTPUT_HEIGHT = 480
HEADER_HEIGHT = 64


def thermal_color(position: float) -> tuple[int, int, int]:
    """把 0-1 的归一化温度映射为蓝到红的伪彩色。"""

    position = max(0.0, min(1.0, position))
    stops = (
        (0.00, (0, 0, 128)),
        (0.25, (0, 128, 255)),
        (0.50, (0, 255, 128)),
        (0.75, (255, 255, 0)),
        (1.00, (255, 0, 0)),
    )
    for (left_position, left_color), (right_position, right_color) in zip(
        stops, stops[1:]
    ):
        if position <= right_position:
            ratio = (position - left_position) / (
                right_position - left_position
            )
            return tuple(
                round(left + (right - left) * ratio)
                for left, right in zip(left_color, right_color)
            )
    return stops[-1][1]


class Mlx90640Camera:
    """通过 Blinka 打开树莓派 I2C 上的 MLX90640。"""

    def __init__(self) -> None:
        # 延迟导入保证 Windows 上可运行无硬件单元测试。
        import adafruit_mlx90640
        import board
        import busio

        self._i2c = busio.I2C(board.SCL, board.SDA, frequency=400_000)
        self._sensor = adafruit_mlx90640.MLX90640(self._i2c)
        self._sensor.refresh_rate = (
            adafruit_mlx90640.RefreshRate.REFRESH_2_HZ
        )

    def read_frame(self) -> list[float]:
        frame = [0.0] * PIXEL_COUNT
        for attempt in range(5):
            try:
                self._sensor.getFrame(frame)
                return frame
            except ValueError:
                if attempt == 4:
                    raise
                time.sleep(0.1)
        raise RuntimeError("MLX90640 帧读取失败")

    def close(self) -> None:
        deinit = getattr(self._i2c, "deinit", None)
        if callable(deinit):
            deinit()


class ThermalImageWriter:
    """将一个 32×24 温度帧保存为带统计信息的 PNG。"""

    def __init__(self, root: Path) -> None:
        self.directory = Path(root) / "thermal"

    def write(
        self,
        temperatures: Sequence[float],
        timestamp: datetime,
        sample_id: int,
    ) -> Path:
        values = [float(value) for value in temperatures]
        if len(values) != PIXEL_COUNT:
            raise ValueError(
                f"MLX90640 温度帧必须包含 768 个像素，收到 {len(values)} 个"
            )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("温度帧只能包含有限数值")

        minimum = min(values)
        maximum = max(values)
        average = sum(values) / len(values)
        span = maximum - minimum
        positions = (
            [0.5] * PIXEL_COUNT
            if span == 0
            else [(value - minimum) / span for value in values]
        )

        raw_image = Image.new("RGB", (WIDTH, HEIGHT))
        raw_image.putdata([thermal_color(position) for position in positions])
        heatmap = raw_image.resize(
            (OUTPUT_WIDTH, OUTPUT_HEIGHT), Image.Resampling.NEAREST
        )

        output = Image.new(
            "RGB", (OUTPUT_WIDTH, HEADER_HEIGHT + OUTPUT_HEIGHT), "#101820"
        )
        output.paste(heatmap, (0, HEADER_HEIGHT))
        draw = ImageDraw.Draw(output)
        font = _load_font()
        draw.text(
            (12, 6),
            f"MLX90640  {timestamp.isoformat(timespec='seconds')}  Sample {sample_id:06d}",
            fill="white",
            font=font,
        )
        draw.text(
            (12, 34),
            f"Min {minimum:.2f} C    Max {maximum:.2f} C    Avg {average:.2f} C",
            fill="#A7E8FF",
            font=font,
        )

        self.directory.mkdir(parents=True, exist_ok=True)
        filename = f"thermal_{timestamp:%Y%m%d_%H%M%S}_{sample_id:06d}.png"
        final_path = self.directory / filename
        temporary_path = final_path.with_suffix(".tmp")
        try:
            output.save(temporary_path, format="PNG")
            temporary_path.replace(final_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return final_path


def _load_font():
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 18)
    except OSError:
        return ImageFont.load_default()
