"""固定工位编号模块。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .base import ModuleContext
from .station_number_model import (
    StationNumberRecognizer,
    StationNumberTemplateModel,
)


class StationNumberModule:
    module_id = "station_number"

    def __init__(
        self,
        config: Mapping[str, Any],
        project_root: Path | None = None,
    ) -> None:
        self.config = dict(config)
        self.allowed = {
            int(value) for value in config.get("allowed_numbers", range(1, 11))
        }
        self.recognition_mode = str(
            config.get("recognition_mode", "capture_metadata")
        )
        self.use_detector_roi = bool(config.get("use_detector_roi", True))
        self.allow_full_frame_fallback = bool(
            config.get("allow_full_frame_fallback", False)
        )
        model_value = str(config.get("model_path", "")).strip()
        model_path = Path(model_value)
        if model_value and not model_path.is_absolute() and project_root is not None:
            model_path = (project_root / model_path).resolve()
        self.model_path = model_path
        self.recognizer: StationNumberRecognizer | None = None
        if (
            config.get("enabled", True)
            and self.recognition_mode == "image_classifier"
            and model_value
            and model_path.is_file()
        ):
            self.recognizer = StationNumberRecognizer(
                StationNumberTemplateModel.load(model_path),
                minimum_confidence=float(
                    config.get("minimum_frame_confidence", 0.55)
                ),
            )

    def run(self, context: ModuleContext) -> dict[str, Any]:
        if not self.config.get("enabled", True):
            return {"enabled": False, "status": "disabled", "number": None}
        if self.recognition_mode == "image_classifier":
            return self._run_image_classifier(context)
        return self._run_capture_metadata(context)

    def _run_image_classifier(self, context: ModuleContext) -> dict[str, Any]:
        if self.recognizer is None:
            return {
                "enabled": True,
                "status": "unavailable",
                "number": None,
                "reason": "station_image_classifier_not_trained",
                "model_path": str(self.model_path),
                "frames": [],
            }
        station_marker_bbox = (
            _best_station_marker_bbox(context.objects)
            if self.use_detector_roi
            else None
        )
        if (
            self.use_detector_roi
            and station_marker_bbox is None
            and not self.allow_full_frame_fallback
        ):
            return {
                "enabled": True,
                "status": "unreadable",
                "number": None,
                "confidence": 0.0,
                "votes": 0,
                "reason": "station_marker_not_detected",
                "roi_source": "yolo_station_marker",
                "station_marker_bbox_xyxy": None,
                "frames": [],
            }
        roi_source = (
            "yolo_station_marker" if station_marker_bbox is not None else "full_frame"
        )
        frame_results = [
            self.recognizer.read(path, roi_bbox_xyxy=station_marker_bbox)
            for path in context.image_paths
        ]
        confirmed = [
            result
            for result in frame_results
            if result.status == "confirmed" and result.number in self.allowed
        ]
        vote_counts = Counter(
            result.number for result in confirmed if result.number is not None
        )
        if not vote_counts:
            return {
                "enabled": True,
                "status": "unreadable",
                "number": None,
                "confidence": 0.0,
                "votes": 0,
                "reason": "no_confirmed_station_number_readings",
                "roi_source": roi_source,
                "station_marker_bbox_xyxy": (
                    list(station_marker_bbox) if station_marker_bbox else None
                ),
                "frames": [result.to_dict() for result in frame_results],
            }
        number, votes = vote_counts.most_common(1)[0]
        minimum_votes = 2 if len(frame_results) >= 2 else 1
        winners = [result for result in confirmed if result.number == number]
        confidence = min(result.confidence for result in winners)
        if votes < minimum_votes:
            return {
                "enabled": True,
                "status": "unreadable",
                "number": None,
                "confidence": confidence,
                "votes": votes,
                "reason": "multi_frame_station_numbers_do_not_agree",
                "roi_source": roi_source,
                "station_marker_bbox_xyxy": (
                    list(station_marker_bbox) if station_marker_bbox else None
                ),
                "frames": [result.to_dict() for result in frame_results],
            }
        metadata_number: int | None
        try:
            metadata_number = int(context.metadata.station_id)
        except ValueError:
            metadata_number = None
        return {
            "enabled": True,
            "status": "confirmed",
            "number": number,
            "confidence": confidence,
            "votes": votes,
            "reason": "multi_frame_majority_confirmed",
            "source": "image_classifier",
            "roi_source": roi_source,
            "station_marker_bbox_xyxy": (
                list(station_marker_bbox) if station_marker_bbox else None
            ),
            "metadata_number": metadata_number,
            "metadata_matches": metadata_number == number,
            "frames": [result.to_dict() for result in frame_results],
        }

    def _run_capture_metadata(self, context: ModuleContext) -> dict[str, Any]:
        try:
            number = int(context.metadata.station_id)
        except ValueError:
            return {
                "enabled": True,
                "status": "unavailable",
                "number": None,
                "reason": "station_id_is_not_numeric",
            }
        if number not in self.allowed:
            return {
                "enabled": True,
                "status": "unavailable",
                "number": None,
                "reason": "station_number_outside_allowed_range",
            }
        return {
            "enabled": True,
            "status": "confirmed",
            "number": number,
            "source": "capture_metadata",
        }


def _best_station_marker_bbox(
    objects: object,
) -> tuple[float, float, float, float] | None:
    candidates: list[tuple[float, tuple[float, float, float, float]]] = []
    for item in objects if isinstance(objects, (list, tuple)) else ():
        if not isinstance(item, Mapping):
            continue
        if (
            item.get("type") != "station_marker"
            and item.get("class") != "station_marker"
        ):
            continue
        bbox = item.get("bbox_xyxy")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        try:
            values = tuple(float(value) for value in bbox)
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        if values[2] <= values[0] or values[3] <= values[1]:
            continue
        candidates.append((confidence, values))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None
