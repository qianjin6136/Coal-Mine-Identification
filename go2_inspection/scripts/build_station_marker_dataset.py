"""从已复核的编号牌样本生成单类别 Ultralytics YOLO 数据集。"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.errors import ValidationError
from app.image_io import read_bgr_image
from app.modules.station_number_model import (
    load_station_number_samples,
    segment_station_number,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "station_marker_dataset_v1.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summary = build_station_marker_dataset(args.config, args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def build_station_marker_dataset(
    config_path: Path,
    output_override: Path | None = None,
) -> dict[str, Any]:
    import cv2
    import numpy as np

    config_path = Path(config_path).resolve()
    config = _load_mapping(config_path)
    samples_config = _resolve_project_path(config.get("samples_config"))
    output_root = (
        Path(output_override).resolve()
        if output_override is not None
        else _resolve_project_path(config.get("output_root"))
    )
    if samples_config is None or output_root is None:
        raise ValidationError("samples_config and output_root are required")
    if output_root.exists() and any(output_root.iterdir()):
        raise ValidationError(
            f"output directory must be empty to avoid mixing datasets: {output_root}"
        )

    class_name = str(config.get("class_name", "station_marker")).strip()
    if not class_name:
        raise ValidationError("class_name cannot be empty")
    padding_fraction = float(config.get("bbox_padding_fraction", 0.04))
    if not 0.0 <= padding_fraction <= 0.25:
        raise ValidationError("bbox_padding_fraction must be between 0 and 0.25")

    validation = _path_set(config.get("validation", []), "validation")
    test = _path_set(config.get("test", []), "test")
    if validation & test:
        raise ValidationError("validation and test paths must not overlap")

    manual_bboxes = _manual_bboxes(config.get("manual_bboxes", {}))

    samples = load_station_number_samples(samples_config)
    sample_keys = {
        _sample_key(label, path): (label, path)
        for label, path in samples
    }
    unknown = (validation | test) - set(sample_keys)
    if unknown:
        raise ValidationError(f"split references unknown sample: {sorted(unknown)[0]}")
    unknown_manual = set(manual_bboxes) - set(sample_keys)
    if unknown_manual:
        raise ValidationError(
            f"manual_bboxes references unknown sample: {sorted(unknown_manual)[0]}"
        )

    negative_root = _resolve_project_path(config.get("negative_root"))
    negative_extensions = _extensions(config.get("negative_extensions"))
    negative_samples = _discover_negative_samples(
        negative_root,
        negative_extensions,
    )
    negative_keys = {
        _negative_key(path): path
        for path in negative_samples
    }
    negative_validation = _path_set(
        config.get("negative_validation", []),
        "negative_validation",
    )
    negative_test = _path_set(
        config.get("negative_test", []),
        "negative_test",
    )
    if negative_validation & negative_test:
        raise ValidationError(
            "negative_validation and negative_test paths must not overlap"
        )
    unknown_negative = (negative_validation | negative_test) - set(negative_keys)
    if unknown_negative:
        raise ValidationError(
            f"negative split references unknown sample: {sorted(unknown_negative)[0]}"
        )

    for split in ("train", "val", "test"):
        (output_root / "images" / split).mkdir(parents=True, exist_ok=True)
        (output_root / "labels" / split).mkdir(parents=True, exist_ok=True)
    (output_root / "previews").mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    preview_images: dict[
        str,
        list[tuple[str, object, list[tuple[int, int, int, int]]]],
    ] = {
        "train": [],
        "val": [],
        "test": [],
    }
    for key, (label, source_path) in sample_keys.items():
        split = "test" if key in test else "val" if key in validation else "train"
        image = read_bgr_image(source_path)
        if image is None:
            raise ValidationError(f"station marker image cannot be read: {source_path}")
        height, width = image.shape[:2]
        annotation_source = "manual" if key in manual_bboxes else "segmentation"
        if key in manual_bboxes:
            raw_bboxes = manual_bboxes[key]
        else:
            segmentation = segment_station_number(image)
            if segmentation["error"] or not segmentation.get("sign_bbox"):
                raise ValidationError(
                    f"{source_path.name}: "
                    f"{segmentation['error'] or 'sign_bbox_missing'}; "
                    "add a reviewed manual_bboxes override"
                )
            raw_bboxes = [
                tuple(int(value) for value in segmentation["sign_bbox"])
            ]
        bboxes = [
            _expand_bbox(
                _validate_bbox(bbox, width, height, key),
                width,
                height,
                padding_fraction,
            )
            for bbox in raw_bboxes
        ]
        output_name = _output_name(label, source_path)
        output_image = output_root / "images" / split / output_name
        output_label = output_root / "labels" / split / f"{Path(output_name).stem}.txt"
        shutil.copy2(source_path, output_image)
        output_label.write_text(
            "".join(_yolo_line(0, bbox, width, height) for bbox in bboxes),
            encoding="utf-8",
        )
        digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        rows.append(
            {
                "source": str(source_path),
                "source_key": key,
                "station_number": label,
                "split": split,
                "image": str(output_image),
                "label": str(output_label),
                "width": width,
                "height": height,
                "annotation_source": annotation_source,
                "bbox_xyxy": list(bboxes[0]),
                "bboxes_xyxy": [list(bbox) for bbox in bboxes],
                "bbox_area_fractions": [
                    round(
                        (bbox[2] - bbox[0])
                        * (bbox[3] - bbox[1])
                        / (width * height),
                        6,
                    )
                    for bbox in bboxes
                ],
                "sha256": digest,
                "dhash": _difference_hash(image),
            }
        )
        preview_images[split].append((f"{label}/{source_path.name}", image, bboxes))

    for key, source_path in negative_keys.items():
        split = (
            "test"
            if key in negative_test
            else "val"
            if key in negative_validation
            else "train"
        )
        image = read_bgr_image(source_path)
        if image is None:
            raise ValidationError(f"negative image cannot be read: {source_path}")
        height, width = image.shape[:2]
        output_name = _negative_output_name(source_path)
        output_image = output_root / "images" / split / output_name
        output_label = output_root / "labels" / split / f"{Path(output_name).stem}.txt"
        shutil.copy2(source_path, output_image)
        output_label.write_text("", encoding="utf-8")
        digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        rows.append(
            {
                "source": str(source_path),
                "source_key": key,
                "station_number": None,
                "split": split,
                "image": str(output_image),
                "label": str(output_label),
                "width": width,
                "height": height,
                "annotation_source": "confirmed_negative",
                "bbox_xyxy": None,
                "bboxes_xyxy": [],
                "bbox_area_fractions": [],
                "sha256": digest,
                "dhash": _difference_hash(image),
            }
        )
        preview_images[split].append((f"-1/{source_path.name}", image, []))

    _write_dataset_yaml(output_root, class_name)
    manifest_path = output_root / "manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    for split, values in preview_images.items():
        _write_preview_sheet(
            output_root / "previews" / f"{split}.jpg",
            values,
            class_name,
            cv2,
            np,
        )

    split_counts = Counter(row["split"] for row in rows)
    station_counts = {
        split: dict(
            sorted(
                Counter(
                    row["station_number"]
                    for row in rows
                    if row["split"] == split and row["station_number"] is not None
                ).items()
            )
        )
        for split in ("train", "val", "test")
    }
    duplicate_pairs = _cross_split_near_duplicates(rows)
    source_config = _load_mapping(samples_config)
    excluded = source_config.get("exclude", [])
    summary = {
        "dataset": str(output_root),
        "dataset_yaml": str(output_root / "dataset.yaml"),
        "class_names": [class_name],
        "images": len(rows),
        "annotations": sum(len(row["bboxes_xyxy"]) for row in rows),
        "positive_images": sum(bool(row["bboxes_xyxy"]) for row in rows),
        "negative_images": sum(not row["bboxes_xyxy"] for row in rows),
        "manual_annotation_images": sum(
            row["annotation_source"] == "manual" for row in rows
        ),
        "source_samples_excluded": len(excluded) if isinstance(excluded, list) else None,
        "split_counts": {
            split: split_counts.get(split, 0)
            for split in ("train", "val", "test")
        },
        "station_number_counts": station_counts,
        "cross_split_near_duplicate_pairs": duplicate_pairs,
        "warnings": list(config.get("warnings", [])),
    }
    (output_root / "build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def _load_mapping(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValidationError(f"configuration must be a JSON object: {path}")
    return dict(value)


def _resolve_project_path(value: object) -> Path | None:
    if value is None:
        return None
    path = Path(str(value))
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def _path_set(value: object, name: str) -> set[str]:
    if not isinstance(value, list):
        raise ValidationError(f"{name} must be an array")
    return {str(item).replace("\\", "/") for item in value}


def _manual_bboxes(
    value: object,
) -> dict[str, list[tuple[int, int, int, int]]]:
    if not isinstance(value, Mapping):
        raise ValidationError("manual_bboxes must be an object")
    result: dict[str, list[tuple[int, int, int, int]]] = {}
    for raw_key, raw_boxes in value.items():
        key = str(raw_key).replace("\\", "/")
        if not isinstance(raw_boxes, list) or not raw_boxes:
            raise ValidationError(f"manual_bboxes[{key}] must be a non-empty array")
        boxes: list[tuple[int, int, int, int]] = []
        for raw_bbox in raw_boxes:
            if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
                raise ValidationError(
                    f"manual_bboxes[{key}] entries must be [x1, y1, x2, y2]"
                )
            try:
                boxes.append(tuple(int(value) for value in raw_bbox))
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    f"manual_bboxes[{key}] entries must contain integers"
                ) from exc
        result[key] = boxes
    return result


def _extensions(value: object) -> set[str]:
    if value is None:
        return {".jpg", ".jpeg", ".png"}
    if not isinstance(value, list):
        raise ValidationError("negative_extensions must be an array")
    extensions = {
        item if item.startswith(".") else f".{item}"
        for item in (str(raw).strip().lower() for raw in value)
        if item
    }
    if not extensions:
        raise ValidationError("negative_extensions cannot be empty")
    return extensions


def _discover_negative_samples(
    root: Path | None,
    extensions: set[str],
) -> list[Path]:
    if root is None:
        return []
    if not root.is_dir():
        raise ValidationError(f"negative_root is not a directory: {root}")
    return sorted(
        path.resolve()
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in extensions
    )


def _negative_key(path: Path) -> str:
    return f"-1/{path.name}"


def _sample_key(label: int, path: Path) -> str:
    return f"{label}/{path.name}"


def _output_name(label: int, source_path: Path) -> str:
    stem = re.sub(r"[^0-9A-Za-z_.-]+", "_", f"{label}_{source_path.stem}").strip("_")
    return f"{stem}{source_path.suffix.lower()}"


def _negative_output_name(source_path: Path) -> str:
    stem = re.sub(
        r"[^0-9A-Za-z_.-]+",
        "_",
        f"negative_{source_path.stem}",
    ).strip("_")
    return f"{stem}{source_path.suffix.lower()}"


def _validate_bbox(
    bbox: tuple[int, int, int, int],
    width: int,
    height: int,
    key: str,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValidationError(
            f"invalid bbox for {key}: {bbox} outside {width}x{height}"
        )
    return bbox


def _expand_bbox(
    bbox: tuple[int, int, int, int],
    width: int,
    height: int,
    fraction: float,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    padding = max(2, round(max(x2 - x1, y2 - y1) * fraction))
    return (
        max(0, x1 - padding),
        max(0, y1 - padding),
        min(width, x2 + padding),
        min(height, y2 + padding),
    )


def _yolo_line(
    class_index: int,
    bbox: tuple[int, int, int, int],
    width: int,
    height: int,
) -> str:
    x1, y1, x2, y2 = bbox
    center_x = (x1 + x2) / 2 / width
    center_y = (y1 + y2) / 2 / height
    box_width = (x2 - x1) / width
    box_height = (y2 - y1) / height
    values = (center_x, center_y, box_width, box_height)
    if not all(0.0 <= value <= 1.0 for value in values):
        raise ValidationError(f"invalid normalized YOLO box: {values}")
    return (
        f"{class_index} {center_x:.6f} {center_y:.6f} "
        f"{box_width:.6f} {box_height:.6f}\n"
    )


def _write_dataset_yaml(output_root: Path, class_name: str) -> None:
    value = (
        f"path: '{output_root.as_posix()}'\n"
        "train: images/train\n"
        "val: images/val\n"
        "test: images/test\n"
        "names:\n"
        f"  0: {class_name}\n"
    )
    (output_root / "dataset.yaml").write_text(value, encoding="utf-8")


def _difference_hash(image: object) -> str:
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = small[:, 1:] > small[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def _cross_split_near_duplicates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            if left["split"] == right["split"]:
                continue
            if left["sha256"] == right["sha256"]:
                distance = 0
            else:
                distance = (
                    int(left["dhash"], 16) ^ int(right["dhash"], 16)
                ).bit_count()
            if distance <= 4:
                pairs.append(
                    {
                        "left": left["source_key"],
                        "left_split": left["split"],
                        "right": right["source_key"],
                        "right_split": right["split"],
                        "dhash_distance": distance,
                    }
                )
    return pairs


def _write_preview_sheet(
    path: Path,
    items: list[tuple[str, object, list[tuple[int, int, int, int]]]],
    class_name: str,
    cv2: object,
    np: object,
) -> None:
    columns = 5
    tile_width = 360
    tile_height = 250
    rows = max(1, math.ceil(len(items) / columns))
    sheet = np.full(
        (rows * tile_height, columns * tile_width, 3),
        28,
        dtype=np.uint8,
    )
    for index, (name, image, bboxes) in enumerate(items):
        annotated = image.copy()
        for bbox in bboxes:
            x1, y1, x2, y2 = bbox
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (40, 220, 40), 8)
            cv2.putText(
                annotated,
                class_name,
                (x1, max(35, y1 - 12)),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.1,
                (40, 220, 40),
                3,
                cv2.LINE_AA,
            )
        if not bboxes:
            cv2.putText(
                annotated,
                "NEGATIVE",
                (20, 55),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.3,
                (40, 180, 255),
                4,
                cv2.LINE_AA,
            )
        scale = min(340 / annotated.shape[1], 205 / annotated.shape[0])
        resized = cv2.resize(
            annotated,
            (
                max(1, round(annotated.shape[1] * scale)),
                max(1, round(annotated.shape[0] * scale)),
            ),
            interpolation=cv2.INTER_AREA,
        )
        row = index // columns
        column = index % columns
        left = column * tile_width + (tile_width - resized.shape[1]) // 2
        top = row * tile_height + 36 + (205 - resized.shape[0]) // 2
        sheet[top : top + resized.shape[0], left : left + resized.shape[1]] = resized
        cv2.putText(
            sheet,
            name,
            (column * tile_width + 8, row * tile_height + 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    encoded = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])[1]
    encoded.tofile(str(path))


if __name__ == "__main__":
    main()
