"""从图片旁的 JSON 文件回放检测结果，供模型训练完成前联调流水线。

例如 ``frame_01.jpg`` 对应 ``frame_01.jpg.detections.json``。这样无需模型
权重，也能验证 API、数据库、跨帧融合和结果输出。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..domain import BoundingBox, CaptureMetadata, Detection


class JsonReplayDetector:
    """读取约定格式的伴随 JSON，并转换为统一 Detection 对象。"""

    name = "json_replay"
    runtime_mode = "json_replay"
    configured = True

    def detect(
        self, image_path: Path, metadata: CaptureMetadata, frame_index: int
    ) -> list[Detection]:
        # 保留原图片扩展名再追加后缀，避免 JPG/PNG 同名时伴随文件冲突。
        sidecar = image_path.with_name(image_path.name + ".detections.json")
        if not sidecar.exists():
            return []
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
        detections: list[Detection] = []
        for item in raw:
            detections.append(
                Detection(
                    type=str(item["type"]),
                    class_id=str(item["class"]),
                    class_cn=str(item.get("class_cn", item["class"])),
                    bbox=BoundingBox.from_sequence(item["bbox_xyxy"]),
                    confidence=float(item["confidence"]),
                    attributes=dict(item.get("attributes", {})),
                    source_frame=frame_index,
                )
            )
        return detections
