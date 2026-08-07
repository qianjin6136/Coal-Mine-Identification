"""评估 field_detect_v1 标签与 field_cv 启发式检测的 IoU 重合度。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import cv2


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.detectors.field_cv_backend import FieldCvDetector
from app.domain import CaptureMetadata, RobotPose


CLASS_NAMES = [
    "station_marker",
    "coal_pile",
    "foreign_object",
    "digital_meter",
    "analog_meter",
    "indicator_red",
    "indicator_green",
]


def _yolo_to_xyxy(line: str, width: int, height: int) -> tuple[str, list[float]]:
    parts = line.strip().split()
    class_id = int(float(parts[0]))
    cx, cy, bw, bh = [float(v) for v in parts[1:5]]
    x1 = (cx - bw / 2) * width
    y1 = (cy - bh / 2) * height
    x2 = (cx + bw / 2) * width
    y2 = (cy + bh / 2) * height
    return CLASS_NAMES[class_id], [x1, y1, x2, y2]


def _iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "runtime_data" / "datasets" / "field_detect_v1",
    )
    parser.add_argument(
        "--split",
        default="val",
        choices=("train", "val", "test"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT
        / "runtime_data"
        / "datasets"
        / "field_detect_v1"
        / "field_cv_eval.json",
    )
    args = parser.parse_args()

    detector = FieldCvDetector(confidence=0.35)
    meta = CaptureMetadata(
        capture_id="eval",
        capture_time="2026-08-07T12:00:00+08:00",
        station_id="1",
        robot_pose=RobotPose(),
        camera_id="cam0",
        image_names=("frame.jpg",),
    )
    images = sorted((args.dataset / "images" / args.split).glob("*"))
    stats = {
        name: {"gt": 0, "pred": 0, "tp": 0}
        for name in CLASS_NAMES
        if name != "station_marker"
    }
    for image_path in images:
        label_path = (
            args.dataset / "labels" / args.split / f"{image_path.stem}.txt"
        )
        image = cv2.imread(str(image_path))
        if image is None or not label_path.is_file():
            continue
        height, width = image.shape[:2]
        gt_boxes: dict[str, list[list[float]]] = {name: [] for name in stats}
        for line in label_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            class_name, box = _yolo_to_xyxy(line, width, height)
            if class_name in gt_boxes:
                gt_boxes[class_name].append(box)
                stats[class_name]["gt"] += 1
        preds = detector.detect(image_path, meta, 0)
        pred_boxes: dict[str, list[list[float]]] = {name: [] for name in stats}
        for detection in preds:
            if detection.type in pred_boxes:
                pred_boxes[detection.type].append(detection.bbox.to_list())
                stats[detection.type]["pred"] += 1
        for class_name in stats:
            used = set()
            for pred in pred_boxes[class_name]:
                best_iou = 0.0
                best_idx = -1
                for index, gt in enumerate(gt_boxes[class_name]):
                    if index in used:
                        continue
                    score = _iou(pred, gt)
                    if score > best_iou:
                        best_iou = score
                        best_idx = index
                if best_iou >= 0.3 and best_idx >= 0:
                    used.add(best_idx)
                    stats[class_name]["tp"] += 1

    summary = {"split": args.split, "images": len(images), "classes": {}}
    for name, values in stats.items():
        precision = values["tp"] / values["pred"] if values["pred"] else 0.0
        recall = values["tp"] / values["gt"] if values["gt"] else 0.0
        summary["classes"][name] = {
            **values,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
