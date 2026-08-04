import pytest

from sensor_logger.modbus import (
    ModbusError,
    build_read_request,
    crc16,
    parse_read_response,
)


def test_crc_matches_documented_request() -> None:
    payload = bytes.fromhex("01 03 00 01 00 01")

    assert crc16(payload) == 0xCAD5


def test_builds_documented_id_1_concentration_request() -> None:
    assert build_read_request(1, 1, 1) == bytes.fromhex(
        "01 03 00 01 00 01 D5 CA"
    )


@pytest.mark.parametrize(
    ("slave_id", "start_register", "register_count"),
    [(0, 0, 1), (248, 0, 1), (1, -1, 1), (1, 0, 0), (1, 0, 126)],
)
def test_rejects_invalid_read_request(
    slave_id: int, start_register: int, register_count: int
) -> None:
    with pytest.raises(ValueError, match="无效"):
        build_read_request(slave_id, start_register, register_count)


def test_parses_documented_oxygen_response() -> None:
    assert parse_read_response(
        bytes.fromhex("02 03 02 00 D1 3C 18"), slave_id=2, register_count=1
    ) == [209]


def test_parses_multiple_registers() -> None:
    payload = bytes.fromhex("01 03 04 12 34 AB CD")
    frame = payload + crc16(payload).to_bytes(2, "little")

    assert parse_read_response(frame, slave_id=1, register_count=2) == [
        0x1234,
        0xABCD,
    ]


def test_rejects_bad_crc() -> None:
    with pytest.raises(ModbusError, match="CRC"):
        parse_read_response(
            bytes.fromhex("02 03 02 00 D1 00 00"), slave_id=2, register_count=1
        )


def test_rejects_wrong_slave_address() -> None:
    payload = bytes.fromhex("03 03 02 00 D1")
    frame = payload + crc16(payload).to_bytes(2, "little")

    with pytest.raises(ModbusError, match="地址"):
        parse_read_response(frame, slave_id=2, register_count=1)


def test_rejects_modbus_exception_response() -> None:
    payload = bytes.fromhex("02 83 02")
    frame = payload + crc16(payload).to_bytes(2, "little")

    with pytest.raises(ModbusError, match="异常响应.*2"):
        parse_read_response(frame, slave_id=2, register_count=1)


def test_rejects_truncated_frame() -> None:
    with pytest.raises(ModbusError, match="长度"):
        parse_read_response(b"\x02\x03", slave_id=2, register_count=1)
