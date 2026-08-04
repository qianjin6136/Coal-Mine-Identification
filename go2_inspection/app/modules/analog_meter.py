"""指针表模块；参考数据到位前不参与主流程。"""

from __future__ import annotations

from typing import Any, Mapping

from .base import ModuleContext


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
        objects = [
            item for item in context.objects if item.get("type") == "analog_meter"
        ]
        return {
            "enabled": True,
            "status": "confirmed" if objects else "unavailable",
            "meters": objects,
        }
