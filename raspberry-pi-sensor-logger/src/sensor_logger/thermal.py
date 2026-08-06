"""MLX90640 帧读取、降噪与平滑伪彩色 PNG 生成。"""

import json
import math
import statistics
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

WIDTH = 32
HEIGHT = 24
PIXEL_COUNT = WIDTH * HEIGHT
OUTPUT_WIDTH = 640
OUTPUT_HEIGHT = 480
HEADER_HEIGHT = 64
THERMAL_STATS_METADATA_KEY = "thermal_stats_v1"
THERMAL_STATS_SCHEMA_VERSION = 1

# 每次温度采样读取三帧并逐像素取中值，降低偶发跳点。
FRAMES_PER_READING = 3

# 显示范围使用 2%～98% 分位数，避免单个异常像素控制整幅图的颜色。
DISPLAY_LOW_PERCENTILE = 0.02
DISPLAY_HIGH_PERCENTILE = 0.98
DISPLAY_EMA_ALPHA = 0.25
MINIMUM_DISPLAY_SPAN_C = 2.0


def thermal_color(position: float) -> tuple[int, int, int]:
    """把 0～1 的归一化温度映射为蓝到红的伪彩色。"""

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


def _percentile(values: Sequence[float], fraction: float) -> float:
    """用线性插值计算分位数，不引入 NumPy 依赖。"""

    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("不能计算空温度序列的分位数")
    position = (len(ordered) - 1) * fraction
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    ratio = position - lower_index
    return (
        ordered[lower_index] * (1.0 - ratio)
        + ordered[upper_index] * ratio
    )


def _median_filter_3x3(values: Sequence[float]) -> list[float]:
    """对 32×24 温度矩阵做 3×3 中值滤波，去除孤立亮点。"""

    filtered: list[float] = []
    for y in range(HEIGHT):
        for x in range(WIDTH):
            neighbours = [
                float(values[row * WIDTH + column])
                for row in range(max(0, y - 1), min(HEIGHT, y + 2))
                for column in range(max(0, x - 1), min(WIDTH, x + 2))
            ]
            filtered.append(float(statistics.median(neighbours)))
    return filtered


class Mlx90640Camera:
    """通过 Blinka 打开树莓派 I2C 上的 MLX90640。"""

    def __init__(self, frames_per_reading: int = FRAMES_PER_READING) -> None:
        if frames_per_reading <= 0:
            raise ValueError("每次温度采样帧数必须大于 0")

        # 延迟导入，保证无树莓派硬件的环境仍可导入本模块。
        import adafruit_mlx90640
        import board
        import busio

        self._i2c = busio.I2C(board.SCL, board.SDA, frequency=400_000)
        self._sensor = adafruit_mlx90640.MLX90640(self._i2c)
        self._sensor.refresh_rate = (
            adafruit_mlx90640.RefreshRate.REFRESH_2_HZ
        )
        self._frames_per_reading = frames_per_reading

    def _read_single_frame(self) -> list[float]:
        frame = [0.0] * PIXEL_COUNT
        for attempt in range(5):
            try:
                self._sensor.getFrame(frame)
                return frame
            except ValueError:
                if attempt == 4:
                    raise
                time.sleep(0.1)
        raise RuntimeError("MLX90640 单帧读取失败")

    def read_frame(self) -> list[float]:
        """读取多帧并逐像素取中值，返回一个稳定的 32×24 温度帧。"""

        frames: list[list[float]] = []
        for index in range(self._frames_per_reading):
            frames.append(self._read_single_frame())
            if index + 1 < self._frames_per_reading:
                # 2 Hz 刷新率下给下一帧留出更新时间。
                time.sleep(0.25)

        if len(frames) == 1:
            return frames[0]
        return [
            float(statistics.median(frame[pixel] for frame in frames))
            for pixel in range(PIXEL_COUNT)
        ]

    def close(self) -> None:
        deinit = getattr(self._i2c, "deinit", None)
        if callable(deinit):
            deinit()


class ThermalImageWriter:
    """将 32×24 温度帧保存为平滑且带统计信息的 PNG。"""

    def __init__(self, root: Path) -> None:
        self.directory = Path(root) / "thermal"
        self._display_minimum: float | None = None
        self._display_maximum: float | None = None

    def _display_bounds(self, values: Sequence[float]) -> tuple[float, float]:
        target_minimum = _percentile(values, DISPLAY_LOW_PERCENTILE)
        target_maximum = _percentile(values, DISPLAY_HIGH_PERCENTILE)

        if target_maximum - target_minimum < MINIMUM_DISPLAY_SPAN_C:
            midpoint = (target_minimum + target_maximum) / 2.0
            half_span = MINIMUM_DISPLAY_SPAN_C / 2.0
            target_minimum = midpoint - half_span
            target_maximum = midpoint + half_span

        if self._display_minimum is None or self._display_maximum is None:
            self._display_minimum = target_minimum
            self._display_maximum = target_maximum
        else:
            alpha = DISPLAY_EMA_ALPHA
            # 新出现的更冷/更热目标立即扩展色域；恢复时缓慢收缩，减少闪烁。
            self._display_minimum = (
                target_minimum
                if target_minimum < self._display_minimum
                else self._display_minimum * (1.0 - alpha)
                + target_minimum * alpha
            )
            self._display_maximum = (
                target_maximum
                if target_maximum > self._display_maximum
                else self._display_maximum * (1.0 - alpha)
                + target_maximum * alpha
            )

        return self._display_minimum, self._display_maximum

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

        # 中值滤波只用于显示；标题统计仍由多帧中值温度数据计算。
        display_values = _median_filter_3x3(values)
        display_minimum, display_maximum = self._display_bounds(display_values)
        display_span = display_maximum - display_minimum
        positions = [
            (value - display_minimum) / display_span
            for value in display_values
        ]

        # 先对归一化温度场做 BICUBIC 插值，再映射颜色，避免 20×20 大色块。
        position_image = Image.new("F", (WIDTH, HEIGHT))
        position_image.putdata(positions)
        smooth_positions = position_image.resize(
            (OUTPUT_WIDTH, OUTPUT_HEIGHT),
            Image.Resampling.BICUBIC,
        )
        heatmap = Image.new("RGB", (OUTPUT_WIDTH, OUTPUT_HEIGHT))
        heatmap.putdata(
            [thermal_color(float(position)) for position in smooth_positions.getdata()]
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
        png_info = PngImagePlugin.PngInfo()
        png_info.add_text(
            THERMAL_STATS_METADATA_KEY,
            json.dumps(
                {
                    "schema_version": THERMAL_STATS_SCHEMA_VERSION,
                    "captured_at": timestamp.isoformat(timespec="seconds"),
                    "sample_id": sample_id,
                    "width": WIDTH,
                    "height": HEIGHT,
                    "minimum_c": minimum,
                    "maximum_c": maximum,
                    "average_c": average,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        try:
            output.save(temporary_path, format="PNG", pnginfo=png_info)
            temporary_path.replace(final_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return final_path


def _load_font():
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 18)
    except OSError:
        return ImageFont.load_default()
