"""兼容 Windows Unicode 路径的 OpenCV 图像读写辅助。"""

from __future__ import annotations

from pathlib import Path


def read_bgr_image(path: str | Path) -> object | None:
    """使用 ``imdecode`` 读取图片，避免 ``cv2.imread`` 无法处理中文路径。"""

    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("OpenCV and NumPy are required for image analysis") from exc
    try:
        payload = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if payload.size == 0:
        return None
    return cv2.imdecode(payload, cv2.IMREAD_COLOR)


def write_bgr_image(path: str | Path, image: object) -> bool:
    """使用 ``imencode + tofile`` 保存图片，支持中文目录。"""

    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required for image output") from exc
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    extension = destination.suffix.lower() or ".png"
    success, payload = cv2.imencode(extension, image)
    if not success:
        return False
    try:
        payload.tofile(str(destination))
    except OSError:
        return False
    return True
