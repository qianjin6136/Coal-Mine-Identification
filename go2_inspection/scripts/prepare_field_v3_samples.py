"""整理 field_v3 样本目录，并用 OpenCV 启发式自动生成 YOLO 标注。"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path
import sys

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


CLASS_NAMES = [
    "station_marker",
    "coal_pile",
    "foreign_object",
    "digital_meter",
    "analog_meter",
    "indicator_red",
    "indicator_green",
]


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        dst.symlink_to(src.resolve())
    except OSError:
        shutil.copy2(src, dst)


def _collect_images(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    files = [
        path
        for path in sorted(directory.iterdir())
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    ]
    return files


def _xyxy_to_yolo(
    x1: float, y1: float, x2: float, y2: float, width: int, height: int
) -> tuple[float, float, float, float]:
    cx = ((x1 + x2) / 2.0) / width
    cy = ((y1 + y2) / 2.0) / height
    w = (x2 - x1) / width
    h = (y2 - y1) / height
    return (
        float(np.clip(cx, 0.0, 1.0)),
        float(np.clip(cy, 0.0, 1.0)),
        float(np.clip(w, 0.0, 1.0)),
        float(np.clip(h, 0.0, 1.0)),
    )


def _write_label(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _detect_blue_markers(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (95, 60, 60), (135, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[tuple[int, int, int, int]] = []
    height, width = image.shape[:2]
    min_area = width * height * 0.0004
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        aspect = w / max(h, 1)
        if not 0.55 <= aspect <= 1.45:
            continue
        boxes.append((x, y, x + w, y + h))
    return boxes


def _detect_bright_cloth(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    masks = [
        cv2.inRange(hsv, (5, 120, 120), (35, 255, 255)),   # yellow/orange
        cv2.inRange(hsv, (35, 80, 80), (95, 255, 255)),    # green/cyan cloth
        cv2.inRange(hsv, (140, 80, 80), (179, 255, 255)),  # magenta/reddish cloth
    ]
    mask = masks[0]
    for item in masks[1:]:
        mask = cv2.bitwise_or(mask, item)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[tuple[int, int, int, int]] = []
    height, width = image.shape[:2]
    min_area = width * height * 0.0008
    max_area = width * height * 0.25
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area or area > max_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        # Skip huge background panels / barriers near image edges.
        if w > width * 0.55 or h > height * 0.55:
            continue
        if y < height * 0.05 and h > height * 0.35:
            continue
        boxes.append((x, y, x + w, y + h))
    return boxes


def _detect_coal_pile(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dark = cv2.inRange(gray, 0, 55)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    low_sat = cv2.inRange(hsv, (0, 0, 0), (179, 70, 70))
    mask = cv2.bitwise_and(dark, low_sat)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[tuple[int, int, int, int]] = []
    height, width = image.shape[:2]
    min_area = width * height * 0.01
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if y + h < height * 0.35:
            continue
        if w < width * 0.08 or h < height * 0.05:
            continue
        boxes.append((x, y, x + w, y + h))
    return boxes[:2]


def _detect_analog_meter(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 5)
    height, width = image.shape[:2]
    min_radius = int(min(width, height) * 0.08)
    max_radius = int(min(width, height) * 0.48)
    circles = cv2.HoughCircles(
        gray,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=min(width, height) // 4,
        param1=120,
        param2=40,
        minRadius=min_radius,
        maxRadius=max_radius,
    )
    boxes: list[tuple[int, int, int, int]] = []
    if circles is None:
        return boxes
    for cx, cy, radius in np.round(circles[0]).astype(int):
        x1 = max(0, cx - radius)
        y1 = max(0, cy - radius)
        x2 = min(width - 1, cx + radius)
        y2 = min(height - 1, cy + radius)
        boxes.append((x1, y1, x2, y2))
    return boxes[:3]


def _detect_indicators(image: np.ndarray) -> dict[str, list[tuple[int, int, int, int]]]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    red = cv2.bitwise_or(
        cv2.inRange(hsv, (0, 100, 120), (12, 255, 255)),
        cv2.inRange(hsv, (165, 100, 120), (179, 255, 255)),
    )
    green = cv2.inRange(hsv, (40, 80, 100), (95, 255, 255))
    result: dict[str, list[tuple[int, int, int, int]]] = {
        "indicator_red": [],
        "indicator_green": [],
    }
    height, width = image.shape[:2]
    min_area = width * height * 0.00015
    max_area = width * height * 0.04
    for name, mask in (("indicator_red", red), ("indicator_green", green)):
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area or area > max_area:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            aspect = w / max(h, 1)
            if not 0.5 <= aspect <= 2.0:
                continue
            result[name].append((x, y, x + w, y + h))
        result[name] = result[name][:2]
    return result


def _detect_digital_meter(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    # Red LED segments / glow.
    red = cv2.bitwise_or(
        cv2.inRange(hsv, (0, 80, 90), (12, 255, 255)),
        cv2.inRange(hsv, (165, 80, 90), (179, 255, 255)),
    )
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, np.ones((9, 5), np.uint8))
    contours, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = image.shape[:2]
    boxes: list[tuple[int, int, int, int]] = []
    min_area = width * height * 0.0005
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        aspect = w / max(h, 1)
        if aspect < 1.2 or h < height * 0.015:
            continue
        pad_x = int(w * 0.15)
        pad_y = int(h * 0.35)
        boxes.append(
            (
                max(0, x - pad_x),
                max(0, y - pad_y),
                min(width - 1, x + w + pad_x),
                min(height - 1, y + h + pad_y),
            )
        )
    boxes.sort(key=lambda box: (box[2] - box[0]) * (box[3] - box[1]), reverse=True)
    return boxes[:1]


def _annotate_for_kind(
    image: np.ndarray, kind: str
) -> list[tuple[str, tuple[int, int, int, int]]]:
    labels: list[tuple[str, tuple[int, int, int, int]]] = []
    if kind in {"number", "cloth", "root"}:
        for box in _detect_blue_markers(image):
            labels.append(("station_marker", box))
        for box in _detect_bright_cloth(image):
            labels.append(("foreign_object", box))
        if kind in {"number", "root"}:
            for box in _detect_coal_pile(image):
                labels.append(("coal_pile", box))
    if kind == "light":
        for box in _detect_digital_meter(image):
            labels.append(("digital_meter", box))
        indicators = _detect_indicators(image)
        for name, boxes in indicators.items():
            for box in boxes:
                labels.append((name, box))
    if kind == "pointertable":
        for box in _detect_analog_meter(image):
            labels.append(("analog_meter", box))
    return labels


def _split_name(index: int, total: int) -> str:
    if total <= 1:
        return "train"
    ratio = index / total
    if ratio < 0.7:
        return "train"
    if ratio < 0.9:
        return "val"
    return "test"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sample-root",
        type=Path,
        default=REPO_ROOT / "sample",
    )
    parser.add_argument(
        "--field-root",
        type=Path,
        default=PROJECT_ROOT / "sample" / "field_v3",
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=PROJECT_ROOT / "runtime_data" / "datasets" / "field_detect_v1",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    mapping = {
        "number": args.sample_root / "number",
        "light": args.sample_root / "light",
        "pointertable": args.sample_root / "pointertable",
        "cloth": args.sample_root / "cloth",
        "root": args.sample_root,
    }
    # Also include project-local coal/conveyor as weak positives.
    extra = {
        "coal_extra": PROJECT_ROOT / "sample" / "煤堆",
        "conveyor_extra": PROJECT_ROOT / "sample" / "传送带",
    }

    field_root = args.field_root
    if field_root.exists():
        shutil.rmtree(field_root)
    field_root.mkdir(parents=True, exist_ok=True)

    organized: list[tuple[str, Path]] = []
    for kind, source in mapping.items():
        images = _collect_images(source)
        if kind == "root":
            # Only loose files directly under sample root.
            images = [path for path in images if path.parent == source]
        label_kind = "number" if kind == "root" else kind
        for image in images:
            destination = field_root / kind / image.name
            _link_or_copy(image, destination)
            organized.append((label_kind, destination))
    for kind, source in extra.items():
        for image in _collect_images(source):
            destination = field_root / kind / image.name
            _link_or_copy(image, destination)
            label_kind = "number" if "coal" in kind or "conveyor" in kind else kind
            organized.append((label_kind, destination))

    dataset_root = args.dataset_root
    if dataset_root.exists():
        shutil.rmtree(dataset_root)
    for split in ("train", "val", "test"):
        (dataset_root / "images" / split).mkdir(parents=True, exist_ok=True)
        (dataset_root / "labels" / split).mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    rng.shuffle(organized)
    class_index = {name: index for index, name in enumerate(CLASS_NAMES)}
    manifest: list[dict[str, object]] = []
    counts = {name: 0 for name in CLASS_NAMES}
    cloth_positive = 0

    for index, (kind, image_path) in enumerate(organized):
        image = cv2.imread(str(image_path))
        if image is None:
            continue
        height, width = image.shape[:2]
        labels = _annotate_for_kind(image, kind)
        if any(name == "foreign_object" for name, _ in labels):
            cloth_positive += 1
        split = _split_name(index, len(organized))
        stem = f"{image_path.parent.name}_{image_path.stem}".replace(" ", "_")
        dst_image = dataset_root / "images" / split / f"{stem}{image_path.suffix.lower()}"
        dst_label = dataset_root / "labels" / split / f"{stem}.txt"
        _link_or_copy(image_path, dst_image)
        lines: list[str] = []
        annotation_boxes: list[dict[str, object]] = []
        for class_name, (x1, y1, x2, y2) in labels:
            cx, cy, bw, bh = _xyxy_to_yolo(x1, y1, x2, y2, width, height)
            lines.append(f"{class_index[class_name]} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            counts[class_name] += 1
            annotation_boxes.append(
                {
                    "class": class_name,
                    "bbox_xyxy": [int(x1), int(y1), int(x2), int(y2)],
                }
            )
        _write_label(dst_label, lines)
        field_sidecar = image_path.with_suffix(image_path.suffix + ".json")
        field_sidecar.write_text(
            json.dumps({"annotations": annotation_boxes}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        manifest.append(
            {
                "path": str(dst_image.resolve()),
                "source": str(image_path.resolve()),
                "split": split,
                "width": width,
                "height": height,
                "kind": kind,
                "label_count": len(lines),
            }
        )

    yaml_path = dataset_root / "dataset.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                f"path: {dataset_root.resolve()}",
                "train: images/train",
                "val: images/val",
                "test: images/test",
                f"nc: {len(CLASS_NAMES)}",
                "names:",
                *[f"  {index}: {name}" for index, name in enumerate(CLASS_NAMES)],
                "",
            ]
        ),
        encoding="utf-8",
    )
    summary = {
        "field_root": str(field_root.resolve()),
        "dataset_root": str(dataset_root.resolve()),
        "images": len(manifest),
        "cloth_positive_images": cloth_positive,
        "class_box_counts": counts,
        "splits": {
            split: sum(1 for item in manifest if item["split"] == split)
            for split in ("train", "val", "test")
        },
    }
    (dataset_root / "build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (dataset_root / "manifest.jsonl").open("w", encoding="utf-8") as stream:
        for item in manifest:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
