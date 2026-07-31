"""基于监督模板的红色七段数字表识别。

当前样本只有 22 张，同一表型、每张 4 个数字。相比直接微调大模型，先从已知
读数中提取逐位模板并做留一图片评估更可控；新增现场样本后仍可重复训练模板，
也可以在不改变模块接口的情况下替换为 CNN/CRNN。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
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
        digit_count: int = 4,
        decimal_places: int = 1,
        allow_negative: bool = True,
        minimum_confidence: float = 0.55,
    ) -> None:
        self.model = model
        self.digit_count = digit_count
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
            expected_digit_count=self.digit_count,
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
    digit_count = int(format_config.get("digit_count", 4))
    decimal_places = int(format_config.get("decimal_places", 1))
    allow_negative = bool(format_config.get("allow_negative", True))
    project_root = Path(__file__).resolve().parents[2]
    templates: list[object] = []
    labels: list[str] = []
    sources: list[str] = []
    sample_rows: list[dict[str, Any]] = []
    for item in config.get("samples", []):
        if not isinstance(item, Mapping):
            raise ValidationError("each digital sample must be an object")
        source_value = str(item.get("file", ""))
        source_path = Path(source_value)
        if not source_path.is_absolute():
            source_path = (project_root / source_path).resolve()
        # 文件名是人工确认的真值；配置中的 text 仅用于显式校验，避免旧标签静默污染模型。
        expected_text = source_path.stem.strip()
        configured_text = str(item.get("text", expected_text)).strip()
        if configured_text != expected_text:
            raise ValidationError(
                f"digital sample filename and configured text disagree: "
                f"{source_path.name} != {configured_text}"
            )
        expected_digits = expected_text.lstrip("-").replace(".", "")
        if len(expected_digits) != digit_count or not expected_digits.isdigit():
            raise ValidationError(
                f"sample text must contain {digit_count} digits: {expected_text}"
            )
        image = read_bgr_image(source_path)
        if image is None:
            raise ValidationError(f"digital sample cannot be read: {source_path}")
        segmentation = segment_display_digits(
            image,
            expected_digit_count=digit_count,
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
        digit_count=digit_count,
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
            "digit_count": digit_count,
            "decimal_places": decimal_places,
            "allow_negative": allow_negative,
        },
        "samples_detail": evaluations,
    }
    return model, metrics


def segment_display_digits(
    image: object,
    *,
    expected_digit_count: int = 4,
    allow_negative: bool = True,
) -> dict[str, Any]:
    """从整图红色区域提取逐位二值掩码，并检测小数点/负号。"""

    import cv2
    import numpy as np

    if image is None or not hasattr(image, "shape"):
        return _segmentation_error("invalid_image")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_or(
        cv2.inRange(hsv, (0, 80, 70), (15, 255, 255)),
        cv2.inRange(hsv, (165, 80, 70), (179, 255, 255)),
    )
    component_count, component_labels, stats, centroids = (
        cv2.connectedComponentsWithStats(mask, connectivity=8)
    )
    minimum_area = max(20, int(mask.shape[0] * mask.shape[1] * 0.000004))
    cleaned = np.zeros_like(mask)
    for index in range(1, component_count):
        if int(stats[index, cv2.CC_STAT_AREA]) >= minimum_area:
            cleaned[component_labels == index] = 255
    ys, xs = np.where(cleaned > 0)
    if not len(xs):
        return _segmentation_error("red_display_not_found")
    x1, x2 = int(xs.min()), int(xs.max()) + 1
    y1, y2 = int(ys.min()), int(ys.max()) + 1
    display = cleaned[y1:y2, x1:x2]
    display_height, display_width = display.shape

    # 小数点通常是靠近底部的小而近方形分量；先移除，再按 x 投影分割数字。
    decimal_centers: list[float] = []
    count, labels_map, local_stats, local_centroids = cv2.connectedComponentsWithStats(
        display,
        connectivity=8,
    )
    digit_only = display.copy()
    for index in range(1, count):
        width = int(local_stats[index, cv2.CC_STAT_WIDTH])
        height = int(local_stats[index, cv2.CC_STAT_HEIGHT])
        center_x, center_y = local_centroids[index]
        if (
            width < display_height * 0.22
            and height < display_height * 0.22
            and center_y > display_height * 0.68
            and 0.55 <= width / max(1, height) <= 1.8
        ):
            digit_only[labels_map == index] = 0
            decimal_centers.append(float(center_x))

    runs = _active_column_runs(digit_only)
    negative = False
    required_digit_count = expected_digit_count
    if allow_negative and runs:
        first_mask = digit_only[:, runs[0][0] : runs[0][1] + 1]
        if _looks_like_minus(first_mask) and len(runs) in {
            expected_digit_count,
            expected_digit_count + 1,
        }:
            negative = True
            # 最坏情况兼容两类设备：独立负号位 + 全部数字位，或负号占用最高数字位。
            if len(runs) == expected_digit_count:
                required_digit_count = expected_digit_count - 1
            runs = runs[1:]
    if len(runs) != required_digit_count:
        return _segmentation_error(
            f"expected_{required_digit_count}_digits_but_found_{len(runs)}",
            (x1, y1, x2, y2),
        )
    digit_masks = [
        digit_only[:, start : end + 1]
        for start, end in runs
    ]
    decimal_places: int | None = None
    if decimal_centers:
        dot_x = max(decimal_centers)
        decimal_places = sum(
            ((start + end) / 2.0) > dot_x for start, end in runs
        )
    return {
        "error": None,
        "digit_masks": digit_masks,
        "display_bbox": (x1, y1, x2, y2),
        "decimal_places": decimal_places,
        "negative": negative,
    }


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


def _active_column_runs(mask: object) -> list[tuple[int, int]]:
    import numpy as np

    projection = (mask > 0).sum(axis=0)
    active = projection >= max(2, int(mask.shape[0] * 0.008))
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(active):
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start >= 4:
                runs.append((start, index - 1))
            start = None
    if start is not None and len(active) - start >= 4:
        runs.append((start, len(active) - 1))
    return runs


def _looks_like_minus(mask: object) -> bool:
    import numpy as np

    ys, xs = np.where(mask > 0)
    if not len(xs):
        return False
    width = xs.max() - xs.min() + 1
    height = ys.max() - ys.min() + 1
    center_y = (ys.min() + ys.max()) / 2.0 / mask.shape[0]
    return width >= height * 2.2 and 0.3 <= center_y <= 0.7


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
