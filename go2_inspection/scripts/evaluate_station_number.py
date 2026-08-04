"""用带标签编号牌样本评估已保存模型，并输出逐图结果。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import json
import os


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.modules.station_number_model import (
    StationNumberRecognizer,
    StationNumberTemplateModel,
    load_station_number_samples,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--samples",
        type=Path,
        default=PROJECT_ROOT / "configs" / "station_number_samples_v2.json",
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=PROJECT_ROOT
        / ".."
        / ".."
        / "runtime_data"
        / "models"
        / "station_number_templates_v2.npz",
    )
    parser.add_argument("--minimum-confidence", type=float, default=0.46)
    parser.add_argument("--detector-weights", type=Path)
    parser.add_argument("--detector-confidence", type=float, default=0.25)
    parser.add_argument("--device", default="0")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    recognizer = StationNumberRecognizer(
        StationNumberTemplateModel.load(args.model),
        minimum_confidence=args.minimum_confidence,
    )
    samples = load_station_number_samples(args.samples)
    detector_rois = (
        _detect_station_rois(
            [path for _, path in samples],
            args.detector_weights,
            args.detector_confidence,
            args.device,
        )
        if args.detector_weights
        else None
    )
    rows = []
    correct = 0
    detected = 0
    for index, (expected, image_path) in enumerate(samples):
        roi_bbox = detector_rois[index] if detector_rois is not None else None
        if detector_rois is not None and roi_bbox is None:
            row = {
                "file": str(image_path),
                "expected": expected,
                "status": "unreadable",
                "number": None,
                "confidence": 0.0,
                "sign_bbox_xyxy": None,
                "reason": "station_marker_not_detected",
                "detector_bbox_xyxy": None,
                "correct": False,
            }
            rows.append(row)
            continue
        detected += int(roi_bbox is not None)
        result = recognizer.read(image_path, roi_bbox_xyxy=roi_bbox)
        row = {
            "file": str(image_path),
            "expected": expected,
            **result.to_dict(),
            "detector_bbox_xyxy": roi_bbox,
        }
        row["correct"] = result.status == "confirmed" and result.number == expected
        correct += int(row["correct"])
        rows.append(row)
    report = {
        "samples": len(rows),
        "correct": correct,
        "accuracy": correct / len(rows) if rows else 0.0,
        "detector_weights": (
            str(args.detector_weights.resolve()) if args.detector_weights else None
        ),
        "detector_confidence": (
            args.detector_confidence if args.detector_weights else None
        ),
        "detected": detected if detector_rois is not None else None,
        "detection_recall": (
            detected / len(rows) if detector_rois is not None and rows else None
        ),
        "results": rows,
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
        print(
            json.dumps(
                {key: value for key, value in report.items() if key != "results"},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(payload)


def _detect_station_rois(
    image_paths: list[Path],
    weights: Path,
    confidence: float,
    device: str,
) -> list[list[float] | None]:
    yolo_config_dir = (PROJECT_ROOT / ".." / ".." / "runtime_data" / "ultralytics").resolve()
    yolo_config_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(yolo_config_dir))
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(
            "ultralytics is required when --detector-weights is used"
        ) from exc
    model = YOLO(str(weights))
    predictions = model.predict(
        source=[str(path) for path in image_paths],
        conf=confidence,
        device=device,
        verbose=False,
        stream=True,
    )
    rois: list[list[float] | None] = []
    for result in predictions:
        candidates = [
            (float(box.conf[0].item()), box.xyxy[0].tolist())
            for box in result.boxes
        ]
        rois.append(max(candidates, key=lambda item: item[0])[1] if candidates else None)
    if len(rois) != len(image_paths):
        raise SystemExit(
            "detector result count does not match station-number sample count"
        )
    return rois


if __name__ == "__main__":
    main()
