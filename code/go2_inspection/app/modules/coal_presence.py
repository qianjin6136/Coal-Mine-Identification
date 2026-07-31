"""煤堆有/无模块，与工具、标牌和仪表结果完全解耦。"""

from __future__ import annotations

from typing import Any, Mapping

from .base import ModuleContext


class CoalPresenceModule:
    module_id = "coal_presence"

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)

    def run(self, context: ModuleContext) -> dict[str, Any]:
        if not self.config.get("enabled", True):
            return {"enabled": False, "status": "disabled", "present": None}
        if not context.detector_configured:
            # 未加载煤堆检测模型时不能把“没有检测框”解释成“没有煤堆”。
            return {
                "enabled": True,
                "status": "unavailable",
                "present": None,
                "reason": "coal_detector_not_configured",
                "detections": [],
            }
        detections = [
            item for item in context.objects if item.get("type") == "coal_pile"
        ]
        return {
            "enabled": True,
            "status": "confirmed",
            "present": bool(detections),
            "detections": detections,
        }
