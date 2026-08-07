"""无 Ultralytics 时的多类现场启发式检测后端。"""

from __future__ import annotations

from pathlib import Path

from ..domain import BoundingBox, CaptureMetadata, Detection
from ..modules.field_cv import (
    detect_analog_meters,
    detect_coal_piles,
    detect_foreign_objects,
    detect_indicator_lights,
    detect_station_markers,
)


class FieldCvDetector:
    """把 OpenCV 启发式结果适配为 DetectionBackend。"""

    name = "field_cv"
    runtime_mode = "field_cv"

    def __init__(self, confidence: float = 0.35) -> None:
        self.confidence = confidence
        self.configured = True

    def detect(
        self, image_path: Path, metadata: CaptureMetadata, frame_index: int
    ) -> list[Detection]:
        del metadata
        detections: list[Detection] = []
        for item in detect_station_markers(image_path):
            detections.append(self._to_detection(item, frame_index))
        for item in detect_coal_piles(image_path):
            detections.append(self._to_detection(item, frame_index))
        for item in detect_foreign_objects(image_path):
            detections.append(self._to_detection(item, frame_index))
        for item in detect_analog_meters(image_path):
            detections.append(self._to_detection(item, frame_index))
        indicators = detect_indicator_lights(image_path)
        for color in ("red", "green"):
            color_result = indicators.get(color) or {}
            for item in color_result.get("detections") or []:
                detections.append(self._to_detection(item, frame_index))
        # digital_meter boxes are handled by DigitalMeterModule full-frame OCR.
        return [
            item
            for item in detections
            if item.confidence >= self.confidence
        ]

    def _to_detection(self, item: dict, frame_index: int) -> Detection:
        bbox = item.get("bbox_xyxy") or [0, 0, 0, 0]
        class_id = str(item.get("class") or item.get("type") or "unknown")
        return Detection(
            type=str(item.get("type") or "unknown"),
            class_id=class_id,
            class_cn=str(item.get("class_cn") or class_id),
            confidence=float(item.get("confidence") or 0.0),
            bbox=BoundingBox(
                float(bbox[0]),
                float(bbox[1]),
                float(bbox[2]),
                float(bbox[3]),
            ),
            source_frame=frame_index,
            attributes={},
        )
