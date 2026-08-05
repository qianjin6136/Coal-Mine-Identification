"""基于监督模板的红色七段数字表识别。

当前以真实现场照片训练逐位模板，支持三位和四位显示。相比直接微调大模型，
从已知读数中提取逐位模板并做留一图片评估更可控；样本继续增加后，也可以在
不改变模块接口的情况下替换为 CNN/CRNN。
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from ..errors import ConfigurationError, ValidationError
from ..image_io import read_bgr_image


@dataclass(frozen=True)
class DigitalRecognition:
    """单张图片的数字表识别结果和可诊断字段。"""

    status: str
    raw_text: str | None
    value: float | None
    confidence: float
    digit_confidences: tuple[float, ...]
    display_bbox_xyxy: tuple[int, int, int, int] | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "raw_text": self.raw_text,
            "value": self.value,
            "confidence": round(self.confidence, 6),
            "digit_confidences": [
                round(value, 6) for value in self.digit_confidences
            ],
            "display_bbox_xyxy": (
                list(self.display_bbox_xyxy) if self.display_bbox_xyxy else None
            ),
            "reason": self.reason,
        }


class TemplateDigitModel:
    """保存归一化数字模板，并通过带微小平移容差的 Dice 距离分类。"""

    def __init__(
        self,
        templates: object,
        labels: Sequence[str],
        sources: Sequence[str],
    ) -> None:
        import numpy as np

        values = np.asarray(templates, dtype=np.uint8)
        if values.ndim != 3 or values.shape[1:] != (96, 64):
            raise ValidationError("digit templates must have shape (N, 96, 64)")
        if len(values) != len(labels) or len(values) != len(sources):
            raise ValidationError("template labels and sources must have equal length")
        if not len(values):
            raise ValidationError("at least one digit template is required")
        self.templates = values
        self.labels = tuple(str(value) for value in labels)
        self.sources = tuple(str(value) for value in sources)

    def predict(
        self,
        digit_mask: object,
        *,
        exclude_source: str | None = None,
    ) -> tuple[str, float]:
        """返回数字和置信度；留一评估时可排除同源图片全部模板。"""

        import numpy as np

        query = normalize_digit_mask(digit_mask)
        candidates = [
            index
            for index, source in enumerate(self.sources)
            if exclude_source is None or source != exclude_source
        ]
        if not candidates:
            raise ValidationError("no templates remain after source exclusion")
        distances = [
            (_dice_distance_with_shift(query, self.templates[index]), index)
            for index in candidates
        ]
        distances.sort(key=lambda item: item[0])
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
            min(1.0, 0.65 * (1.0 - best_distance) + 0.35 * margin),
        )
        return best_label, confidence

    def save(self, path: Path) -> Path:
        import numpy as np

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            templates=self.templates,
            labels=np.asarray(self.labels),
            sources=np.asarray(self.sources),
            model_version=np.asarray(["digital-template-v1"]),
        )
        return path

    @classmethod
    def load(cls, path: Path) -> "TemplateDigitModel":
        import numpy as np

        path = Path(path)
        if not path.is_file():
            raise ConfigurationError(f"digital meter model not found: {path}")
        with np.load(path, allow_pickle=False) as payload:
            return cls(
                payload["templates"],
                payload["labels"].tolist(),
                payload["sources"].tolist(),
            )


class DigitalMeterRecognizer:
    """检测红色显示区、分割数字、分类并恢复小数点与负号。"""

    def __init__(
        self,
        model: TemplateDigitModel,
        *,
        digit_count: int | Sequence[int] = 4,
        decimal_places: int = 1,
        allow_negative: bool = True,
        minimum_confidence: float = 0.55,
    ) -> None:
        self.model = model
        if isinstance(digit_count, int):
            digit_counts = (digit_count,)
        else:
            digit_counts = tuple(sorted({int(value) for value in digit_count}))
        if not digit_counts or any(value < 1 for value in digit_counts):
            raise ValidationError("digit count must contain positive integers")
        self.digit_counts = digit_counts
        # 保留旧属性，避免已有调用方读取单一位数配置时失效。
        self.digit_count = digit_counts[0]
        self.decimal_places = decimal_places
        self.allow_negative = allow_negative
        self.minimum_confidence = minimum_confidence

    def read(self, path: str | Path) -> DigitalRecognition:
        image = read_bgr_image(path)
        if image is None:
            return _unreadable("image_cannot_be_read")
        return self.read_image(image)

    def read_image(
        self,
        image: object,
        *,
        exclude_source: str | None = None,
    ) -> DigitalRecognition:
        segmentation = segment_display_digits(
            image,
            expected_digit_count=self.digit_counts,
            allow_negative=self.allow_negative,
        )
        if segmentation["error"]:
            return _unreadable(
                str(segmentation["error"]),
                segmentation.get("display_bbox"),
            )
        digits: list[str] = []
        confidences: list[float] = []
        for mask in segmentation["digit_masks"]:
            digit, confidence = self.model.predict(
                mask,
                exclude_source=exclude_source,
            )
            digits.append(digit)
            confidences.append(confidence)
        if not digits:
            return _unreadable(
                "no_digits_segmented",
                segmentation.get("display_bbox"),
            )
        decimal_places = int(
            segmentation.get("decimal_places")
            if segmentation.get("decimal_places") is not None
            else self.decimal_places
        )
        if decimal_places < 0 or decimal_places >= len(digits):
            return _unreadable(
                "invalid_decimal_position",
                segmentation.get("display_bbox"),
                confidences,
            )
        integer = "".join(digits[:-decimal_places]) if decimal_places else "".join(digits)
        fraction = "".join(digits[-decimal_places:]) if decimal_places else ""
        text = integer + (f".{fraction}" if decimal_places else "")
        if segmentation.get("negative"):
            text = "-" + text
        text = _remove_leading_zero_padding(text)
        confidence = min(confidences)
        if confidence < self.minimum_confidence:
            return DigitalRecognition(
                status="unreadable",
                raw_text=text,
                value=None,
                confidence=confidence,
                digit_confidences=tuple(confidences),
                display_bbox_xyxy=segmentation.get("display_bbox"),
                reason="digit_confidence_below_threshold",
            )
        return DigitalRecognition(
            status="confirmed",
            raw_text=text,
            value=float(text),
            confidence=confidence,
            digit_confidences=tuple(confidences),
            display_bbox_xyxy=segmentation.get("display_bbox"),
            reason="template_digits_confirmed",
        )


def train_template_model(
    samples_config_path: Path,
) -> tuple[TemplateDigitModel, dict[str, Any]]:
    """从带整串读数标签的图片中提取逐位模板并完成留一图片评估。"""

    import numpy as np

    config_path = Path(samples_config_path).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, Mapping):
        raise ValidationError("digital sample configuration must be an object")
    format_config = config.get("format", {})
    digit_counts = configured_digit_counts(format_config)
    decimal_places = int(format_config.get("decimal_places", 1))
    allow_negative = bool(format_config.get("allow_negative", True))
    project_root = Path(__file__).resolve().parents[2]
    templates: list[object] = []
    labels: list[str] = []
    sources: list[str] = []
    sample_rows: list[dict[str, Any]] = []
    skipped_unreadable_files: list[str] = []
    sample_items = expand_digital_sample_items(config, project_root)
    for item in sample_items:
        if not isinstance(item, Mapping):
            raise ValidationError("each digital sample must be an object")
        source_value = str(item.get("file", ""))
        source_path = Path(source_value)
        if not source_path.is_absolute():
            source_path = (project_root / source_path).resolve()
        # 文件名是人工确认的真值；配置中的 text 仅用于显式校验，避免旧标签静默污染模型。
        filename_text = source_path.stem.strip()
        normalized_filename_text = _label_from_sample_filename(source_path)
        configured_text = str(item.get("text", normalized_filename_text)).strip()
        if configured_text not in {filename_text, normalized_filename_text}:
            raise ValidationError(
                f"digital sample filename and configured text disagree: "
                f"{source_path.name} != {configured_text}"
            )
        expected_text = configured_text
        expected_digits = expected_text.lstrip("-").replace(".", "")
        if len(expected_digits) not in digit_counts or not expected_digits.isdigit():
            raise ValidationError(
                f"sample text must contain one of {digit_counts} digits: "
                f"{expected_text}"
            )
        image = read_bgr_image(source_path)
        if image is None:
            if bool(config.get("discovery", {}).get("skip_unreadable", False)):
                skipped_unreadable_files.append(str(source_path))
                continue
            raise ValidationError(f"digital sample cannot be read: {source_path}")
        segmentation = segment_display_digits(
            image,
            expected_digit_count=len(expected_digits),
            allow_negative=allow_negative,
        )
        if segmentation["error"]:
            raise ValidationError(
                f"{source_path.name}: {segmentation['error']}"
            )
        digit_masks = segmentation["digit_masks"]
        if len(digit_masks) != len(expected_digits):
            raise ValidationError(
                f"{source_path.name}: expected {len(expected_digits)} digit crops, "
                f"got {len(digit_masks)}"
            )
        source_key = str(source_path)
        for mask, label in zip(digit_masks, expected_digits):
            templates.append(normalize_digit_mask(mask))
            labels.append(label)
            sources.append(source_key)
        sample_rows.append(
            {
                "file": source_key,
                "expected": expected_text,
                "detected_decimal_places": segmentation.get("decimal_places"),
                "display_bbox_xyxy": list(segmentation["display_bbox"]),
            }
        )
    model = TemplateDigitModel(np.asarray(templates), labels, sources)
    recognizer = DigitalMeterRecognizer(
        model,
        digit_count=digit_counts,
        decimal_places=decimal_places,
        allow_negative=allow_negative,
        minimum_confidence=0.0,
    )
    digit_correct = 0
    digit_total = 0
    string_correct = 0
    evaluations: list[dict[str, Any]] = []
    for row in sample_rows:
        result = recognizer.read_image(
            read_bgr_image(row["file"]),
            exclude_source=row["file"],
        )
        expected = row["expected"]
        predicted = result.raw_text
        expected_digits = expected.lstrip("-").replace(".", "")
        predicted_digits = (predicted or "").lstrip("-").replace(".", "")
        digit_correct += sum(
            left == right
            for left, right in zip(expected_digits, predicted_digits)
        )
        digit_total += len(expected_digits)
        string_correct += int(predicted == expected)
        evaluations.append(
            {
                "file": row["file"],
                "expected": expected,
                "predicted": predicted,
                "confidence": result.confidence,
                "correct": predicted == expected,
            }
        )
    metrics = {
        "model_type": "supervised_template_digits",
        "samples": len(sample_rows),
        "discovered_files": len(sample_items),
        "skipped_unreadable_files": skipped_unreadable_files,
        "digit_templates": len(labels),
        "digit_accuracy_leave_one_image_out": (
            digit_correct / digit_total if digit_total else 0.0
        ),
        "string_accuracy_leave_one_image_out": (
            string_correct / len(sample_rows) if sample_rows else 0.0
        ),
        "digit_class_counts": {
            digit: labels.count(digit) for digit in "0123456789"
        },
        "format": {
            "digit_counts": list(digit_counts),
            "decimal_places": decimal_places,
            "allow_negative": allow_negative,
        },
        "samples_detail": evaluations,
    }
    return model, metrics


def segment_display_digits(
    image: object,
    *,
    expected_digit_count: int | Sequence[int] = 4,
    allow_negative: bool = True,
) -> dict[str, Any]:
    """从整图红色区域提取逐位二值掩码，并检测小数点/负号。"""

    import cv2
    if image is None or not hasattr(image, "shape"):
        return _segmentation_error("invalid_image")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_or(
        cv2.inRange(hsv, (0, 90, 80), (15, 255, 255)),
        cv2.inRange(hsv, (165, 90, 80), (179, 255, 255)),
    )
    component_count, _labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )
    if component_count <= 1:
        return _segmentation_error("red_display_not_found")
    expected_counts = (
        (expected_digit_count,)
        if isinstance(expected_digit_count, int)
        else tuple(sorted({int(value) for value in expected_digit_count}))
    )
    selection = _select_digit_components(
        mask,
        stats,
        centroids,
        expected_counts=expected_counts,
        allow_negative=allow_negative,
    )
    if selection is None:
        candidate_count = len(_digit_component_candidates(mask, stats))
        return _segmentation_error(
            f"expected_{'_or_'.join(str(value) for value in expected_counts)}"
            f"_digits_but_found_{candidate_count}",
        )
    selected, negative = selection
    selected = sorted(selected, key=lambda item: item[0])
    digit_masks = [
        mask[y : y + height, x : x + width]
        for x, y, width, height, _area, _density, _index in selected
    ]
    x1 = min(item[0] for item in selected)
    y1 = min(item[1] for item in selected)
    x2 = max(item[0] + item[2] for item in selected)
    y2 = max(item[1] + item[3] for item in selected)
    decimal_places = _detect_decimal_places(stats, centroids, selected)
    return {
        "error": None,
        "digit_masks": digit_masks,
        "display_bbox": (x1, y1, x2, y2),
        "decimal_places": decimal_places,
        "negative": negative,
    }


def configured_digit_counts(format_config: Mapping[str, Any]) -> tuple[int, ...]:
    values = format_config.get("digit_counts")
    if values is None:
        values = (format_config.get("digit_count", 4),)
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        values = (values,)
    result = tuple(sorted({int(value) for value in values}))
    if not result or any(value < 1 for value in result):
        raise ValidationError("digital format digit counts must be positive")
    return result


def expand_digital_sample_items(
    config: Mapping[str, Any],
    project_root: Path,
) -> list[dict[str, Any]]:
    items = [dict(item) for item in config.get("samples", [])]
    discovery = config.get("discovery")
    if not isinstance(discovery, Mapping):
        return items
    directory = Path(str(discovery.get("directory", "")))
    if not directory.is_absolute():
        directory = (project_root / directory).resolve()
    pattern = str(discovery.get("glob", "*.jpg"))
    existing = {
        str(Path(str(item.get("file", ""))).resolve())
        for item in items
        if item.get("file")
    }
    for path in sorted(directory.glob(pattern)):
        if not path.is_file() or str(path.resolve()) in existing:
            continue
        items.append(
            {
                "file": str(path.resolve()),
                "text": _label_from_sample_filename(path),
            }
        )
    return items


def _label_from_sample_filename(path: Path) -> str:
    """去掉资源管理器复制后缀，但保留文件名中的完整数字真值。"""

    return re.sub(r"(?:[（(]\d+[）)]|_)+$", "", path.stem.strip())


def _remove_leading_zero_padding(text: str) -> str:
    sign = "-" if text.startswith("-") else ""
    unsigned = text.lstrip("-")
    integer, dot, fraction = unsigned.partition(".")
    integer = integer.lstrip("0") or "0"
    return sign + integer + (dot + fraction if dot else "")


def _digit_component_candidates(
    mask: object,
    stats: object,
) -> list[tuple[Any, ...]]:
    import cv2

    image_height, image_width = mask.shape
    minimum_area = max(80, int(image_height * image_width * 0.00015))
    candidates: list[tuple[Any, ...]] = []
    for index in range(1, len(stats)):
        x = int(stats[index, cv2.CC_STAT_LEFT])
        y = int(stats[index, cv2.CC_STAT_TOP])
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        area = int(stats[index, cv2.CC_STAT_AREA])
        density = area / max(1, width * height)
        if (
            area >= minimum_area
            and image_height * 0.065 <= height <= image_height * 0.80
            and 0.07 <= width / max(1, height) <= 1.65
            and density >= 0.20
        ):
            candidates.append((x, y, width, height, area, density, index))
    return candidates


def _select_digit_components(
    mask: object,
    stats: object,
    centroids: object,
    *,
    expected_counts: Sequence[int],
    allow_negative: bool,
) -> tuple[tuple[tuple[Any, ...], ...], bool] | None:
    import numpy as np

    candidates = _digit_component_candidates(mask, stats)
    best: tuple[float, tuple[tuple[Any, ...], ...], bool] | None = None
    possible_counts = set(expected_counts)
    if allow_negative:
        possible_counts.update(value - 1 for value in expected_counts if value > 1)
    for count in sorted(possible_counts):
        for group_value in combinations(candidates, count):
            group = tuple(sorted(group_value, key=lambda item: item[0]))
            heights = np.asarray([item[3] for item in group], dtype=float)
            median_height = float(np.median(heights))
            centers_x = np.asarray(
                [item[0] + item[2] / 2.0 for item in group], dtype=float
            )
            centers_y = np.asarray(
                [item[1] + item[3] / 2.0 for item in group], dtype=float
            )
            steps = np.diff(centers_x)
            if (
                heights.max() / heights.min() > 2.25
                or centers_y.max() - centers_y.min() > median_height * 1.05
                or (len(steps) and steps.min() < median_height * 0.18)
                or (len(steps) and steps.max() > median_height * 2.05)
            ):
                continue
            minus = _find_minus_component(stats, centroids, group)
            negative = allow_negative and minus is not None
            if count not in expected_counts:
                if not negative or count + 1 not in expected_counts:
                    continue
            alignment_penalty = (
                centers_y.max() - centers_y.min()
            ) / median_height
            regularity_penalty = (
                float(np.std(steps) / median_height) if len(steps) > 1 else 0.0
            )
            score = sum(item[4] for item in group) * (
                1.0 - 0.10 * alignment_penalty - 0.06 * regularity_penalty
            )
            if best is None or score > best[0]:
                best = (score, group, negative)
    return None if best is None else (best[1], best[2])


def _find_minus_component(
    stats: object,
    centroids: object,
    digits: Sequence[tuple[Any, ...]],
) -> int | None:
    import cv2

    first_x = min(item[0] for item in digits)
    top = min(item[1] for item in digits)
    bottom = max(item[1] + item[3] for item in digits)
    digit_height = bottom - top
    for index in range(1, len(stats)):
        x = int(stats[index, cv2.CC_STAT_LEFT])
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        area = int(stats[index, cv2.CC_STAT_AREA])
        center_x, center_y = centroids[index]
        if (
            center_x < first_x
            and first_x - center_x < digit_height * 1.6
            and top + digit_height * 0.25 <= center_y <= top + digit_height * 0.75
            and width >= max(4, height * 2.2)
            and area >= max(12, digit_height * 0.08)
            and x + width <= first_x + digit_height * 0.15
        ):
            return index
    return None


def _detect_decimal_places(
    stats: object,
    centroids: object,
    digits: Sequence[tuple[Any, ...]],
) -> int | None:
    import cv2
    import numpy as np

    digit_indices = {item[6] for item in digits}
    median_height = float(np.median([item[3] for item in digits]))
    top = min(item[1] for item in digits)
    bottom = max(item[1] + item[3] for item in digits)
    left = min(item[0] for item in digits)
    right = max(item[0] + item[2] for item in digits)
    candidates: list[tuple[int, float]] = []
    for index in range(1, len(stats)):
        if index in digit_indices:
            continue
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        area = int(stats[index, cv2.CC_STAT_AREA])
        center_x, center_y = centroids[index]
        if (
            area >= 8
            and width <= median_height * 0.30
            and height <= median_height * 0.30
            and 0.4 <= width / max(1, height) <= 2.5
            and left <= center_x <= right
            and top + median_height * 0.62 <= center_y <= bottom + median_height * 0.30
        ):
            candidates.append((area, float(center_x)))
    if not candidates:
        return None
    dot_x = max(candidates)[1]
    places = sum(item[0] + item[2] / 2.0 > dot_x for item in digits)
    return places if 0 < places < len(digits) else None


def normalize_digit_mask(mask: object) -> object:
    """把单个数字等比缩放到 64×96 画布，保留“1”的窄宽特征。"""

    import cv2
    import numpy as np

    values = np.asarray(mask, dtype=np.uint8)
    ys, xs = np.where(values > 0)
    canvas = np.zeros((96, 64), dtype=np.uint8)
    if not len(xs):
        return canvas
    crop = values[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    scale = min(56 / max(1, crop.shape[1]), 88 / max(1, crop.shape[0]))
    target_width = max(1, round(crop.shape[1] * scale))
    target_height = max(1, round(crop.shape[0] * scale))
    resized = cv2.resize(
        crop,
        (target_width, target_height),
        interpolation=cv2.INTER_NEAREST,
    )
    left = (64 - target_width) // 2
    top = (96 - target_height) // 2
    canvas[top : top + target_height, left : left + target_width] = resized
    return (canvas > 0).astype(np.uint8)


def _dice_distance_with_shift(query: object, template: object) -> float:
    import numpy as np

    query_values = np.asarray(query, dtype=np.uint8)
    template_values = np.asarray(template, dtype=np.uint8)
    best = 1.0
    for shift_y, shift_x in (
        (0, 0),
        (-2, 0),
        (2, 0),
        (0, -2),
        (0, 2),
        (-1, -1),
        (1, 1),
    ):
        shifted = np.zeros_like(query_values)
        source_y1 = max(0, -shift_y)
        source_y2 = min(96, 96 - shift_y)
        source_x1 = max(0, -shift_x)
        source_x2 = min(64, 64 - shift_x)
        destination_y1 = max(0, shift_y)
        destination_y2 = destination_y1 + (source_y2 - source_y1)
        destination_x1 = max(0, shift_x)
        destination_x2 = destination_x1 + (source_x2 - source_x1)
        shifted[
            destination_y1:destination_y2,
            destination_x1:destination_x2,
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
        "digit_masks": [],
        "display_bbox": bbox,
        "decimal_places": None,
        "negative": False,
    }


def _unreadable(
    reason: str,
    bbox: tuple[int, int, int, int] | None = None,
    confidences: Sequence[float] = (),
) -> DigitalRecognition:
    return DigitalRecognition(
        status="unreadable",
        raw_text=None,
        value=None,
        confidence=min(confidences) if confidences else 0.0,
        digit_confidences=tuple(confidences),
        display_bbox_xyxy=bbox,
        reason=reason,
    )
