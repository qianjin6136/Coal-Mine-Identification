"""采集器在各模块之间传递的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class GasReading:
    """一个气体通道的一次读数。"""

    channel: str
    slave_id: int
    value: Decimal | None
    unit: str = ""
    status: str = "unknown"
    error: str = ""


@dataclass(frozen=True)
class SampleResult:
    """一次完整采样尝试产生的文件和错误。"""

    sample_id: int
    timestamp: datetime
    csv_path: Path
    png_path: Path | None
    errors: tuple[str, ...] = ()
