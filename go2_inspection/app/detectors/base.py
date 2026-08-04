"""检测后端必须满足的结构化接口。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..domain import CaptureMetadata, Detection


class DetectionBackend(Protocol):
    """流水线依赖的检测器协议，便于替换模型或注入测试实现。"""

    name: str
    configured: bool

    def detect(
        self, image_path: Path, metadata: CaptureMetadata, frame_index: int
    ) -> list[Detection]:
        """返回单张图片中的全部检测目标。"""
