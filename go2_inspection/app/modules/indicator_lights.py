"""变电硐室红/绿指示灯识别模块。"""

from __future__ import annotations

from typing import Any, Mapping

from .base import ModuleContext
from .field_cv import detect_indicator_lights


class IndicatorLightsModule:
    module_id = "indicator_lights"

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)

    def run(self, context: ModuleContext) -> dict[str, Any]:
        if not self.config.get("enabled", True):
            return {"enabled": False, "status": "disabled"}
        if not self.config.get("model_ready", True):
            return {
                "enabled": True,
                "status": "unavailable",
                "reason": str(
                    self.config.get("reason") or "indicator_lights_not_ready"
                ),
                "red": {"on": None, "confidence": 0.0, "detections": []},
                "green": {"on": None, "confidence": 0.0, "detections": []},
            }

        frame_results = [detect_indicator_lights(path) for path in context.image_paths]
        if not frame_results:
            return {
                "enabled": True,
                "status": "unavailable",
                "reason": "no_frames",
                "red": {"on": None, "confidence": 0.0, "detections": []},
                "green": {"on": None, "confidence": 0.0, "detections": []},
            }

        red = _vote_color(frame_results, "red")
        green = _vote_color(frame_results, "green")
        status = "confirmed" if red["on"] is not None or green["on"] is not None else "unreadable"
        return {
            "enabled": True,
            "status": status,
            "red": red,
            "green": green,
            "frames": frame_results,
            "confidence": max(red["confidence"], green["confidence"]),
        }


def _vote_color(frames: list[dict[str, Any]], color: str) -> dict[str, Any]:
    on_votes = 0
    off_votes = 0
    confidences: list[float] = []
    detections: list[dict[str, Any]] = []
    for frame in frames:
        item = frame.get(color) if isinstance(frame.get(color), Mapping) else {}
        if item.get("on") is True:
            on_votes += 1
        else:
            off_votes += 1
        confidences.append(float(item.get("confidence") or 0.0))
        for detection in item.get("detections") or []:
            if isinstance(detection, Mapping):
                detections.append(dict(detection))
    on = on_votes >= max(1, (len(frames) + 1) // 2) and on_votes > 0
    # If every frame failed to see a lit lamp, report off with low confidence.
    if on_votes == 0 and off_votes == len(frames):
        on = False
    return {
        "on": on,
        "confidence": round(max(confidences) if confidences else 0.0, 4),
        "detections": detections[:4],
        "on_votes": on_votes,
        "off_votes": off_votes,
    }
