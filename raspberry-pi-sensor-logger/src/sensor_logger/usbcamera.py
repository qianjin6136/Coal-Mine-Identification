"""USB 可见光相机单张彩色抓拍与扁平目录存储。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class VisibleCaptureResult:
    """一次 USB 相机抓拍的保存结果。"""

    capture_id: str
    timestamp: datetime
    package_path: Path | None
    image_paths: tuple[Path, ...] = ()
    errors: tuple[str, ...] = ()


class UsbCamera:
    """通过 OpenCV/V4L2 读取普通 USB UVC 相机。"""

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

        # 丢弃刚打开设备时的旧帧，让自动曝光先更新一次。
        ok, frame = capture.read()
        if not ok or frame is None:
            capture.release()
            raise OSError(f"USB 相机首帧读取失败：{self.device}")

        self._cv2 = cv2
        self._capture = capture
        return capture

    def open(self) -> None:
        """提前打开设备，用于启动阶段的硬件完整性检查。"""

        self._open()

    def capture_color_jpeg(self) -> bytes:
        """读取一帧并编码为彩色 JPEG。"""

        capture = self._open()
        cv2 = self._cv2
        if cv2 is None:
            raise RuntimeError("USB 相机尚未完成初始化")

        try:
            ok, frame = capture.read()
            if not ok or frame is None:
                raise OSError("USB 相机彩色帧读取失败")
            encoded, buffer = cv2.imencode(
                ".jpg",
                frame,
                [cv2.IMWRITE_JPEG_QUALITY, self.quality],
            )
            if not encoded:
                raise OSError("USB 相机 JPEG 编码失败")
            return buffer.tobytes()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        capture, self._capture = self._capture, None
        self._cv2 = None
        if capture is not None:
            capture.release()


class VisiblePackageWriter:
    """把所有彩色照片直接保存到同一个 data/visible 目录。"""

    def __init__(self, root: Path, station_id: str, camera_id: str) -> None:
        # station_id 和 camera_id 保留在参数中，以兼容现有 cli.py 的调用。
        self.directory = Path(root) / "visible"
        self.station_id = station_id
        self.camera_id = camera_id

    def write(
        self,
        color_jpeg: bytes,
        timestamp: datetime,
    ) -> VisibleCaptureResult:
        if not color_jpeg:
            raise ValueError("彩色照片数据不能为空")

        self.directory.mkdir(parents=True, exist_ok=True)
        capture_id = f"color_{timestamp:%Y%m%d_%H%M%S_%f}"
        final_path = self.directory / f"{capture_id}.jpg"

        # 微秒时间戳通常不会重复；仍保留冲突保护，避免覆盖已有照片。
        suffix = 1
        while final_path.exists():
            final_path = self.directory / f"{capture_id}_{suffix:02d}.jpg"
            suffix += 1

        temporary_path = self.directory / f".{final_path.name}.{uuid4().hex}.tmp"
        try:
            with temporary_path.open("xb") as stream:
                stream.write(color_jpeg)
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(final_path)
        finally:
            temporary_path.unlink(missing_ok=True)

        return VisibleCaptureResult(
            capture_id=final_path.stem,
            timestamp=timestamp,
            package_path=final_path,
            image_paths=(final_path,),
        )


class VisibleCameraLogger:
    """协调 USB 相机和照片写入；抓拍失败不终止其他传感器。"""

    def __init__(self, camera: UsbCamera, writer: VisiblePackageWriter) -> None:
        self.camera = camera
        self.writer = writer

    def capture(self, timestamp: datetime) -> VisibleCaptureResult:
        try:
            color_jpeg = self.camera.capture_color_jpeg()
            return self.writer.write(color_jpeg, timestamp)
        except Exception as error:
            return VisibleCaptureResult(
                capture_id=f"color_{timestamp:%Y%m%d_%H%M%S_%f}",
                timestamp=timestamp,
                package_path=None,
                errors=(f"USB 彩色相机抓拍失败：{error}",),
            )

    def close(self) -> None:
        self.camera.close()
