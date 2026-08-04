"""采样协调与固定周期调度。"""

import time
import math
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from sensor_logger.gas import CHANNELS
from sensor_logger.models import GasReading, SampleResult


class GasReader(Protocol):
    def read_all(self) -> tuple[GasReading, ...]: ...


class Camera(Protocol):
    def read_frame(self) -> list[float]: ...


class CsvWriter(Protocol):
    def append(self, timestamp, sample_id, readings, errors=()): ...


class ImageWriter(Protocol):
    def write(self, temperatures, timestamp, sample_id): ...


class StopEvent(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float) -> bool: ...


class SensorLogger:
    """在同一个 sample_id 下保存气体表格和热像图片。"""

    def __init__(
        self,
        gas_reader: GasReader,
        camera: Camera,
        csv_writer: CsvWriter,
        image_writer: ImageWriter,
    ) -> None:
        self._gas_reader = gas_reader
        self._camera = camera
        self._csv_writer = csv_writer
        self._image_writer = image_writer

    def capture(self, sample_id: int, timestamp: datetime) -> SampleResult:
        service_errors: list[str] = []
        try:
            readings = self._gas_reader.read_all()
        except Exception as error:  # 串口整体故障时仍要保存热像
            message = f"气体读取失败：{error}"
            service_errors.append(message)
            readings = tuple(
                GasReading(
                    channel=channel,
                    slave_id=slave_id,
                    value=None,
                    status="read_error",
                )
                for channel, slave_id in CHANNELS
            )

        png_path = None
        try:
            frame = self._camera.read_frame()
            png_path = self._image_writer.write(frame, timestamp, sample_id)
        except Exception as error:  # I2C 或磁盘故障不能丢失气体行
            service_errors.append(f"热像读取或保存失败：{error}")

        csv_path = self._csv_writer.append(
            timestamp, sample_id, readings, errors=service_errors
        )
        reading_errors = tuple(
            reading.error for reading in readings if reading.error
        )
        return SampleResult(
            sample_id=sample_id,
            timestamp=timestamp,
            csv_path=csv_path,
            png_path=png_path,
            errors=reading_errors + tuple(service_errors),
        )


def run_periodic(
    capture: Callable[[int, datetime], object],
    interval: float = 5.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    max_samples: int | None = None,
    stop_event: StopEvent | None = None,
) -> None:
    """立即采样一次，之后按单调时钟的绝对截止时间重复。"""

    if interval <= 0:
        raise ValueError("采样间隔必须大于 0 秒")
    if max_samples is not None and max_samples <= 0:
        return

    deadline = clock()
    sample_id = 1
    while max_samples is None or sample_id <= max_samples:
        if stop_event is not None and stop_event.is_set():
            break
        capture(sample_id, now())
        if max_samples is not None and sample_id >= max_samples:
            break
        sample_id += 1
        deadline += interval
        delay = deadline - clock()

        # 长时间读硬件或系统暂停后跳过已经错过的周期，避免密集补采。
        if delay < 0:
            missed = math.ceil((-delay) / interval)
            deadline += missed * interval
            delay = deadline - clock()
        if delay > 0:
            if stop_event is not None:
                if stop_event.wait(delay):
                    break
            else:
                sleep(delay)
