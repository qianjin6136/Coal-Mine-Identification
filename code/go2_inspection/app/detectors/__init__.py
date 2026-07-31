"""可替换的目标检测后端。"""

from .base import DetectionBackend
from .noop import NoopDetector

__all__ = ["DetectionBackend", "NoopDetector"]
