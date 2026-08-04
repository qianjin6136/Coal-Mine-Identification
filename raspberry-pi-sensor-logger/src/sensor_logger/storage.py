"""气体浓度 CSV 保存。"""

import csv
import os
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from sensor_logger.models import GasReading

GAS_CHANNELS = ("ch4", "o2", "co", "h2s")
FIELDS = ["timestamp", "sample_id"] + [
    f"{channel}_{suffix}"
    for channel in GAS_CHANNELS
    for suffix in ("value", "unit", "status")
] + ["error"]


class GasCsvWriter:
    """把一次四通道采样写为同一行，每天创建一个文件。"""

    def __init__(self, root: Path) -> None:
        self.directory = Path(root) / "gas"

    def append(
        self,
        timestamp: datetime,
        sample_id: int,
        readings: Sequence[GasReading],
        errors: Sequence[str] = (),
    ) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"gas_{timestamp:%Y-%m-%d}.csv"
        row = dict.fromkeys(FIELDS, "")
        row["timestamp"] = timestamp.isoformat(timespec="seconds")
        row["sample_id"] = f"{sample_id:06d}"

        reading_errors: list[str] = []
        for reading in readings:
            if reading.channel not in GAS_CHANNELS:
                continue
            prefix = reading.channel
            row[f"{prefix}_value"] = (
                "" if reading.value is None else format(reading.value, "f")
            )
            row[f"{prefix}_unit"] = reading.unit
            row[f"{prefix}_status"] = reading.status
            if reading.error:
                reading_errors.append(reading.error)

        row["error"] = "; ".join(
            message for message in (*reading_errors, *errors) if message
        )

        with path.open("a", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            if stream.tell() == 0:
                writer.writeheader()
            writer.writerow(row)
            stream.flush()
            os.fsync(stream.fileno())

        return path
