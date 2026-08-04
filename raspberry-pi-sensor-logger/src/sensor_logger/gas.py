"""4MZ-HH4 四通道气体模组读取与寄存器解析。"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from sensor_logger.modbus import build_read_request, parse_read_response
from sensor_logger.models import GasReading

CHANNELS: tuple[tuple[str, int], ...] = (
    ("ch4", 0x01),
    ("o2", 0x02),
    ("co", 0x03),
    ("h2s", 0x04),
)

UNIT_CODES = {
    0x0: "ppm",
    0x2: "%LEL",
    0x4: "%VOL",
    0x6: "mg/m3",
    0x8: "ppb",
}

DECIMAL_CODES = {
    0x0: 0,
    0x4: 1,
    0x8: 2,
    0xC: 3,
}

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
    """FourGasReader 所需的最小串口接口。"""

    def reset_input_buffer(self) -> None: ...

    def write(self, data: bytes) -> int | None: ...

    def read(self, size: int) -> bytes: ...


def decode_registers(
    channel: str, slave_id: int, registers: Sequence[int]
) -> GasReading:
    """按照 4MZ-HH4 规格书解析寄存器 0-5。"""

    if len(registers) < 6:
        raise ValueError("解析气体数据至少需要 6 个寄存器")

    parameter = registers[0]
    decimal_code = (parameter >> 8) & 0x0F
    if decimal_code not in DECIMAL_CODES:
        raise ValueError(f"未知的小数位编码：0x{decimal_code:X}")

    unit_code = (parameter >> 12) & 0x0F
    high_bits = (parameter >> 6) & 0x03
    raw_value = (high_bits << 16) | registers[1]
    value = Decimal(raw_value).scaleb(-DECIMAL_CODES[decimal_code])
    status_code = registers[5] & 0xFF

    return GasReading(
        channel=channel,
        slave_id=slave_id,
        value=value,
        unit=UNIT_CODES.get(unit_code, f"unknown(0x{unit_code:X})"),
        status=STATUS_CODES.get(status_code, f"unknown(0x{status_code:02X})"),
    )


class FourGasReader:
    """依次查询 CH4、O2、CO、H2S，单通道失败不影响其他通道。"""

    register_count = 6
    response_length = 5 + register_count * 2

    def __init__(self, serial_port: SerialPort, retries: int = 2) -> None:
        if retries < 0:
            raise ValueError("重试次数不能为负数")
        self._serial = serial_port
        self._retries = retries

    def read_all(self) -> tuple[GasReading, ...]:
        return tuple(
            self._read_channel(channel, slave_id)
            for channel, slave_id in CHANNELS
        )

    def _read_channel(self, channel: str, slave_id: int) -> GasReading:
        request = build_read_request(slave_id, 0, self.register_count)
        last_error = "未知错误"

        for _ in range(self._retries + 1):
            try:
                self._serial.reset_input_buffer()
                self._serial.write(request)
                frame = self._serial.read(self.response_length)
                if not frame:
                    raise TimeoutError("串口响应超时")
                registers = parse_read_response(
                    frame, slave_id=slave_id, register_count=self.register_count
                )
                return decode_registers(channel, slave_id, registers)
            except Exception as error:  # 每个通道必须独立失败并继续采集
                last_error = str(error)

        return GasReading(
            channel=channel,
            slave_id=slave_id,
            value=None,
            status="read_error",
            error=f"{channel} 读取失败：{last_error}",
        )
