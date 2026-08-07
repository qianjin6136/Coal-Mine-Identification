"""指针表模块：检出表盘并保留证据，本阶段不读数。"""

from __future__ import annotations

from typing import Any, Mapping

from .base import ModuleContext
from .field_cv import detect_analog_meters


class AnalogMeterModule:
    module_id = "analog_meter"

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)

    def run(self, context: ModuleContext) -> dict[str, Any]:
        if not self.config.get("enabled", False):
            return {
                "enabled": False,
                "status": "disabled",
                "reason": self.config.get(
                    "reason",
                    "normal and abnormal references are not available",
                ),
            }

        meters = [
            item for item in context.objects if item.get("type") == "analog_meter"
        ]
        if not meters and self.config.get("allow_image_fallback", True):
            for path in context.image_paths:
                meters.extend(detect_analog_meters(path))

        meters = _dedupe(meters)
        if not meters:
            return {
                "enabled": True,
                "status": "unavailable",
                "meters": [],
                "reason": "analog_meter_not_detected",
            }
        return {
            "enabled": True,
            "status": "confirmed",
            "meters": meters,
            "count": len(meters),
            "mode": "detect_only",
            "confidence": max(
                (float(item.get("confidence") or 0.0) for item in meters),
                default=0.0,
            ),
        }


def _dedupe(meters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for item in sorted(
        meters,
        key=lambda value: float(value.get("confidence") or 0.0),
        reverse=True,
    ):
        bbox = item.get("bbox_xyxy")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            kept.append(item)
            continue
        overlap = False
        for other in kept:
            other_bbox = other.get("bbox_xyxy")
            if (
                isinstance(other_bbox, (list, tuple))
                and len(other_bbox) == 4
                and _iou(bbox, other_bbox) > 0.4
            ):
                overlap = True
                break
        if not overlap:
            kept.append(item)
    return kept


def _iou(first: object, second: object) -> float:
    ax1, ay1, ax2, ay2 = [float(v) for v in first]  # type: ignore[arg-type]
    bx1, by1, bx2, by2 = [float(v) for v in second]  # type: ignore[arg-type]
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
