"""皮带机彩布/异物检测模块。"""

from __future__ import annotations

from typing import Any, Mapping

from .base import ModuleContext
from .field_cv import detect_foreign_objects


class ForeignObjectModule:
    module_id = "foreign_object"

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)

    def run(self, context: ModuleContext) -> dict[str, Any]:
        if not self.config.get("enabled", True):
            return {"enabled": False, "status": "disabled", "present": None}
        if not self.config.get("model_ready", False):
            return {
                "enabled": True,
                "status": "unavailable",
                "present": None,
                "reason": str(
                    self.config.get("reason") or "foreign_object_model_not_ready"
                ),
                "detections": [],
            }

        detections = [
            item for item in context.objects if item.get("type") == "foreign_object"
        ]
        if not detections and self.config.get("allow_image_fallback", True):
            for path in context.image_paths:
                detections.extend(detect_foreign_objects(path))

        # Deduplicate roughly overlapping boxes by keeping highest confidence.
        detections = _dedupe(detections)
        return {
            "enabled": True,
            "status": "confirmed",
            "present": bool(detections),
            "detections": detections,
            "confidence": max(
                (float(item.get("confidence") or 0.0) for item in detections),
                default=0.0,
            ),
        }


def _dedupe(detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for item in sorted(
        detections,
        key=lambda value: float(value.get("confidence") or 0.0),
        reverse=True,
    ):
        bbox = item.get("bbox_xyxy")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            kept.append(item)
            continue
        if any(_iou(bbox, other.get("bbox_xyxy")) > 0.45 for other in kept):
            continue
        kept.append(item)
    return kept


def _iou(first: object, second: object) -> float:
    if (
        not isinstance(first, (list, tuple))
        or not isinstance(second, (list, tuple))
        or len(first) != 4
        or len(second) != 4
    ):
        return 0.0
    ax1, ay1, ax2, ay2 = [float(v) for v in first]
    bx1, by1, bx2, by2 = [float(v) for v in second]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0
