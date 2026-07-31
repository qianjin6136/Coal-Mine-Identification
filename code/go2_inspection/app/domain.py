"""API、存储层和检测流水线共用的领域对象及输入校验。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import math
import re
from typing import Any, Mapping, Sequence

from .errors import ValidationError


# capture_id 会参与本地目录命名，因此只允许跨平台文件系统中安全的字符。
_CAPTURE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _finite_optional_float(value: Any, name: str) -> float | None:
    """把可选输入转为有限浮点数，拒绝 NaN 和无穷大进入持久化层。"""

    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be a number or null") from exc
    if not math.isfinite(result):
        raise ValidationError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class RobotPose:
    """采集时机器人在指定坐标系中的二维位姿。"""

    frame: str = "map"
    x_m: float | None = None
    y_m: float | None = None
    yaw_deg: float | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "RobotPose":
        """从外部字典构造位姿，并统一完成类型转换和边界校验。"""

        data = data or {}
        frame = str(data.get("frame", "map")).strip() or "map"
        if len(frame) > 32:
            raise ValidationError("robot_pose.frame is too long")
        return cls(
            frame=frame,
            x_m=_finite_optional_float(data.get("x_m"), "robot_pose.x_m"),
            y_m=_finite_optional_float(data.get("y_m"), "robot_pose.y_m"),
            yaw_deg=_finite_optional_float(data.get("yaw_deg"), "robot_pose.yaw_deg"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame": self.frame,
            "x_m": self.x_m,
            "y_m": self.y_m,
            "yaw_deg": self.yaw_deg,
        }


@dataclass(frozen=True)
class CaptureMetadata:
    """一次抓拍任务的元数据；图片内容由服务层按名称顺序另行接收。"""

    capture_id: str
    capture_time: str
    station_id: str
    robot_pose: RobotPose
    camera_id: str
    image_names: tuple[str, ...]
    batch_id: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CaptureMetadata":
        """校验上传字段，并转换为后续各层可直接信任的领域对象。"""

        capture_id = str(data.get("capture_id", "")).strip()
        if not _CAPTURE_ID_RE.fullmatch(capture_id):
            raise ValidationError(
                "capture_id must be 1-128 characters using letters, digits, '.', '_' or '-'"
            )

        capture_time = str(data.get("capture_time", "")).strip()
        if not capture_time:
            raise ValidationError("capture_time is required")
        try:
            # Python 的 fromisoformat 不直接识别所有版本中的 Z 后缀，先转为 UTC 偏移。
            datetime.fromisoformat(capture_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError("capture_time must be ISO-8601") from exc

        station_id = str(data.get("station_id", "")).strip()
        if not station_id or len(station_id) > 64:
            raise ValidationError("station_id is required and must be at most 64 characters")

        camera_id = str(data.get("camera_id", "go2_front")).strip()
        if not camera_id or len(camera_id) > 64:
            raise ValidationError("camera_id must be 1-64 characters")

        batch_value = data.get("batch_id")
        batch_id = str(batch_value).strip() if batch_value is not None else None
        if batch_id == "":
            batch_id = None
        if batch_id is not None and len(batch_id) > 128:
            raise ValidationError("batch_id must be at most 128 characters")

        raw_images = data.get("images", ())
        if not isinstance(raw_images, Sequence) or isinstance(raw_images, (str, bytes)):
            raise ValidationError("images must be a list of file names")
        image_names = tuple(str(name).strip() for name in raw_images if str(name).strip())
        if not 1 <= len(image_names) <= 5:
            raise ValidationError("images must contain between 1 and 5 file names")
        if len(set(image_names)) != len(image_names):
            raise ValidationError("images must not contain duplicate file names")

        return cls(
            capture_id=capture_id,
            capture_time=capture_time,
            station_id=station_id,
            robot_pose=RobotPose.from_mapping(data.get("robot_pose")),
            camera_id=camera_id,
            image_names=image_names,
            batch_id=batch_id,
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> "CaptureMetadata":
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValidationError("metadata must be valid JSON") from exc
        if not isinstance(data, Mapping):
            raise ValidationError("metadata JSON must be an object")
        return cls.from_mapping(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capture_id": self.capture_id,
            "capture_time": self.capture_time,
            "station_id": self.station_id,
            "robot_pose": self.robot_pose.to_dict(),
            "camera_id": self.camera_id,
            "images": list(self.image_names),
            "batch_id": self.batch_id,
        }


@dataclass(frozen=True)
class BoundingBox:
    """采用 ``(左, 上, 右, 下)`` 即 xyxy 格式表示的目标框。"""

    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        values = (self.x1, self.y1, self.x2, self.y2)
        if not all(math.isfinite(value) for value in values):
            raise ValidationError("bounding box values must be finite")
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValidationError("bounding box must have positive width and height")

    @classmethod
    def from_sequence(cls, values: Sequence[Any]) -> "BoundingBox":
        if len(values) != 4:
            raise ValidationError("bbox_xyxy must contain four numbers")
        try:
            converted = [float(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise ValidationError("bbox_xyxy must contain four numbers") from exc
        return cls(*converted)

    def to_list(self) -> list[float]:
        return [self.x1, self.y1, self.x2, self.y2]

    def iou(self, other: "BoundingBox") -> float:
        """计算两个目标框的交并比，用于跨帧目标关联。"""

        left = max(self.x1, other.x1)
        top = max(self.y1, other.y1)
        right = min(self.x2, other.x2)
        bottom = min(self.y2, other.y2)
        intersection = max(0.0, right - left) * max(0.0, bottom - top)
        if intersection == 0:
            return 0.0
        own_area = (self.x2 - self.x1) * (self.y2 - self.y1)
        other_area = (other.x2 - other.x1) * (other.y2 - other.y1)
        return intersection / (own_area + other_area - intersection)


@dataclass
class Detection:
    """检测器输出的统一目标结构，与具体模型后端解耦。"""

    type: str
    class_id: str
    class_cn: str
    bbox: BoundingBox
    confidence: float
    attributes: dict[str, Any] = field(default_factory=dict)
    source_frame: int | None = None

    def __post_init__(self) -> None:
        if not self.type or not self.class_id:
            raise ValidationError("detection type and class_id are required")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValidationError("detection confidence must be in [0, 1]")
        self.confidence = float(self.confidence)

    def to_dict(self, location_text: str | None = None) -> dict[str, Any]:
        """转换为 API/数据库使用的扁平结构，并按需补充区域描述。"""

        result = {
            "type": self.type,
            "class": self.class_id,
            "class_cn": self.class_cn,
            "bbox_xyxy": self.bbox.to_list(),
            "confidence": round(self.confidence, 6),
            **self.attributes,
        }
        if location_text is not None:
            result["location_text"] = location_text
        return result


@dataclass
class InspectionResult:
    """一次抓拍完成推理后需要持久化和返回的汇总结果。"""

    capture_id: str
    station_id: str
    capture_pose: RobotPose
    objects: list[dict[str, Any]]
    modules: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    annotated_image: str | None = None
    processing_parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capture_id": self.capture_id,
            "station_id": self.station_id,
            "capture_pose": self.capture_pose.to_dict(),
            "objects": self.objects,
            "modules": self.modules,
            "warnings": self.warnings,
            "annotated_image": self.annotated_image,
            "processing_parameters": self.processing_parameters,
        }
