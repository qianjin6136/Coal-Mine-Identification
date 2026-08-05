"""气体浓度 CSV 表格保存程序。"""

import csv
import os
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from sensor_logger.models import GasReading


# CSV 表格固定为这 7 列
FIELDS = [
    "时间",
    "编号",
    "CH4(%LEL)",
    "O2(%VOL)",
    "CO(ppm)",
    "H2S(ppm)",
    "状态",
]


# 传感器通道与 CSV 列名的对应关系
VALUE_FIELDS = {
    "ch4": "CH4(%LEL)",
    "o2": "O2(%VOL)",
    "co": "CO(ppm)",
    "h2s": "H2S(ppm)",
}


# 状态栏中显示的气体名称
DISPLAY_NAMES = {
    "ch4": "CH4",
    "o2": "O2",
    "co": "CO",
    "h2s": "H2S",
}


class GasCsvWriter:
    """每次把四种气体浓度写入 CSV 的同一行。"""

    def __init__(self, root: Path) -> None:
        # 气体表格统一保存在 data/gas 文件夹
        self.directory = Path(root) / "gas"

    def append(
        self,
        timestamp: datetime,
        sample_id: int,
        readings: Sequence[GasReading],
        errors: Sequence[str] = (),
    ) -> Path:
        """向当天的 CSV 文件追加一行数据。"""

        # 如果 data/gas 文件夹不存在，就自动创建
        self.directory.mkdir(parents=True, exist_ok=True)

        # 每天创建一个新的 CSV 文件
        path = self.directory / f"gas_{timestamp:%Y-%m-%d}.csv"

        # 判断是否是一个新文件
        is_new_file = not path.exists() or path.stat().st_size == 0

        # 检查已有文件是不是新的 7 列格式
        if not is_new_file:
            self._validate_header(path)

        # 创建一行空数据
        row = dict.fromkeys(FIELDS, "")

        # 保存采集时间和编号
        row["时间"] = timestamp.isoformat(timespec="seconds")
        row["编号"] = f"{sample_id:06d}"

        # 把四种气体读数按照通道名称保存
        readings_by_channel = {
            reading.channel.lower(): reading
            for reading in readings
            if reading.channel.lower() in VALUE_FIELDS
        }

        status_messages: list[str] = []

        # 按固定顺序处理四种气体
        for channel in ("ch4", "o2", "co", "h2s"):
            reading = readings_by_channel.get(channel)

            # 如果没有收到这个气体的读数
            if reading is None:
                status_messages.append(
                    f"{DISPLAY_NAMES[channel]}缺少读数"
                )
                continue

            # 将浓度写入对应列
            if reading.value is not None:
                row[VALUE_FIELDS[channel]] = format(
                    reading.value,
                    "f",
                )

            # 如果传感器返回错误，就写入状态栏
            if reading.error:
                status_messages.append(
                    f"{DISPLAY_NAMES[channel]}：{reading.error}"
                )

            # 如果传感器状态不是 normal，也写入状态栏
            elif reading.status not in ("normal", ""):
                status_messages.append(
                    f"{DISPLAY_NAMES[channel]}状态："
                    f"{reading.status}"
                )

        # 加入采集程序返回的其他错误
        for error in errors:
            if error:
                status_messages.append(error)

        # 没有错误时显示“正常”
        if status_messages:
            row["状态"] = "；".join(
                dict.fromkeys(status_messages)
            )
        else:
            row["状态"] = "正常"

        # 新文件使用 utf-8-sig，Windows Excel 可以正常显示中文
        encoding = "utf-8-sig" if is_new_file else "utf-8"

        with path.open(
            "a",
            encoding=encoding,
            newline="",
        ) as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=FIELDS,
            )

            # 只有新文件才写入表头
            if is_new_file:
                writer.writeheader()

            # 写入这一组四气体浓度
            writer.writerow(row)

            # 立即保存到磁盘
            stream.flush()
            os.fsync(stream.fileno())

        return path

    @staticmethod
    def _validate_header(path: Path) -> None:
        """检查已有 CSV 是否为新的 7 列格式。"""

        with path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as stream:
            existing_fields = next(csv.reader(stream), [])

        if existing_fields != FIELDS:
            raise ValueError(
                f"{path} 还是旧表格格式，"
                "请先把旧文件改名或移动到其他文件夹"
            )