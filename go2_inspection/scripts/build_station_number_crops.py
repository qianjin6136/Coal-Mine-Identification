"""Build a station-number classifier dataset from reviewed YOLO boxes."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.errors import ValidationError
from app.image_io import read_bgr_image, write_bgr_image
from app.modules.station_number_model import (
    load_station_number_samples,
    segment_station_number,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "station_number_crops_v2.json",
    )
    args = parser.parse_args()
    summary = build_station_number_crops(args.config)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def build_station_number_crops(config_path: Path) -> dict[str, Any]:
    """Crop the intended sign from each labelled source image.

    Rows in the detector manifest are matched by SHA-256 instead of their old
    path or station-number field. This keeps annotations valid after a sample
    is moved to its corrected numeric class directory.
    """

    config_path = Path(config_path).resolve()
    config = _load_mapping(config_path)
    samples_config = _resolve_project_path(config.get("samples_config"))
    annotation_manifest = _resolve_project_path(
        config.get("annotation_manifest")
    )
    output_root = _resolve_project_path(config.get("output_root"))
    if samples_config is None or annotation_manifest is None or output_root is None:
        raise ValidationError(
            "samples_config, annotation_manifest and output_root are required"
        )
    if not annotation_manifest.is_file():
        raise ValidationError(
            f"station marker annotation manifest not found: {annotation_manifest}"
        )
    if output_root.exists() and any(output_root.iterdir()):
        raise ValidationError(
            f"output directory must be empty to avoid mixing datasets: {output_root}"
        )

    padding_fraction = float(config.get("padding_fraction", 0.08))
    if not 0.0 <= padding_fraction <= 0.30:
        raise ValidationError("padding_fraction must be between 0 and 0.30")
    bbox_selection = str(config.get("bbox_selection", "largest_area"))
    if bbox_selection != "largest_area":
        raise ValidationError("bbox_selection must be largest_area")
    skip_segmentation_failures = bool(
        config.get("skip_segmentation_failures", True)
    )

    annotations = _annotations_by_digest(annotation_manifest)
    samples = load_station_number_samples(samples_config)
    output_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    corrected_manifest_labels = 0

    for label, source_path in samples:
        digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        annotation = annotations.get(digest)
        if annotation is None:
            raise ValidationError(
                f"no reviewed detector annotation matches: {source_path}"
            )
        raw_bboxes = annotation.get("bboxes_xyxy")
        if not isinstance(raw_bboxes, list) or not raw_bboxes:
            raise ValidationError(
                f"reviewed detector annotation has no box: {source_path}"
            )
        image = read_bgr_image(source_path)
        if image is None:
            raise ValidationError(f"station number sample cannot be read: {source_path}")
        height, width = image.shape[:2]
        bbox = _largest_bbox(raw_bboxes, width, height, source_path)
        crop_bbox = _expand_bbox(
            bbox,
            width,
            height,
            padding_fraction,
        )
        x1, y1, x2, y2 = crop_bbox
        crop = image[y1:y2, x1:x2]
        segmentation = segment_station_number(crop)
        if segmentation["error"]:
            failure = {
                "source": str(source_path),
                "label": label,
                "reason": str(segmentation["error"]),
                "bbox_xyxy": list(bbox),
                "crop_bbox_xyxy": list(crop_bbox),
            }
            if not skip_segmentation_failures:
                raise ValidationError(
                    f"{source_path.name}: {segmentation['error']} after reviewed crop"
                )
            skipped.append(failure)
            continue

        destination = output_root / str(label) / source_path.name
        if not write_bgr_image(destination, crop):
            raise ValidationError(f"station number crop cannot be written: {destination}")
        old_label = annotation.get("station_number")
        label_was_corrected = old_label is not None and int(old_label) != label
        corrected_manifest_labels += int(label_was_corrected)
        rows.append(
            {
                "source": str(source_path),
                "crop": str(destination),
                "label": label,
                "sha256": digest,
                "annotation_source": annotation.get("annotation_source"),
                "annotation_manifest_label": old_label,
                "label_corrected_from_directory": label_was_corrected,
                "bbox_selection": bbox_selection,
                "bbox_xyxy": list(bbox),
                "crop_bbox_xyxy": list(crop_bbox),
                "crop_width": x2 - x1,
                "crop_height": y2 - y1,
            }
        )

    manifest_path = output_root / "manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    counts = Counter(int(row["label"]) for row in rows)
    summary = {
        "dataset": str(output_root),
        "source_samples": len(samples),
        "classifier_samples": len(rows),
        "samples_per_class": {
            str(label): counts[label] for label in sorted(counts)
        },
        "corrected_manifest_labels": corrected_manifest_labels,
        "skipped_samples": len(skipped),
        "skipped": skipped,
        "annotation_manifest": str(annotation_manifest),
        "manifest": str(manifest_path),
        "bbox_selection": bbox_selection,
        "padding_fraction": padding_fraction,
    }
    (output_root / "build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def _annotations_by_digest(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"invalid annotation manifest JSON at line {line_number}: {path}"
            ) from exc
        if not isinstance(row, Mapping):
            raise ValidationError(
                f"annotation manifest line {line_number} must be an object"
            )
        digest = str(row.get("sha256", "")).strip().lower()
        if not digest:
            raise ValidationError(
                f"annotation manifest line {line_number} has no sha256"
            )
        if digest in result:
            raise ValidationError(
                f"annotation manifest contains duplicate sha256: {digest}"
            )
        result[digest] = dict(row)
    return result


def _largest_bbox(
    raw_bboxes: Sequence[object],
    width: int,
    height: int,
    source_path: Path,
) -> tuple[int, int, int, int]:
    bboxes: list[tuple[int, int, int, int]] = []
    for raw_bbox in raw_bboxes:
        if not isinstance(raw_bbox, Sequence) or isinstance(raw_bbox, (str, bytes)):
            raise ValidationError(f"invalid reviewed bbox for {source_path}")
        if len(raw_bbox) != 4:
            raise ValidationError(f"invalid reviewed bbox for {source_path}")
        try:
            bbox = tuple(int(value) for value in raw_bbox)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"invalid reviewed bbox for {source_path}") from exc
        x1, y1, x2, y2 = bbox
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ValidationError(
                f"reviewed bbox is outside {width}x{height}: {source_path} {bbox}"
            )
        bboxes.append(bbox)
    return max(
        bboxes,
        key=lambda value: (value[2] - value[0]) * (value[3] - value[1]),
    )


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


def _load_mapping(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValidationError(f"configuration must be a JSON object: {path}")
    return dict(value)


def _resolve_project_path(value: object) -> Path | None:
    if value is None:
        return None
    path = Path(str(value))
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


if __name__ == "__main__":
    main()
