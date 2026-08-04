"""模拟仪表红色指针检测，以及相对参考角度的状态判定。"""

from __future__ import annotations

from dataclasses import dataclass
import math

from ..image_io import read_bgr_image

@dataclass(frozen=True)
class PointerStatus:
    """指针角度判定结果；内部角度字段保留供调试和标定使用。"""

    status: str
    confidence: float
    angle_deg: float | None
    reference_angle_deg: float
    delta_deg: float | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "confidence": round(self.confidence, 6),
            "angle_deg_internal": self.angle_deg,
            "reference_angle_deg_internal": self.reference_angle_deg,
            "delta_deg_internal": self.delta_deg,
            "reason": self.reason,
        }


def angular_distance_deg(first: float, second: float) -> float:
    """返回两个角度在圆周上的最短绝对距离。"""

    return abs((first - second + 180.0) % 360.0 - 180.0)


def classify_pointer_status(
    angle_deg: float | None,
    reference_angle_deg: float,
    tolerance_deg: float,
    detection_confidence: float,
    min_confidence: float = 0.55,
) -> PointerStatus:
    """结合检测置信度和允许偏差，把指针状态分为正常、异常或不确定。"""

    # 低置信度时不强行给出正常/异常结论，避免将检测失败误报为设备异常。
    if angle_deg is None or detection_confidence < min_confidence:
        return PointerStatus(
            status="uncertain",
            confidence=max(0.0, min(1.0, detection_confidence)),
            angle_deg=angle_deg,
            reference_angle_deg=reference_angle_deg,
            delta_deg=None,
            reason="pointer_not_reliably_detected",
        )
    if tolerance_deg <= 0:
        raise ValueError("tolerance_deg must be positive")
    delta = angular_distance_deg(angle_deg, reference_angle_deg)
    return PointerStatus(
        status="normal" if delta <= tolerance_deg else "abnormal",
        confidence=max(0.0, min(1.0, detection_confidence)),
        angle_deg=round(angle_deg, 4),
        reference_angle_deg=round(reference_angle_deg, 4),
        delta_deg=round(delta, 4),
        reason=(
            "pointer_angle_within_tolerance"
            if delta <= tolerance_deg
            else "pointer_angle_delta_exceeds_tolerance"
        ),
    )


def detect_red_pointer_angle(
    image_path: str,
    center_xy: tuple[float, float] | None = None,
) -> tuple[float | None, float]:
    """从经过透视矫正的仪表裁剪图中检测红色指针。

    返回值使用数学坐标系：0° 指向右侧，90° 指向上方。当前实现是针对红色
    指针的基线方案；黑色等深色指针需要在取得现场样本后单独标定。
    """
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("OpenCV is required for pointer image analysis") from exc

    image = read_bgr_image(image_path)
    if image is None:
        return None, 0.0
    return detect_red_pointer_angle_image(image, center_xy)


def detect_red_pointer_angle_image(
    image: object,
    center_xy: tuple[float, float] | None = None,
) -> tuple[float | None, float]:
    """从 OpenCV BGR 图像直接检测红色指针，供检测框 ROI 流水线调用。"""

    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("OpenCV is required for pointer image analysis") from exc

    if image is None or not hasattr(image, "shape"):
        return None, 0.0
    height, width = image.shape[:2]
    center = center_xy or (width / 2.0, height * 0.68)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    # HSV 的红色横跨色相环首尾，因此需要合并低色相和高色相两个区间。
    lower_red = cv2.inRange(hsv, (0, 70, 60), (15, 255, 255))
    upper_red = cv2.inRange(hsv, (165, 70, 60), (179, 255, 255))
    mask = cv2.bitwise_or(lower_red, upper_red)
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8)
    )
    # 在二值指针区域中寻找线段，阈值随图像尺寸缩放以适应不同裁剪分辨率。
    lines = cv2.HoughLinesP(
        mask,
        rho=1,
        theta=np.pi / 180,
        threshold=max(20, min(width, height) // 18),
        minLineLength=max(20, min(width, height) // 8),
        maxLineGap=max(6, min(width, height) // 30),
    )
    if lines is None:
        return None, 0.0

    cx, cy = center
    radius = min(width, height) * 0.48
    best: tuple[float, float, float] | None = None
    for x1, y1, x2, y2 in lines[:, 0]:
        endpoints = ((float(x1), float(y1)), (float(x2), float(y2)))
        distances = [math.hypot(x - cx, y - cy) for x, y in endpoints]
        near_index = 0 if distances[0] <= distances[1] else 1
        far_index = 1 - near_index
        near_distance = distances[near_index]
        far_distance = distances[far_index]
        # 合法指针线段应一端靠近表盘中心，另一端延伸到表盘外圈。
        if near_distance > radius * 0.38 or far_distance < radius * 0.35:
            continue
        far_x, far_y = endpoints[far_index]
        score = far_distance - near_distance
        if best is None or score > best[0]:
            # 图像 y 轴向下，使用 cy-far_y 翻转后才符合数学坐标系角度。
            angle = math.degrees(math.atan2(cy - far_y, far_x - cx)) % 360.0
            best = (score, angle, far_distance)
    if best is None:
        return None, 0.0
    # 指针越接近表盘外圈，几何证据越充分；此处给出简单的归一化置信度。
    confidence = min(1.0, max(0.0, best[2] / radius))
    return round(best[1], 4), round(confidence, 4)
