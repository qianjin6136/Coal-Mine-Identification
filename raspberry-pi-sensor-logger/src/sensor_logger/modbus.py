"""4MZ-HH4 使用的最小 Modbus-RTU 读寄存器实现。"""


class ModbusError(ValueError):
    """表示收到的 Modbus 响应无效。"""


def crc16(data: bytes) -> int:
    """计算 Modbus-RTU CRC-16。"""

    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def build_read_request(
    slave_id: int, start_register: int, register_count: int
) -> bytes:
    """生成 0x03 读保持寄存器请求帧。"""

    last_register = start_register + register_count - 1
    if (
        not 1 <= slave_id <= 247
        or not 0 <= start_register <= 0xFFFF
        or not 1 <= register_count <= 125
        or last_register > 0xFFFF
    ):
        raise ValueError("无效的 Modbus 读取参数")

    payload = (
        bytes((slave_id, 0x03))
        + start_register.to_bytes(2, "big")
        + register_count.to_bytes(2, "big")
    )
    return payload + crc16(payload).to_bytes(2, "little")


def parse_read_response(
    frame: bytes, slave_id: int, register_count: int
) -> list[int]:
    """校验 0x03 响应帧并返回无符号 16 位寄存器。"""

    if len(frame) < 5:
        raise ModbusError(f"响应长度过短：{len(frame)}")

    expected_crc = crc16(frame[:-2]).to_bytes(2, "little")
    if frame[-2:] != expected_crc:
        raise ModbusError("CRC 校验失败")
    if frame[0] != slave_id:
        raise ModbusError(f"从机地址不匹配：期望 {slave_id}，收到 {frame[0]}")
    if frame[1] & 0x80:
        raise ModbusError(f"Modbus 异常响应，异常码 {frame[2]}")

    expected_length = 5 + register_count * 2
    if len(frame) != expected_length:
        raise ModbusError(
            f"响应长度不匹配：期望 {expected_length}，收到 {len(frame)}"
        )
    if frame[1] != 0x03:
        raise ModbusError(f"功能码不匹配：收到 0x{frame[1]:02X}")
    if frame[2] != register_count * 2:
        raise ModbusError(
            f"数据字节数不匹配：期望 {register_count * 2}，收到 {frame[2]}"
        )

    return [
        int.from_bytes(frame[index : index + 2], "big")
        for index in range(3, 3 + register_count * 2, 2)
    ]
