"""工具和安全标识牌模块；类别未冻结前明确禁用。"""

from __future__ import annotations

from typing import Any, Mapping

from .base import ModuleContext


class ToolAndSafetySignModule:
    module_id = "tool_and_safety_sign"

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)

    def run(self, context: ModuleContext) -> dict[str, Any]:
        if not self.config.get("enabled", False):
            return {
                "enabled": False,
                "status": "disabled",
                "reason": self.config.get(
                    "reason",
                    "final class list is not frozen",
                ),
                "objects": [],
            }
        objects = [
            item
            for item in context.objects
            if item.get("type") in {"tool", "safety_sign"}
        ]
        return {
            "enabled": True,
            "status": "confirmed" if context.detector_configured else "unavailable",
            "objects": objects,
        }
