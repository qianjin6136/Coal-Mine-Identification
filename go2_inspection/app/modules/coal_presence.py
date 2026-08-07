"""煤堆有/无模块；支持检测框与图像启发式兜底。"""

from __future__ import annotations

from typing import Any, Mapping

from .base import ModuleContext
from .field_cv import detect_coal_piles


class CoalPresenceModule:
    module_id = "coal_presence"

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
                    self.config.get("reason") or "coal_field_model_not_trained"
                ),
                "detections": [],
            }

        detections = [
            item for item in context.objects if item.get("type") == "coal_pile"
        ]
        used_fallback = False
        if (
            not detections
            and self.config.get("allow_image_fallback", True)
        ):
            for path in context.image_paths:
                detections.extend(detect_coal_piles(path))
            used_fallback = bool(detections)

        if (
            not detections
            and not context.detector_configured
            and not self.config.get("allow_image_fallback", True)
        ):
            return {
                "enabled": True,
                "status": "unavailable",
                "present": None,
                "reason": "coal_detector_not_configured",
                "detections": [],
            }

        return {
            "enabled": True,
            "status": "confirmed",
            "present": bool(detections),
            "detections": detections,
            "source": "image_fallback" if used_fallback else "detector",
            "confidence": max(
                (float(item.get("confidence") or 0.0) for item in detections),
                default=0.0,
            ),
        }
