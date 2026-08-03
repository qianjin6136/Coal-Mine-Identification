"""蓝底白字工位编号牌的监督模板分类器。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from ..errors import ConfigurationError, ValidationError
from ..image_io import read_bgr_image


@dataclass(frozen=True)
class StationNumberRecognition:
    """单张图片的编号牌识别结果。"""

    status: str
    number: int | None
    confidence: float
    sign_bbox_xyxy: tuple[int, int, int, int] | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "number": self.number,
            "confidence": round(self.confidence, 6),
            "sign_bbox_xyxy": (
                list(self.sign_bbox_xyxy) if self.sign_bbox_xyxy else None
            ),
            "reason": self.reason,
        }


class StationNumberTemplateModel:
    """保存 1～10 号白色字形模板，并使用带平移容差的 Dice 距离分类。"""

    def __init__(
        self,
        templates: object,
        labels: Sequence[int],
        sources: Sequence[str],
    ) -> None:
        import numpy as np

        values = np.asarray(templates, dtype=np.uint8)
        if values.ndim != 3 or values.shape[1:] != (96, 96):
            raise ValidationError(
                "station number templates must have shape (N, 96, 96)"
            )
        if len(values) != len(labels) or len(values) != len(sources):
            raise ValidationError(
                "station number labels and sources must have equal length"
            )
        if not len(values):
            raise ValidationError("at least one station number template is required")
        self.templates = (values > 0).astype(np.uint8)
        self.labels = tuple(int(value) for value in labels)
        self.sources = tuple(str(value) for value in sources)

    def predict(self, feature: object) -> tuple[int, float]:
        """返回编号和置信度。"""

        import numpy as np

        query = np.asarray(feature, dtype=np.uint8)
        if query.shape != (96, 96):
            raise ValidationError("station number feature must have shape (96, 96)")
        query = (query > 0).astype(np.uint8)
        distances = sorted(
            (
                _dice_distance_with_shift(query, template),
                index,
            )
            for index, template in enumerate(self.templates)
        )
        best_distance, best_index = distances[0]
        best_label = self.labels[best_index]
        second_distance = next(
            (
                distance
                for distance, index in distances[1:]
                if self.labels[index] != best_label
            ),
            1.0,
        )
        margin = max(0.0, second_distance - best_distance)
        confidence = max(
            0.0,
            min(1.0, 0.70 * (1.0 - best_distance) + 0.30 * min(1.0, 2 * margin)),
        )
        return best_label, confidence

    def save(self, path: Path) -> Path:
        import numpy as np

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            templates=self.templates,
            labels=np.asarray(self.labels, dtype=np.int16),
            sources=np.asarray(self.sources),
            model_version=np.asarray(["station-number-template-v1"]),
        )
        return path

    @classmethod
    def load(cls, path: Path) -> "StationNumberTemplateModel":
        import numpy as np

        path = Path(path)
        if not path.is_file():
            raise ConfigurationError(f"station number model not found: {path}")
        with np.load(path, allow_pickle=False) as payload:
            return cls(
                payload["templates"],
                payload["labels"].tolist(),
                payload["sources"].tolist(),
            )


class StationNumberRecognizer:
    """定位蓝色圆牌并识别其中的白色编号。"""

    def __init__(
        self,
        model: StationNumberTemplateModel,
        *,
        minimum_confidence: float = 0.55,
    ) -> None:
        self.model = model
        self.minimum_confidence = minimum_confidence

    def read(
        self,
        path: str | Path,
        roi_bbox_xyxy: Sequence[float] | None = None,
    ) -> StationNumberRecognition:
        image = read_bgr_image(path)
        if image is None:
            return _unreadable("image_cannot_be_read")
        return self.read_image(image, roi_bbox_xyxy=roi_bbox_xyxy)

    def read_image(
        self,
        image: object,
        roi_bbox_xyxy: Sequence[float] | None = None,
    ) -> StationNumberRecognition:
        target_image = image
        offset = (0, 0)
        if roi_bbox_xyxy is not None:
            cropped = _crop_station_roi(image, roi_bbox_xyxy)
            if cropped is None:
                return _unreadable("invalid_station_marker_bbox")
            target_image, offset = cropped
        segmentation = segment_station_number(target_image)
        sign_bbox = _translate_bbox(segmentation.get("sign_bbox"), offset)
        if segmentation["error"]:
            return _unreadable(
                str(segmentation["error"]),
                sign_bbox,
            )
        number, confidence = self.model.predict(segmentation["feature"])
        if confidence < self.minimum_confidence:
            return StationNumberRecognition(
                status="unreadable",
                number=number,
                confidence=confidence,
                sign_bbox_xyxy=sign_bbox,
                reason="station_number_confidence_below_threshold",
            )
        return StationNumberRecognition(
            status="confirmed",
            number=number,
            confidence=confidence,
            sign_bbox_xyxy=sign_bbox,
            reason="station_number_template_confirmed",
        )


def train_station_number_model(
    samples_config_path: Path,
) -> tuple[StationNumberTemplateModel, dict[str, Any]]:
    """从以编号命名的样本中提取模板，并用确定性扰动做稳健性评估。"""

    import numpy as np

    configured_samples = load_station_number_samples(samples_config_path)
    templates: list[object] = []
    labels: list[int] = []
    sources: list[str] = []
    source_images: list[tuple[int, str, object]] = []
    for label, source_path in configured_samples:
        image = read_bgr_image(source_path)
        if image is None:
            raise ValidationError(f"station number sample cannot be read: {source_path}")
        segmentation = segment_station_number(image)
        if segmentation["error"]:
            raise ValidationError(
                f"{source_path.name}: {segmentation['error']}"
            )
        templates.append(segmentation["feature"])
        labels.append(label)
        sources.append(str(source_path))
        source_images.append((label, str(source_path), image))

    model = StationNumberTemplateModel(np.asarray(templates), labels, sources)
    recognizer = StationNumberRecognizer(model, minimum_confidence=0.0)
    training_rows: list[dict[str, Any]] = []
    training_correct = 0
    robustness_correct = 0
    robustness_total = 0
    robustness_failures: list[dict[str, Any]] = []
    for expected, source, image in source_images:
        result = recognizer.read_image(image)
        correct = result.number == expected
        training_correct += int(correct)
        training_rows.append(
            {
                "file": source,
                "expected": expected,
                "predicted": result.number,
                "confidence": result.confidence,
                "correct": correct,
            }
        )
        for variant_name, variant in _robustness_variants(image):
            variant_result = recognizer.read_image(variant)
            variant_correct = variant_result.number == expected
            robustness_correct += int(variant_correct)
            robustness_total += 1
            if not variant_correct:
                robustness_failures.append(
                    {
                        "file": source,
                        "variant": variant_name,
                        "expected": expected,
                        "predicted": variant_result.number,
                        "reason": variant_result.reason,
                    }
                )
    class_counts = {
        label: labels.count(label)
        for label in sorted(set(labels))
    }
    validation_rows: list[dict[str, Any]] = []
    validation_failures: list[dict[str, Any]] = []
    validation_correct = 0
    validation_robustness_correct = 0
    validation_robustness_total = 0
    validation_robustness_failures: list[dict[str, Any]] = []
    for excluded_index, (expected, source, image) in enumerate(source_images):
        if class_counts[expected] < 2:
            continue
        validation_model = StationNumberTemplateModel(
            np.delete(model.templates, excluded_index, axis=0),
            [
                label
                for index, label in enumerate(model.labels)
                if index != excluded_index
            ],
            [
                item
                for index, item in enumerate(model.sources)
                if index != excluded_index
            ],
        )
        validation_recognizer = StationNumberRecognizer(
            validation_model,
            minimum_confidence=0.55,
        )
        result = validation_recognizer.read_image(image)
        correct = result.status == "confirmed" and result.number == expected
        validation_correct += int(correct)
        validation_row = {
            "file": source,
            "expected": expected,
            "predicted": result.number,
            "status": result.status,
            "confidence": result.confidence,
            "correct": correct,
        }
        validation_rows.append(validation_row)
        if not correct:
            validation_failures.append(validation_row)
        for variant_name, variant in _robustness_variants(image):
            variant_result = validation_recognizer.read_image(variant)
            variant_correct = (
                variant_result.status == "confirmed"
                and variant_result.number == expected
            )
            validation_robustness_correct += int(variant_correct)
            validation_robustness_total += 1
            if not variant_correct:
                validation_robustness_failures.append(
                    {
                        "file": source,
                        "variant": variant_name,
                        "expected": expected,
                        "predicted": variant_result.number,
                        "status": variant_result.status,
                        "confidence": variant_result.confidence,
                        "reason": variant_result.reason,
                    }
                )
    metrics = {
        "model_type": "supervised_station_number_templates",
        "samples": len(labels),
        "classes": sorted(set(labels)),
        "samples_per_class": {
            str(label): count for label, count in class_counts.items()
        },
        "training_accuracy": training_correct / len(labels) if labels else 0.0,
        "robustness_cases": robustness_total,
        "robustness_accuracy": (
            robustness_correct / robustness_total if robustness_total else 0.0
        ),
        "samples_detail": training_rows,
        "robustness_failures": robustness_failures,
        "validation_method": "leave_one_source_out",
        "validation_minimum_confidence": 0.55,
        "validation_samples": len(validation_rows),
        "validation_accuracy": (
            validation_correct / len(validation_rows) if validation_rows else None
        ),
        "validation_samples_detail": validation_rows,
        "validation_failures": validation_failures,
        "validation_robustness_cases": validation_robustness_total,
        "validation_robustness_accuracy": (
            validation_robustness_correct / validation_robustness_total
            if validation_robustness_total
            else None
        ),
        "validation_robustness_failures": validation_robustness_failures,
    }
    return model, metrics


def load_station_number_samples(
    samples_config_path: Path,
) -> list[tuple[int, Path]]:
    """加载旧版单文件映射或按数字子目录自动发现的多样本配置。"""

    config_path = Path(samples_config_path).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, Mapping):
        raise ValidationError("station number sample configuration must be an object")
    project_root = Path(__file__).resolve().parents[2]
    if "dataset_root" in config:
        return _discover_station_number_samples(config, project_root)

    samples: list[tuple[int, Path]] = []
    for configured_label, source_value in config.items():
        try:
            label = int(configured_label)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"station number label must be an integer: {configured_label}"
            ) from exc
        values = (
            list(source_value)
            if isinstance(source_value, Sequence)
            and not isinstance(source_value, (str, bytes))
            else [source_value]
        )
        for value in values:
            source_path = _resolve_station_path(value, project_root)
            if source_path.is_dir():
                directory_samples = _image_files(source_path, _default_extensions())
                if not directory_samples:
                    raise ValidationError(
                        f"station sample directory contains no images: {source_path}"
                    )
                samples.extend((label, path) for path in directory_samples)
                continue
            if len(values) == 1 and source_path.stem != str(label):
                raise ValidationError(
                    f"station sample filename must match its label: "
                    f"{source_path.name} != {label}{source_path.suffix}"
                )
            _validate_sample_filename(label, source_path)
            samples.append((label, source_path))
    return _validate_discovered_samples(samples)


def _discover_station_number_samples(
    config: Mapping[str, Any],
    project_root: Path,
) -> list[tuple[int, Path]]:
    dataset_root = _resolve_station_path(config["dataset_root"], project_root)
    if not dataset_root.is_dir():
        raise ValidationError(
            f"station number dataset root is not a directory: {dataset_root}"
        )
    extensions_value = config.get("extensions", list(_default_extensions()))
    if not isinstance(extensions_value, Sequence) or isinstance(
        extensions_value, (str, bytes)
    ):
        raise ValidationError("station number extensions must be an array")
    extensions = {
        str(value).lower()
        if str(value).startswith(".")
        else f".{str(value).lower()}"
        for value in extensions_value
    }
    excluded_value = config.get("exclude", [])
    if not isinstance(excluded_value, Sequence) or isinstance(
        excluded_value, (str, bytes)
    ):
        raise ValidationError("station number exclude must be an array")
    excluded_paths = {
        (dataset_root / str(value)).resolve()
        for value in excluded_value
    }
    missing_exclusions = [path for path in excluded_paths if not path.is_file()]
    if missing_exclusions:
        raise ValidationError(
            f"excluded station number sample does not exist: "
            f"{missing_exclusions[0]}"
        )
    configured_labels = config.get("labels")
    if configured_labels is None:
        label_directories = sorted(
            (
                (int(path.name), path)
                for path in dataset_root.iterdir()
                if path.is_dir() and path.name.isdigit()
            ),
            key=lambda item: item[0],
        )
    else:
        if not isinstance(configured_labels, Sequence) or isinstance(
            configured_labels, (str, bytes)
        ):
            raise ValidationError("station number labels must be an array")
        label_directories = [
            (int(value), dataset_root / str(int(value)))
            for value in configured_labels
        ]
    if not label_directories:
        raise ValidationError(
            f"station number dataset has no numeric class directories: {dataset_root}"
        )

    samples: list[tuple[int, Path]] = []
    for label, directory in label_directories:
        if not directory.is_dir():
            raise ValidationError(
                f"station number class directory is missing: {directory}"
            )
        class_samples = [
            path
            for path in _image_files(directory, extensions)
            if path not in excluded_paths
        ]
        if not class_samples:
            raise ValidationError(
                f"station number class directory contains no images: {directory}"
            )
        for source_path in class_samples:
            _validate_sample_filename(label, source_path)
            samples.append((label, source_path))
    return _validate_discovered_samples(samples)


def _resolve_station_path(value: object, project_root: Path) -> Path:
    source_path = Path(str(value))
    if not source_path.is_absolute():
        source_path = (project_root / source_path).resolve()
    return source_path


def _default_extensions() -> set[str]:
    return {".jpg", ".jpeg", ".png"}


def _image_files(directory: Path, extensions: set[str]) -> list[Path]:
    return sorted(
        (
            path.resolve()
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in extensions
        ),
        key=lambda path: _natural_sort_key(path.name),
    )


def _natural_sort_key(value: str) -> tuple[tuple[int, object], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", value)
    )


def _validate_sample_filename(label: int, source_path: Path) -> None:
    leading_number = re.match(r"^(\d+)(?:\D|$)", source_path.stem)
    if leading_number and int(leading_number.group(1)) != label:
        raise ValidationError(
            f"station sample filename disagrees with its class directory: "
            f"{source_path.name} is not label {label}"
        )


def _validate_discovered_samples(
    samples: list[tuple[int, Path]],
) -> list[tuple[int, Path]]:
    if not samples:
        raise ValidationError("at least one station number sample is required")
    missing = [str(path) for _, path in samples if not path.is_file()]
    if missing:
        raise ValidationError(f"station number sample does not exist: {missing[0]}")
    paths = [path for _, path in samples]
    if len(set(paths)) != len(paths):
        raise ValidationError("station number sample paths must be unique")
    return samples


def segment_station_number(image: object) -> dict[str, Any]:
    """定位蓝色牌体，并提取牌体中央的白色数字二值特征。"""

    import cv2
    import numpy as np

    if image is None or not hasattr(image, "shape"):
        return _segmentation_error("invalid_image")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, (85, 80, 35), (140, 255, 255))
    component_count, component_labels, stats, _ = cv2.connectedComponentsWithStats(
        blue,
        connectivity=8,
    )
    image_area = int(image.shape[0] * image.shape[1])
    minimum_area = max(150, int(image_area * 0.00015))
    candidates: list[tuple[int, int, int, int, int, int]] = []
    for index in range(1, component_count):
        x, y, width, height, area = (
            int(value) for value in stats[index]
        )
        aspect = width / max(1, height)
        fill_ratio = area / max(1, width * height)
        if (
            area >= minimum_area
            and 0.65 <= aspect <= 1.20
            and 0.25 <= fill_ratio <= 0.85
        ):
            candidates.append((area, index, x, y, width, height))
    if not candidates:
        return _segmentation_error("blue_station_sign_not_found")

    _, _, x, y, width, height = max(candidates)
    sign_hsv = hsv[y : y + height, x : x + width]
    white = cv2.inRange(sign_hsv, (0, 0, 135), (179, 115, 255))
    yy, xx = np.ogrid[:height, :width]
    central_ellipse = (
        ((xx - width * 0.5) / max(1.0, width * 0.38)) ** 2
        + ((yy - height * 0.5) / max(1.0, height * 0.40)) ** 2
        <= 1.0
    )
    white[~central_ellipse] = 0
    count, labels_map, local_stats, _ = cv2.connectedComponentsWithStats(
        white,
        connectivity=8,
    )
    cleaned = np.zeros_like(white)
    minimum_character_area = max(20, int(width * height * 0.0008))
    for index in range(1, count):
        if int(local_stats[index, cv2.CC_STAT_AREA]) >= minimum_character_area:
            cleaned[labels_map == index] = 255
    white_ratio = float((cleaned > 0).sum()) / max(1, width * height)
    bbox = (x, y, x + width, y + height)
    if not 0.005 <= white_ratio <= 0.30:
        return _segmentation_error("white_station_number_not_found", bbox)
    feature = cv2.resize(
        cleaned,
        (96, 96),
        interpolation=cv2.INTER_NEAREST,
    )
    return {
        "error": None,
        "feature": (feature > 0).astype(np.uint8),
        "sign_bbox": bbox,
    }


def _crop_station_roi(
    image: object,
    bbox_xyxy: Sequence[float],
) -> tuple[object, tuple[int, int]] | None:
    """把检测框裁剪到图像范围内，并返回用于还原全图坐标的偏移。"""

    if image is None or not hasattr(image, "shape"):
        return None
    try:
        values = [float(value) for value in bbox_xyxy]
    except (TypeError, ValueError):
        return None
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        return None
    height, width = image.shape[:2]
    x1 = max(0, min(width, math.floor(values[0])))
    y1 = max(0, min(height, math.floor(values[1])))
    x2 = max(0, min(width, math.ceil(values[2])))
    y2 = max(0, min(height, math.ceil(values[3])))
    if x2 <= x1 or y2 <= y1:
        return None
    return image[y1:y2, x1:x2], (x1, y1)


def _translate_bbox(
    bbox: tuple[int, int, int, int] | None,
    offset: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    if bbox is None:
        return None
    offset_x, offset_y = offset
    x1, y1, x2, y2 = bbox
    return x1 + offset_x, y1 + offset_y, x2 + offset_x, y2 + offset_y


def _robustness_variants(image: object) -> list[tuple[str, object]]:
    """生成未加入模板的亮度、模糊和小角度扰动评估图。"""

    import cv2

    height, width = image.shape[:2]
    variants = [
        ("original", image),
        ("dark_65_percent", cv2.convertScaleAbs(image, alpha=0.65, beta=0)),
        ("bright_115_percent", cv2.convertScaleAbs(image, alpha=1.15, beta=20)),
        ("gaussian_blur", cv2.GaussianBlur(image, (7, 7), 1.4)),
    ]
    for angle in (-2.0, 2.0):
        matrix = cv2.getRotationMatrix2D(
            (width / 2.0, height / 2.0),
            angle,
            1.0,
        )
        variants.append(
            (
                f"rotation_{angle:+.1f}_degrees",
                cv2.warpAffine(
                    image,
                    matrix,
                    (width, height),
                    borderMode=cv2.BORDER_REPLICATE,
                ),
            )
        )
    return variants


def _dice_distance_with_shift(query: object, template: object) -> float:
    import numpy as np

    query_values = np.asarray(query, dtype=np.uint8)
    template_values = np.asarray(template, dtype=np.uint8)
    best = 1.0
    for shift_y, shift_x in (
        (0, 0),
        (-3, 0),
        (3, 0),
        (0, -3),
        (0, 3),
        (-2, -2),
        (2, 2),
        (-2, 2),
        (2, -2),
    ):
        shifted = np.zeros_like(query_values)
        source_y1 = max(0, -shift_y)
        source_y2 = min(96, 96 - shift_y)
        source_x1 = max(0, -shift_x)
        source_x2 = min(96, 96 - shift_x)
        destination_y1 = max(0, shift_y)
        destination_x1 = max(0, shift_x)
        shifted[
            destination_y1 : destination_y1 + (source_y2 - source_y1),
            destination_x1 : destination_x1 + (source_x2 - source_x1),
        ] = query_values[source_y1:source_y2, source_x1:source_x2]
        intersection = int((shifted & template_values).sum())
        total = int(shifted.sum() + template_values.sum())
        distance = 1.0 - (2.0 * intersection / total if total else 0.0)
        best = min(best, distance)
    return best


def _segmentation_error(
    reason: str,
    bbox: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    return {
        "error": reason,
        "feature": None,
        "sign_bbox": bbox,
    }


def _unreadable(
    reason: str,
    bbox: tuple[int, int, int, int] | None = None,
) -> StationNumberRecognition:
    return StationNumberRecognition(
        status="unreadable",
        number=None,
        confidence=0.0,
        sign_bbox_xyxy=bbox,
        reason=reason,
    )
