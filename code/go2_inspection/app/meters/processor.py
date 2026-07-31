"""对检测出的仪表矩形框执行 ROI 读数和指针角度提取。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from ..domain import CaptureMetadata, Detection
from ..image_io import read_bgr_image
from .analog import detect_red_pointer_angle_image
from .digital import SevenSegmentReader


class MeterProcessor:
    """将图像级仪表算法适配到统一检测流水线。"""

    def __init__(
        self,
        analog_references: Mapping[str, Any] | None = None,
        digital_activation_threshold: float = 0.32,
    ) -> None:
        self.analog_references = analog_references or {}
        self.digital_reader = SevenSegmentReader(digital_activation_threshold)

    def enrich_frame(
        self,
        detections: Sequence[Detection],
        image_path: Path,
        metadata: CaptureMetadata,
    ) -> list[str]:
        """为单帧仪表检测写入私有测量属性，返回非致命警告。"""

        meter_detections = [
            item
            for item in detections
            if item.type in {"analog_meter", "digital_meter"}
        ]
        if not meter_detections:
            return []
        try:
            import cv2
        except ImportError:
            return ["meter_processing_skipped: OpenCV is not installed"]
        image = read_bgr_image(image_path)
        if image is None:
            return [f"meter_processing_skipped: unreadable image {image_path.name}"]
        height, width = image.shape[:2]
        warnings: list[str] = []
        for detection in meter_detections:
            x1 = max(0, min(width, int(detection.bbox.x1)))
            y1 = max(0, min(height, int(detection.bbox.y1)))
            x2 = max(0, min(width, int(detection.bbox.x2)))
            y2 = max(0, min(height, int(detection.bbox.y2)))
            if x2 <= x1 or y2 <= y1:
                warnings.append(
                    f"meter_processing_skipped: invalid ROI for {detection.class_id}"
                )
                continue
            roi = image[y1:y2, x1:x2]
            if detection.type == "digital_meter":
                detection.attributes["_digital_text"] = self.digital_reader.read_image(
                    roi
                )
                continue
            reference = self._analog_reference(
                metadata.station_id,
                detection.class_id,
            )
            if reference is None:
                detection.attributes["_pointer_angle_deg"] = None
                detection.attributes["_pointer_confidence"] = 0.0
                warnings.append(
                    f"analog_reference_missing: station={metadata.station_id} class={detection.class_id}"
                )
                continue
            center = reference.get("center_xy")
            center_xy = (
                (float(center[0]), float(center[1]))
                if isinstance(center, (list, tuple)) and len(center) == 2
                else None
            )
            angle, confidence = detect_red_pointer_angle_image(roi, center_xy)
            detection.attributes["_pointer_angle_deg"] = angle
            detection.attributes["_pointer_confidence"] = confidence
            detection.attributes["_analog_reference"] = dict(reference)
        return warnings

    def _analog_reference(
        self,
        station_id: str,
        class_id: str,
    ) -> Mapping[str, Any] | None:
        """优先匹配工位和具体仪表类，单仪表工位可只配置 station_id。"""

        fallback: Mapping[str, Any] | None = None
        for reference in self.analog_references.values():
            if not isinstance(reference, Mapping):
                continue
            if str(reference.get("station_id", "")) != station_id:
                continue
            configured_class = reference.get("class")
            if configured_class is None:
                fallback = reference
            elif str(configured_class) == class_id:
                return reference
        return fallback
