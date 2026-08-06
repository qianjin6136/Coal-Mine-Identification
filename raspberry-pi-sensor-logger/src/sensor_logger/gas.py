"""4MZ-HH4 四通道气体模组读取与寄存器解析。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from sensor_logger.modbus import build_read_request, parse_read_response
from sensor_logger.models import GasReading

LOGGER = logging.getLogger(__name__)

# 规格书定义的四个从机地址
CHANNELS: tuple[tuple[str, int], ...] = (
    ("ch4", 0x01),
    ("o2", 0x02),
    ("co", 0x03),
    ("h2s", 0x04),
)

# 寄存器0的 Bit12-Bit15：单位
UNIT_CODES = {
    0x0: "ppm",
    0x2: "%LEL",
    0x4: "%VOL",
    0x6: "mg/m3",
    0x8: "ppb",
}

# 寄存器0的 Bit8-Bit11：小数位
DECIMAL_CODES = {
    0x0: 0,
    0x4: 1,
    0x8: 2,
    0xC: 3,
}

# 寄存器5低8位：传感器状态
STATUS_CODES = {
    0x00: "warming",
    0x01: "normal",
    0x02: "data_error",
    0x03: "sensor_fault",
    0x04: "warning",
    0x05: "low_alarm",
    0x06: "high_alarm",
    0x07: "access_fault",
    0x08: "over_range",
    0x09: "calibration_required",
    0x0A: "timeout",
    0x0B: "stel_alarm",
    0x0C: "twa_alarm",
    0x0D: "reserved",
    0x0E: "reserved",
    0x0F: "communication_fault",
}


class SerialPort(Protocol):
    """FourGasReader使用的串口接口。"""

    def reset_input_buffer(self) -> None: ...

    def write(self, data: bytes) -> int | None: ...

    def read(self, size: int) -> bytes: ...


def decode_registers(
    channel: str,
    slave_id: int,
    registers: Sequence[int],
) -> GasReading:
    """按照4MZ-HH4规格书解析寄存器0-5。"""

    if len(registers) < 6:
        raise ValueError("解析气体数据至少需要6个寄存器")

    # 寄存器0包含单位、小数位和浓度最高两位
    parameter = registers[0]

    # Bit8-Bit11：小数位编码
    decimal_code = (parameter >> 8) & 0x0F
    if decimal_code not in DECIMAL_CODES:
        raise ValueError(
            f"未知的小数位编码：0x{decimal_code:X}"
        )

    decimal_places = DECIMAL_CODES[decimal_code]

    # Bit12-Bit15：单位编码
    unit_code = (parameter >> 12) & 0x0F

    # Bit6-Bit7：浓度第17、18位
    high_bits = (parameter >> 6) & 0x03

    # 寄存器1：浓度低16位
    raw_value = (high_bits << 16) | registers[1]

    # 按传感器声明的小数位计算，不使用float
    value = Decimal(raw_value).scaleb(-decimal_places)

    # 寄存器5低8位：工作状态
    status_code = registers[5] & 0xFF

    return GasReading(
        channel=channel,
        slave_id=slave_id,
        value=value,
        unit=UNIT_CODES.get(
            unit_code,
            f"unknown(0x{unit_code:X})",
        ),
        status=STATUS_CODES.get(
            status_code,
            f"unknown(0x{status_code:02X})",
        ),
    )


class FourGasReader:
    """依次读取CH4、O2、CO、H2S。"""

    # 读取寄存器0-5，共6个寄存器
    register_count = 6

    # 地址1字节 + 功能码1字节 + 字节数1字节
    # + 6个寄存器共12字节 + CRC 2字节
    response_length = 5 + register_count * 2

    def __init__(
        self,
        serial_port: SerialPort,
        retries: int = 2,
    ) -> None:
        if retries < 0:
            raise ValueError("重试次数不能为负数")

        self._serial = serial_port
        self._retries = retries

    def read_all(self) -> tuple[GasReading, ...]:
        """按规格书地址顺序读取四个气体通道。"""

        return tuple(
            self._read_channel(channel, slave_id)
            for channel, slave_id in CHANNELS
        )

    def _read_channel(
        self,
        channel: str,
        slave_id: int,
    ) -> GasReading:
        """读取一个气体通道，失败时独立重试。"""

        request = build_read_request(
            slave_id,
            start_register=0,
            register_count=self.register_count,
        )

        last_error = "未知错误"

        for attempt in range(1, self._retries + 2):
            try:
                # 丢弃之前残留的串口数据
                self._serial.reset_input_buffer()

                # 发送Modbus 0x03读取请求
                self._serial.write(request)

                # 读取本次完整响应帧
                frame = self._serial.read(
                    self.response_length
                )

                LOGGER.debug(
                    "原始帧 channel=%s slave=%d "
                    "attempt=%d length=%d frame=%s",
                    channel,
                    slave_id,
                    attempt,
                    len(frame),
                    frame.hex(" "),
                )

                if not frame:
                    raise TimeoutError("串口响应超时")

                # 校验CRC、地址、功能码、长度和字节数
                registers = parse_read_response(
                    frame,
                    slave_id=slave_id,
                    register_count=self.register_count,
                )

                # 解析本次新读到的寄存器
                reading = decode_registers(
                    channel,
                    slave_id,
                    registers,
                )

                decimal_code = (
                    registers[0] >> 8
                ) & 0x0F
                decimal_places = DECIMAL_CODES[
                    decimal_code
                ]

                LOGGER.debug(
                    "解析结果 channel=%s slave=%d "
                    "registers=%s decimal_places=%d "
                    "value=%s unit=%s status=%s",
                    channel,
                    slave_id,
                    registers,
                    decimal_places,
                    reading.value,
                    reading.unit,
                    reading.status,
                )

                return reading

            except Exception as error:
                last_error = str(error)

                LOGGER.warning(
                    "读取失败 channel=%s slave=%d "
                    "attempt=%d/%d error=%s",
                    channel,
                    slave_id,
                    attempt,
                    self._retries + 1,
                    error,
                )

        # 重试全部失败时返回空值，不使用上一组数据
        return GasReading(
            channel=channel,
            slave_id=slave_id,
            value=None,
            status="read_error",
            error=f"{channel} 读取失败：{last_error}",
        )
