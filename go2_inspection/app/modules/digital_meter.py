"""独立数字表模块：整图读取、逐帧诊断和多帧投票。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from ..meters.digital_model import DigitalMeterRecognizer, TemplateDigitModel
from .base import ModuleContext


class DigitalMeterModule:
    module_id = "digital_meter"

    def __init__(
        self,
        config: Mapping[str, Any],
        project_root: Path,
    ) -> None:
        self.config = dict(config)
        model_value = str(config.get("model_path", "")).strip()
        model_path = Path(model_value)
        if model_value and not model_path.is_absolute():
            model_path = (project_root / model_path).resolve()
        self.model_path = model_path
        self.recognizer: DigitalMeterRecognizer | None = None
        if config.get("enabled", True) and model_value and model_path.is_file():
            self.recognizer = DigitalMeterRecognizer(
                TemplateDigitModel.load(model_path),
                digit_count=config.get(
                    "digit_counts",
                    int(config.get("digit_count", 4)),
                ),
                decimal_places=int(config.get("decimal_places", 1)),
                allow_negative=bool(config.get("allow_negative", True)),
                minimum_confidence=float(
                    config.get("minimum_frame_confidence", 0.55)
                ),
            )

    def run(self, context: ModuleContext) -> dict[str, Any]:
        if not self.config.get("enabled", True):
            return {"enabled": False, "status": "disabled", "raw_text": None}
        if self.recognizer is None:
            return {
                "enabled": True,
                "status": "unavailable",
                "raw_text": None,
                "value": None,
                "reason": "digital_meter_model_not_trained",
                "model_path": str(self.model_path),
                "frames": [],
            }
        frame_results = [
            self.recognizer.read(path)
            for path in context.image_paths
        ]
        confirmed = [
            result for result in frame_results if result.status == "confirmed"
        ]
        minimum_votes = 2 if len(frame_results) >= 2 else 1
        vote_counts = Counter(
            result.raw_text for result in confirmed if result.raw_text is not None
        )
        if not vote_counts:
            return {
                "enabled": True,
                "status": "unreadable",
                "raw_text": None,
                "value": None,
                "confidence": 0.0,
                "votes": 0,
                "reason": "no_confirmed_frame_readings",
                "frames": [result.to_dict() for result in frame_results],
            }
        text, votes = vote_counts.most_common(1)[0]
        winners = [result for result in confirmed if result.raw_text == text]
        confidence = min(result.confidence for result in winners)
        if votes < minimum_votes:
            return {
                "enabled": True,
                "status": "unreadable",
                "raw_text": None,
                "value": None,
                "confidence": confidence,
                "votes": votes,
                "reason": "multi_frame_readings_do_not_agree",
                "frames": [result.to_dict() for result in frame_results],
            }
        return {
            "enabled": True,
            "status": "confirmed",
            "raw_text": text,
            "value": float(text),
            "confidence": confidence,
            "votes": votes,
            "reason": "multi_frame_majority_confirmed",
            "frames": [result.to_dict() for result in frame_results],
        }
