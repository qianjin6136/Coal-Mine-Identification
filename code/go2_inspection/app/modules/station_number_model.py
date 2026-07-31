"""蓝底白字工位编号牌的监督模板分类器。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
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

    def read(self, path: str | Path) -> StationNumberRecognition:
        image = read_bgr_image(path)
        if image is None:
            return _unreadable("image_cannot_be_read")
        return self.read_image(image)

    def read_image(self, image: object) -> StationNumberRecognition:
        segmentation = segment_station_number(image)
        if segmentation["error"]:
            return _unreadable(
                str(segmentation["error"]),
                segmentation.get("sign_bbox"),
            )
        number, confidence = self.model.predict(segmentation["feature"])
        if confidence < self.minimum_confidence:
            return StationNumberRecognition(
                status="unreadable",
                number=number,
                confidence=confidence,
                sign_bbox_xyxy=segmentation["sign_bbox"],
                reason="station_number_confidence_below_threshold",
            )
        return StationNumberRecognition(
            status="confirmed",
            number=number,
            confidence=confidence,
            sign_bbox_xyxy=segmentation["sign_bbox"],
            reason="station_number_template_confirmed",
        )


def train_station_number_model(
    samples_config_path: Path,
) -> tuple[StationNumberTemplateModel, dict[str, Any]]:
    """从以编号命名的样本中提取模板，并用确定性扰动做稳健性评估。"""

    import numpy as np

    config_path = Path(samples_config_path).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, Mapping):
        raise ValidationError("station number sample configuration must be an object")
    project_root = Path(__file__).resolve().parents[2]
    templates: list[object] = []
    labels: list[int] = []
    sources: list[str] = []
    source_images: list[tuple[int, str, object]] = []
    for configured_label, source_value in config.items():
        try:
            label = int(configured_label)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"station number label must be an integer: {configured_label}"
            ) from exc
        source_path = Path(str(source_value))
        if not source_path.is_absolute():
            source_path = (project_root / source_path).resolve()
        if source_path.stem != str(label):
            raise ValidationError(
                f"station sample filename must match its label: "
                f"{source_path.name} != {label}.png"
            )
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
    if len(set(labels)) != len(labels):
        raise ValidationError("station number sample labels must be unique")

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
    metrics = {
        "model_type": "supervised_station_number_templates",
        "samples": len(labels),
        "classes": sorted(labels),
        "training_accuracy": training_correct / len(labels) if labels else 0.0,
        "robustness_cases": robustness_total,
        "robustness_accuracy": (
            robustness_correct / robustness_total if robustness_total else 0.0
        ),
        "samples_detail": training_rows,
        "robustness_failures": robustness_failures,
    }
    return model, metrics


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
