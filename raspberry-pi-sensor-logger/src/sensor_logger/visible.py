"""USB 可见光相机三连拍及上位机兼容抓拍包存储。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class VisibleCaptureResult:
    """一次可见光三连拍的保存结果。"""

    capture_id: str
    timestamp: datetime
    package_path: Path | None
    image_paths: tuple[Path, ...] = ()
    errors: tuple[str, ...] = ()


class UsbCamera:
    """通过 OpenCV/V4L2 读取普通 USB UVC 相机并编码为 JPEG。"""

    def __init__(
        self,
        device: int | str = 0,
        width: int = 1280,
        height: int = 720,
        fps: float = 30.0,
        quality: int = 95,
    ) -> None:
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.quality = quality
        self._capture: Any | None = None
        self._cv2: Any | None = None

    def _open(self) -> Any:
        if self._capture is not None and self._capture.isOpened():
            return self._capture

        import cv2

        backend = getattr(cv2, "CAP_V4L2", 0)
        capture = cv2.VideoCapture(self.device, backend)
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        capture.set(cv2.CAP_PROP_FPS, self.fps)
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not capture.isOpened():
            capture.release()
            raise OSError(f"无法打开 USB 相机：{self.device}")

        # 首帧用于确认设备已经开始输出，同时让自动曝光有一次更新机会。
        ok, frame = capture.read()
        if not ok or frame is None:
            capture.release()
            raise OSError(f"USB 相机首帧读取失败：{self.device}")

        self._cv2 = cv2
        self._capture = capture
        return capture

    def capture_jpegs(self, count: int = 3) -> tuple[bytes, ...]:
        """连续读取并编码指定数量的 JPEG；任一帧失败则整组失败。"""

        if count <= 0:
            raise ValueError("拍摄张数必须大于 0")
        capture = self._open()
        cv2 = self._cv2
        assert cv2 is not None
        encoded_frames: list[bytes] = []
        try:
            for index in range(count):
                ok, frame = capture.read()
                if not ok or frame is None:
                    raise OSError(f"USB 相机第 {index + 1} 帧读取失败")
                encoded, buffer = cv2.imencode(
                    ".jpg",
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, self.quality],
                )
                if not encoded:
                    raise OSError(f"USB 相机第 {index + 1} 帧 JPEG 编码失败")
                encoded_frames.append(buffer.tobytes())
        except Exception:
            self.close()
            raise
        return tuple(encoded_frames)

    def close(self) -> None:
        capture, self._capture = self._capture, None
        self._cv2 = None
        if capture is not None:
            capture.release()


class VisiblePackageWriter:
    """把三张 JPEG 原子发布为上位机可直接导入的抓拍包。"""

    FRAME_NAMES = ("frame_01.jpg", "frame_02.jpg", "frame_03.jpg")

    def __init__(self, root: Path, station_id: str, camera_id: str) -> None:
        self.directory = Path(root) / "visible"
        self.station_id = station_id
        self.camera_id = camera_id

    def write(
        self,
        jpeg_frames: tuple[bytes, ...],
        timestamp: datetime,
    ) -> VisibleCaptureResult:
        if len(jpeg_frames) != 3:
            raise ValueError("每个抓拍包必须正好包含三张照片")
        if any(not frame for frame in jpeg_frames):
            raise ValueError("抓拍包不能包含空照片")

        date_directory = self.directory / f"{timestamp:%Y-%m-%d}"
        date_directory.mkdir(parents=True, exist_ok=True)
        base_capture_id = f"rpi_{timestamp:%Y%m%d_%H%M%S_%f}"
        capture_id = base_capture_id
        suffix = 1
        while (date_directory / capture_id).exists():
            capture_id = f"{base_capture_id}_{suffix:02d}"
            suffix += 1

        destination = date_directory / capture_id
        temporary = date_directory / f".{capture_id}.{uuid4().hex}.tmp"
        image_paths: list[Path] = []
        try:
            temporary.mkdir()
            for name, payload in zip(self.FRAME_NAMES, jpeg_frames):
                path = temporary / name
                with path.open("xb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                image_paths.append(destination / name)

            metadata = {
                "capture_id": capture_id,
                "capture_time": timestamp.isoformat(),
                "station_id": self.station_id,
                "robot_pose": {
                    "frame": "map",
                    "x_m": None,
                    "y_m": None,
                    "yaw_deg": None,
                },
                "camera_id": self.camera_id,
                "images": list(self.FRAME_NAMES),
                "batch_id": None,
            }
            metadata_path = temporary / "metadata.json"
            with metadata_path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(metadata, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())

            temporary.replace(destination)
            return VisibleCaptureResult(
                capture_id=capture_id,
                timestamp=timestamp,
                package_path=destination,
                image_paths=tuple(image_paths),
            )
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)
            raise


class VisibleCameraLogger:
    """协调 USB 相机和抓拍包写入，单组失败不会终止周期任务。"""

    def __init__(self, camera: UsbCamera, writer: VisiblePackageWriter) -> None:
        self.camera = camera
        self.writer = writer

    def capture(self, timestamp: datetime) -> VisibleCaptureResult:
        try:
            frames = self.camera.capture_jpegs(3)
            return self.writer.write(frames, timestamp)
        except Exception as error:
            return VisibleCaptureResult(
                capture_id=f"rpi_{timestamp:%Y%m%d_%H%M%S_%f}",
                timestamp=timestamp,
                package_path=None,
                errors=(f"可见光三连拍失败：{error}",),
            )

    def close(self) -> None:
        self.camera.close()
