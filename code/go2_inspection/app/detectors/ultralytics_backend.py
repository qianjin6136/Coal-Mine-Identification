"""把可选的 Ultralytics YOLO 模型输出适配为应用领域对象。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from ..domain import BoundingBox, CaptureMetadata, Detection
from ..errors import ConfigurationError


class UltralyticsDetector:
    """加载一次 YOLO 权重，并按类别配置转换每张图的预测结果。"""

    name = "ultralytics"
    configured = True

    def __init__(
        self,
        weights: Path,
        class_config: Mapping[str, Any],
        confidence: float = 0.35,
        config_dir: Path | None = None,
    ) -> None:
        if not weights.exists():
            raise ConfigurationError(f"detector weights not found: {weights}")
        if config_dir is not None:
            config_dir.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("YOLO_CONFIG_DIR", str(config_dir))
        try:
            # Ultralytics 体积较大且为可选依赖，仅在选择该后端时才导入。
            from ultralytics import YOLO
        except ImportError as exc:
            raise ConfigurationError(
                "ultralytics is not installed; install the 'vision' dependencies"
            ) from exc
        self.model = YOLO(str(weights))
        self.class_config = class_config
        self.confidence = confidence

    def detect(
        self, image_path: Path, metadata: CaptureMetadata, frame_index: int
    ) -> list[Detection]:
        predictions = self.model.predict(
            source=str(image_path),
            conf=self.confidence,
            verbose=False,
        )
        detections: list[Detection] = []
        if not predictions:
            return detections
        result = predictions[0]
        names = result.names
        for box in result.boxes:
            class_index = int(box.cls[0].item())
            model_name = str(names[class_index])
            # 配置文件把训练标签映射为稳定的业务类型、编号和中文名称。
            config = self.class_config.get(model_name, {})
            class_id = str(config.get("id", model_name))
            confidence = float(box.conf[0].item())
            attributes = dict(config.get("attributes", {}))
            minimum = config.get("min_confidence")
            if (
                str(config.get("type", model_name.split("_", 1)[0])) == "tool"
                and minimum is not None
                and confidence < float(minimum)
            ):
                attributes.update(
                    {
                        "predicted_class": class_id,
                        "predicted_class_cn": str(
                            config.get("name_cn", class_id)
                        ),
                    }
                )
                class_id = "unknown_tool"
                class_cn = "未知工具"
            else:
                class_cn = str(config.get("name_cn", class_id))
            detections.append(
                Detection(
                    type=str(config.get("type", model_name.split("_", 1)[0])),
                    class_id=class_id,
                    class_cn=class_cn,
                    bbox=BoundingBox.from_sequence(box.xyxy[0].tolist()),
                    confidence=confidence,
                    attributes=attributes,
                    source_frame=frame_index,
                )
            )
        return detections
