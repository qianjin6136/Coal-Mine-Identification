from collections import deque
from decimal import Decimal

import pytest

from sensor_logger.gas import FourGasReader, decode_registers
from sensor_logger.modbus import crc16


def response_frame(slave_id: int, registers: list[int]) -> bytes:
    data = b"".join(value.to_bytes(2, "big") for value in registers)
    payload = bytes((slave_id, 0x03, len(data))) + data
    return payload + crc16(payload).to_bytes(2, "little")


class ScriptedSerial:
    def __init__(self, scripts: dict[int, list[bytes]]) -> None:
        self.scripts = {
            slave_id: deque(responses) for slave_id, responses in scripts.items()
        }
        self.current = b""
        self.requests: list[bytes] = []
        self.reset_count = 0

    def reset_input_buffer(self) -> None:
        self.current = b""
        self.reset_count += 1

    def write(self, request: bytes) -> int:
        self.requests.append(request)
        responses = self.scripts.get(request[0], deque())
        self.current = responses.popleft() if responses else b""
        return len(request)

    def read(self, size: int) -> bytes:
        result, self.current = self.current[:size], self.current[size:]
        return result


def normal_registers(value: int = 0) -> list[int]:
    return [0x0000, value, 35, 200, 1000, 0x0001]


def test_decodes_oxygen_decimal_and_unit() -> None:
    reading = decode_registers("o2", 2, [0x4400, 209, 195, 235, 300, 1])

    assert reading.value == Decimal("20.9")
    assert reading.unit == "%VOL"
    assert reading.status == "normal"


def test_decodes_18_bit_concentration() -> None:
    reading = decode_registers("co", 3, [0x0040, 1, 0, 0, 0, 6])

    assert reading.value == Decimal("65537")
    assert reading.status == "high_alarm"


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(0, "warming"), (3, "sensor_fault"), (5, "low_alarm"), (15, "communication_fault")],
)
def test_decodes_sensor_status(status_code: int, expected: str) -> None:
    assert decode_registers(
        "ch4", 1, [0, 0, 0, 0, 0, status_code]
    ).status == expected


def test_rejects_invalid_decimal_code() -> None:
    with pytest.raises(ValueError, match="小数位"):
        decode_registers("co", 3, [0x0100, 1, 0, 0, 0, 1])


def test_reader_reads_channels_in_documented_order() -> None:
    serial_port = ScriptedSerial(
        {
            slave_id: [response_frame(slave_id, normal_registers(slave_id))]
            for slave_id in range(1, 5)
        }
    )

    readings = FourGasReader(serial_port).read_all()

    assert [(item.channel, item.slave_id) for item in readings] == [
        ("ch4", 1),
        ("o2", 2),
        ("co", 3),
        ("h2s", 4),
    ]
    assert [item.value for item in readings] == [
        Decimal("1"),
        Decimal("2"),
        Decimal("3"),
        Decimal("4"),
    ]
    assert all(request[2:6] == bytes.fromhex("00 00 00 06") for request in serial_port.requests)


def test_reader_retries_bad_crc_then_accepts_valid_frame() -> None:
    valid = response_frame(1, normal_registers(12))
    bad_crc = valid[:-2] + b"\x00\x00"
    serial_port = ScriptedSerial(
        {
            1: [bad_crc, valid],
            2: [response_frame(2, normal_registers())],
            3: [response_frame(3, normal_registers())],
            4: [response_frame(4, normal_registers())],
        }
    )

    readings = FourGasReader(serial_port, retries=1).read_all()

    assert readings[0].value == Decimal("12")
    assert len([request for request in serial_port.requests if request[0] == 1]) == 2


def test_reader_keeps_other_channels_when_one_times_out() -> None:
    serial_port = ScriptedSerial(
        {
            1: [response_frame(1, normal_registers(11))],
            2: [b"", b""],
            3: [response_frame(3, normal_registers(33))],
            4: [response_frame(4, normal_registers(44))],
        }
    )

    readings = FourGasReader(serial_port, retries=1).read_all()

    assert readings[0].value == Decimal("11")
    assert readings[1].value is None
    assert readings[1].status == "read_error"
    assert "超时" in readings[1].error
    assert readings[2].value == Decimal("33")
    assert readings[3].value == Decimal("44")
