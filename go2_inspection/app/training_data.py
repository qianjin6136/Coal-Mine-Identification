"""把已复核的矩形框标注转换为 Ultralytics YOLO 数据集。"""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import shutil
from typing import Any, Iterable, Mapping, Sequence

from .domain import BoundingBox
from .errors import ValidationError


def load_manifest(path: Path) -> list[dict[str, Any]]:
    """读取 prepare_dataset.py 生成的 JSONL 清单。"""

    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    f"invalid manifest JSON at line {line_number}"
                ) from exc
            if not isinstance(item, Mapping):
                raise ValidationError(
                    f"manifest line {line_number} must be a JSON object"
                )
            records.append(dict(item))
    return records


def load_training_classes(path: Path) -> list[str]:
    """按配置文件顺序读取模型标签，并排除明确禁用训练的条目。"""

    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValidationError("classes configuration must be a JSON object")
    return [
        str(model_name)
        for model_name, config in value.items()
        if not isinstance(config, Mapping) or config.get("train", True)
    ]


def build_yolo_dataset(
    manifest_path: Path,
    classes_path: Path,
    output_root: Path,
    *,
    materialize_mode: str = "copy",
) -> dict[str, Any]:
    """转换全部有 sidecar 标注的图片，不把未标注图片误当负样本。"""

    if materialize_mode not in {"copy", "hardlink"}:
        raise ValueError("materialize_mode must be copy or hardlink")
    records = load_manifest(manifest_path)
    class_names = load_training_classes(classes_path)
    class_indices = {name: index for index, name in enumerate(class_names)}
    configured_classes_value = json.loads(Path(classes_path).read_text(encoding="utf-8"))
    configured_classes = set(configured_classes_value)
    output_root = Path(output_root)
    _require_empty_output(output_root)
    for split in ("train", "val", "test"):
        (output_root / "images" / split).mkdir(parents=True, exist_ok=True)
        (output_root / "labels" / split).mkdir(parents=True, exist_ok=True)

    counts: Counter[str] = Counter()
    ignored_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    skipped_unlabelled: list[str] = []
    converted = 0
    for record in records:
        image_path = Path(str(record.get("path", "")))
        if not image_path.is_file():
            raise ValidationError(f"manifest image not found: {image_path}")
        split = str(record.get("split") or "")
        if split not in {"train", "val", "test"}:
            raise ValidationError(f"manifest has invalid split for {image_path}: {split}")
        sidecar = _annotation_sidecar(image_path)
        if sidecar is None:
            skipped_unlabelled.append(str(image_path))
            continue
        annotations = _load_annotations(sidecar)
        width = int(record.get("width") or 0)
        height = int(record.get("height") or 0)
        if width <= 0 or height <= 0:
            raise ValidationError(f"manifest image dimensions are missing: {image_path}")

        unique_name = _unique_name(record, image_path)
        destination_image = (
            output_root / "images" / split / f"{unique_name}{image_path.suffix.lower()}"
        )
        destination_label = output_root / "labels" / split / f"{unique_name}.txt"
        _materialize(image_path, destination_image, materialize_mode)
        label_lines: list[str] = []
        for annotation in annotations:
            class_name = str(annotation.get("class", "")).strip()
            if class_name not in class_indices:
                if class_name in configured_classes:
                    ignored_counts[class_name] += 1
                    continue
                raise ValidationError(
                    f"unknown annotation class '{class_name}' in {sidecar}"
                )
            bbox = BoundingBox.from_sequence(annotation.get("bbox_xyxy", ()))
            label_lines.append(
                _yolo_label(class_indices[class_name], bbox, width, height)
            )
            counts[class_name] += 1
        destination_label.write_text(
            "\n".join(label_lines) + ("\n" if label_lines else ""),
            encoding="utf-8",
        )
        converted += 1
        split_counts[split] += 1

    yaml_path = output_root / "dataset.yaml"
    yaml_path.write_text(
        _dataset_yaml(output_root, class_names),
        encoding="utf-8",
    )
    summary = {
        "images_converted": converted,
        "images_skipped_unlabelled": len(skipped_unlabelled),
        "skipped_unlabelled": skipped_unlabelled,
        "split_counts": dict(split_counts),
        "class_counts": {name: counts.get(name, 0) for name in class_names},
        "ignored_annotation_counts": dict(ignored_counts),
        "dataset_yaml": str(yaml_path),
    }
    (output_root / "build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def _require_empty_output(output_root: Path) -> None:
    if output_root.exists() and any(output_root.iterdir()):
        raise ValidationError(
            f"output directory must be empty to avoid mixing datasets: {output_root}"
        )
    output_root.mkdir(parents=True, exist_ok=True)


def _annotation_sidecar(image_path: Path) -> Path | None:
    candidates = (
        image_path.with_name(image_path.name + ".labels.json"),
        image_path.with_suffix(".labels.json"),
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _load_annotations(sidecar: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid annotation JSON: {sidecar}") from exc
    if not isinstance(value, list):
        raise ValidationError(f"annotations must be a JSON array: {sidecar}")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValidationError(f"each annotation must be an object: {sidecar}")
    return [dict(item) for item in value]


def _unique_name(record: Mapping[str, Any], image_path: Path) -> str:
    capture_id = str(record.get("capture_id") or image_path.parent.name)
    digest = str(record.get("sha256") or "")[:10]
    safe_capture = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in capture_id
    ).strip("._")
    return f"{safe_capture}_{image_path.stem}_{digest}".strip("_")


def _materialize(source: Path, destination: Path, mode: str) -> None:
    if mode == "hardlink":
        try:
            os.link(source, destination)
            return
        except OSError:
            pass
    shutil.copy2(source, destination)


def _yolo_label(
    class_index: int,
    bbox: BoundingBox,
    image_width: int,
    image_height: int,
) -> str:
    x1 = min(max(bbox.x1, 0.0), float(image_width))
    y1 = min(max(bbox.y1, 0.0), float(image_height))
    x2 = min(max(bbox.x2, 0.0), float(image_width))
    y2 = min(max(bbox.y2, 0.0), float(image_height))
    if x2 <= x1 or y2 <= y1:
        raise ValidationError("bounding box falls outside image after clipping")
    center_x = ((x1 + x2) / 2.0) / image_width
    center_y = ((y1 + y2) / 2.0) / image_height
    width = (x2 - x1) / image_width
    height = (y2 - y1) / image_height
    return (
        f"{class_index} {center_x:.8f} {center_y:.8f} "
        f"{width:.8f} {height:.8f}"
    )


def _dataset_yaml(output_root: Path, class_names: Sequence[str]) -> str:
    escaped_root = str(output_root.resolve()).replace("\\", "/").replace("'", "''")
    lines = [
        f"path: '{escaped_root}'",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        "names:",
    ]
    lines.extend(
        f"  {index}: '{name.replace(chr(39), chr(39) * 2)}'"
        for index, name in enumerate(class_names)
    )
    return "\n".join(lines) + "\n"
