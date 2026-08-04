import json
from datetime import datetime, timedelta, timezone

import pytest

from sensor_logger.usbcamera import VisibleCameraLogger, VisiblePackageWriter


NOW = datetime(2026, 8, 4, 15, 30, 5, 123456, tzinfo=timezone(timedelta(hours=8)))
JPEG = b"\xff\xd8\xffsynthetic"


class WorkingCamera:
    def capture_jpegs(self, count=3):
        return tuple(JPEG for _ in range(count))


class BrokenCamera:
    def capture_jpegs(self, count=3):
        raise OSError("设备已断开")


class RecoveringCamera:
    def __init__(self) -> None:
        self.calls = 0

    def capture_jpegs(self, count=3):
        self.calls += 1
        if self.calls == 1:
            raise OSError("临时断开")
        return tuple(JPEG for _ in range(count))


def test_writer_publishes_three_frame_upper_machine_package(tmp_path) -> None:
    writer = VisiblePackageWriter(tmp_path, "08", "raspberry_pi_usb")

    result = writer.write((JPEG, JPEG, JPEG), NOW)

    assert result.package_path is not None
    assert result.package_path.parent.name == "2026-08-04"
    assert [path.name for path in result.image_paths] == [
        "frame_01.jpg",
        "frame_02.jpg",
        "frame_03.jpg",
    ]
    assert all(path.read_bytes() == JPEG for path in result.image_paths)
    metadata = json.loads(
        (result.package_path / "metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["capture_id"] == result.capture_id
    assert metadata["capture_time"] == NOW.isoformat()
    assert metadata["station_id"] == "08"
    assert metadata["camera_id"] == "raspberry_pi_usb"
    assert metadata["images"] == [
        "frame_01.jpg",
        "frame_02.jpg",
        "frame_03.jpg",
    ]
    assert metadata["robot_pose"] == {
        "frame": "map",
        "x_m": None,
        "y_m": None,
        "yaw_deg": None,
    }
    assert not list((tmp_path / "visible").rglob("*.tmp"))


def test_writer_rejects_incomplete_burst_without_publishing(tmp_path) -> None:
    writer = VisiblePackageWriter(tmp_path, "08", "raspberry_pi_usb")

    with pytest.raises(ValueError, match="三张"):
        writer.write((JPEG, JPEG), NOW)

    assert not list((tmp_path / "visible").rglob("metadata.json"))


def test_camera_failure_is_returned_without_package(tmp_path) -> None:
    logger = VisibleCameraLogger(
        BrokenCamera(), VisiblePackageWriter(tmp_path, "08", "raspberry_pi_usb")
    )

    result = logger.capture(NOW)

    assert result.package_path is None
    assert result.errors == ("可见光三连拍失败：设备已断开",)
    assert not (tmp_path / "visible").exists()


def test_next_cycle_retries_after_camera_failure(tmp_path) -> None:
    camera = RecoveringCamera()
    logger = VisibleCameraLogger(
        camera, VisiblePackageWriter(tmp_path, "08", "raspberry_pi_usb")
    )

    failed = logger.capture(NOW)
    recovered = logger.capture(NOW.replace(microsecond=234567))

    assert failed.package_path is None
    assert recovered.package_path is not None
    assert camera.calls == 2
