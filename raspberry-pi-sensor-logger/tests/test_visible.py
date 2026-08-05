from datetime import datetime, timedelta, timezone

import pytest

from sensor_logger.usbcamera import VisibleCameraLogger, VisiblePackageWriter


NOW = datetime(2026, 8, 4, 15, 30, 5, 123456, tzinfo=timezone(timedelta(hours=8)))
JPEG = b"\xff\xd8\xffsynthetic"


class WorkingCamera:
    def capture_color_jpeg(self):
        return JPEG


class BrokenCamera:
    def capture_color_jpeg(self):
        raise OSError("设备已断开")


class RecoveringCamera:
    def __init__(self) -> None:
        self.calls = 0

    def capture_color_jpeg(self):
        self.calls += 1
        if self.calls == 1:
            raise OSError("临时断开")
        return JPEG


def test_writer_publishes_one_flat_color_image_atomically(tmp_path) -> None:
    writer = VisiblePackageWriter(tmp_path, "08", "raspberry_pi_usb")

    result = writer.write(JPEG, NOW)

    assert result.package_path is not None
    assert result.package_path == (
        tmp_path / "visible" / "color_20260804_153005_123456.jpg"
    )
    assert result.image_paths == (result.package_path,)
    assert result.package_path.read_bytes() == JPEG
    assert not list((tmp_path / "visible").rglob("metadata.json"))
    assert not list((tmp_path / "visible").rglob("*.tmp"))


def test_writer_rejects_empty_image_without_publishing(tmp_path) -> None:
    writer = VisiblePackageWriter(tmp_path, "08", "raspberry_pi_usb")

    with pytest.raises(ValueError, match="不能为空"):
        writer.write(b"", NOW)

    assert not list((tmp_path / "visible").rglob("*.jpg"))


def test_writer_avoids_overwriting_same_timestamp(tmp_path) -> None:
    writer = VisiblePackageWriter(tmp_path, "", "raspberry_pi_usb")

    first = writer.write(JPEG, NOW)
    second = writer.write(JPEG + b"2", NOW)

    assert first.package_path != second.package_path
    assert second.package_path.name == "color_20260804_153005_123456_01.jpg"
    assert first.package_path.read_bytes() == JPEG
    assert second.package_path.read_bytes() == JPEG + b"2"


def test_camera_failure_is_returned_without_package(tmp_path) -> None:
    logger = VisibleCameraLogger(
        BrokenCamera(), VisiblePackageWriter(tmp_path, "08", "raspberry_pi_usb")
    )

    result = logger.capture(NOW)

    assert result.package_path is None
    assert result.errors == ("USB 彩色相机抓拍失败：设备已断开",)
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
