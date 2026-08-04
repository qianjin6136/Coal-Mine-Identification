from pathlib import Path

import pytest

import sensor_logger.cli as cli
from sensor_logger.modbus import crc16


def response_frame(slave_id: int) -> bytes:
    registers = [0, slave_id, 0, 0, 0, 1]
    data = b"".join(value.to_bytes(2, "big") for value in registers)
    payload = bytes((slave_id, 3, len(data))) + data
    return payload + crc16(payload).to_bytes(2, "little")


class WorkingSerial:
    def __init__(self) -> None:
        self.current = b""
        self.closed = False

    def reset_input_buffer(self) -> None:
        self.current = b""

    def write(self, request: bytes) -> int:
        self.current = response_frame(request[0])
        return len(request)

    def read(self, size: int) -> bytes:
        result, self.current = self.current[:size], self.current[size:]
        return result

    def close(self) -> None:
        self.closed = True


class WorkingCamera:
    instances = []

    def __init__(self) -> None:
        self.closed = False
        self.__class__.instances.append(self)

    def read_frame(self):
        return [20.0 + index / 100 for index in range(768)]

    def close(self) -> None:
        self.closed = True


class FailingCloseCamera(WorkingCamera):
    def close(self) -> None:
        super().close()
        raise OSError("I2C 关闭失败")


class WorkingUsbCamera:
    instances = []

    def __init__(self, **_kwargs) -> None:
        self.closed = False
        self.__class__.instances.append(self)

    def capture_jpegs(self, count=3):
        return tuple(b"\xff\xd8\xfftest" for _ in range(count))

    def close(self) -> None:
        self.closed = True


def test_cli_defaults_match_hardware_design() -> None:
    args = cli.build_parser().parse_args([])

    assert args.interval == 5.0
    assert args.camera_interval == 2.0
    assert args.serial_port == "/dev/serial0"
    assert args.data_dir == Path("data")
    assert args.camera_device == 0
    assert args.camera_width == 1280
    assert args.camera_height == 720
    assert args.camera_fps == 30.0
    assert args.camera_quality == 95
    assert args.camera_id == "raspberry_pi_usb"
    assert args.once is False


def test_once_flag_selects_single_capture() -> None:
    assert cli.build_parser().parse_args(["--once"]).once is True


def test_rejects_non_positive_interval() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--interval", "0"])


def test_once_mode_creates_csv_and_png_then_closes_hardware(
    tmp_path, monkeypatch
) -> None:
    serial_port = WorkingSerial()
    WorkingCamera.instances.clear()
    WorkingUsbCamera.instances.clear()
    monkeypatch.setattr(cli, "_open_serial", lambda _port: serial_port)
    monkeypatch.setattr(cli, "Mlx90640Camera", WorkingCamera)
    monkeypatch.setattr(cli, "UsbCamera", WorkingUsbCamera)
    monkeypatch.chdir(tmp_path)

    result = cli.main(
        [
            "--once",
            "--station-id",
            "08",
            "--data-dir",
            str(tmp_path / "data"),
        ]
    )

    assert result == 0
    assert len(list((tmp_path / "data" / "gas").glob("*.csv"))) == 1
    assert len(list((tmp_path / "data" / "thermal").glob("*.png"))) == 1
    packages = list((tmp_path / "data" / "visible").glob("*/*"))
    assert len(packages) == 1
    assert len(list(packages[0].glob("frame_*.jpg"))) == 3
    assert (packages[0] / "metadata.json").is_file()
    assert serial_port.closed is True
    assert WorkingCamera.instances[0].closed is True
    assert WorkingUsbCamera.instances[0].closed is True
    assert (tmp_path / "logs" / "sensor_logger.log").exists()


def test_keyboard_interrupt_returns_130_and_closes_hardware(
    tmp_path, monkeypatch
) -> None:
    serial_port = WorkingSerial()
    WorkingCamera.instances.clear()
    WorkingUsbCamera.instances.clear()
    monkeypatch.setattr(cli, "_open_serial", lambda _port: serial_port)
    monkeypatch.setattr(cli, "Mlx90640Camera", WorkingCamera)
    monkeypatch.setattr(cli, "UsbCamera", WorkingUsbCamera)
    monkeypatch.setattr(
        cli,
        "run_periodic",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.chdir(tmp_path)

    result = cli.main(
        ["--station-id", "08", "--data-dir", str(tmp_path / "data")]
    )

    assert result == 130
    assert serial_port.closed is True
    assert WorkingCamera.instances[0].closed is True


def test_camera_close_failure_does_not_prevent_serial_close(
    tmp_path, monkeypatch
) -> None:
    serial_port = WorkingSerial()
    FailingCloseCamera.instances.clear()
    WorkingUsbCamera.instances.clear()
    monkeypatch.setattr(cli, "_open_serial", lambda _port: serial_port)
    monkeypatch.setattr(cli, "Mlx90640Camera", FailingCloseCamera)
    monkeypatch.setattr(cli, "UsbCamera", WorkingUsbCamera)
    monkeypatch.chdir(tmp_path)

    result = cli.main(
        [
            "--once",
            "--station-id",
            "08",
            "--data-dir",
            str(tmp_path / "data"),
        ]
    )

    assert result == 0
    assert FailingCloseCamera.instances[0].closed is True
    assert serial_port.closed is True


def test_main_requires_station_id(monkeypatch) -> None:
    monkeypatch.delenv("SENSOR_LOGGER_STATION_ID", raising=False)
    with pytest.raises(SystemExit):
        cli.main(["--once"])


def test_camera_argument_validation() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--camera-quality", "101"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--camera-width", "0"])
