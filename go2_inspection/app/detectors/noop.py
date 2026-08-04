"""模型权重未就绪时使用的空检测后端。"""

from pathlib import Path

from ..domain import CaptureMetadata, Detection


class NoopDetector:
    """不产生目标，但允许上传、存储和 API 链路先行联调。"""

    name = "noop"
    runtime_mode = "noop"
    configured = False

    def detect(
        self, image_path: Path, metadata: CaptureMetadata, frame_index: int
    ) -> list[Detection]:
        return []
