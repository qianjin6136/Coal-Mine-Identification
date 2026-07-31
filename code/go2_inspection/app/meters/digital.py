"""红色七段数码管识别，以及多帧读数投票。"""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from typing import Iterable

from ..image_io import read_bgr_image

# 七段管采用行业常用的 a-g 命名：a 为顶部横段，之后顺时针排列，g 为中段。
SEGMENT_TO_DIGIT = {
    frozenset("abcdef"): "0",
    frozenset("bc"): "1",
    frozenset("abdeg"): "2",
    frozenset("abcdg"): "3",
    frozenset("bcfg"): "4",
    frozenset("acdfg"): "5",
    frozenset("acdefg"): "6",
    frozenset("abc"): "7",
    frozenset("abcdefg"): "8",
    frozenset("abcdfg"): "9",
}


@dataclass(frozen=True)
class DigitalReading:
    """多帧投票后的数码表读数及可信度。"""

    status: str
    raw_text: str | None
    value: float | None
    confidence: float
    votes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "raw_text": self.raw_text,
            "value": self.value,
            "confidence": round(self.confidence, 6),
            "votes": self.votes,
        }


def decode_segments(active_segments: Iterable[str]) -> str | None:
    """把点亮的段集合映射为单个数字，未知组合返回 None。"""

    return SEGMENT_TO_DIGIT.get(frozenset(active_segments))


def majority_vote_readings(
    readings: Iterable[str | None],
    min_agreement: int = 2,
) -> DigitalReading:
    """对多帧 OCR 结果做多数投票，降低瞬时反光或遮挡造成的误读。"""

    values = [value.strip() for value in readings if value and value.strip()]
    if not values:
        return DigitalReading("unreadable", None, None, 0.0, 0)
    value, votes = Counter(values).most_common(1)[0]
    total = len(values)
    if votes < min_agreement:
        return DigitalReading("unreadable", None, None, votes / total, votes)
    try:
        numeric_value = float(value)
    except ValueError:
        numeric_value = None
    return DigitalReading("confirmed", value, numeric_value, votes / total, votes)


class SevenSegmentReader:
    """针对已矫正红色七段数码管裁剪图的 OpenCV 基线识别器。"""

    def __init__(self, activation_threshold: float = 0.32) -> None:
        self.activation_threshold = activation_threshold

    def read(self, image_path: str) -> str | None:
        """按从左到右的顺序识别全部数字及数字间的小数点。"""

        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("OpenCV is required for seven-segment image analysis") from exc

        image = read_bgr_image(image_path)
        if image is None:
            return None
        return self.read_image(image)

    def read_image(self, image: object) -> str | None:
        """从 OpenCV BGR 图像直接读取数码管，供检测框 ROI 流水线调用。"""

        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("OpenCV is required for seven-segment image analysis") from exc

        if image is None or not hasattr(image, "shape"):
            return None
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        # 与红色指针相同，红色在 HSV 色相轴上需要用两个区间共同表示。
        mask = cv2.bitwise_or(
            cv2.inRange(hsv, (0, 80, 70), (15, 255, 255)),
            cv2.inRange(hsv, (165, 80, 70), (179, 255, 255)),
        )
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8)
        )
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        height, width = mask.shape
        boxes = [cv2.boundingRect(contour) for contour in contours]
        # 先按相对尺寸排除小数点、噪点等小轮廓，留下主体数字候选框。
        digit_boxes = [
            box
            for box in boxes
            if box[3] >= height * 0.35 and box[2] >= width * 0.035
        ]
        digit_boxes.sort(key=lambda box: box[0])
        if not digit_boxes:
            return None

        decoded: list[str] = []
        for index, (x, y, w, h) in enumerate(digit_boxes):
            digit_mask = mask[y : y + h, x : x + w]
            digit = self._decode_digit(digit_mask)
            if digit is None:
                return None
            decoded.append(digit)
            if index < len(digit_boxes) - 1:
                next_x = digit_boxes[index + 1][0]
                # 小数点应位于相邻数字之间，且靠近当前数字的右下方。
                decimal_candidates = [
                    box
                    for box in boxes
                    if x + w * 0.65 <= box[0] <= next_x
                    and box[1] >= y + h * 0.65
                    and box[2] < w * 0.4
                    and box[3] < h * 0.35
                ]
                if decimal_candidates:
                    decoded.append(".")
        return "".join(decoded)

    def _decode_digit(self, mask: object) -> str | None:
        """按七个固定采样区的点亮比例解码单个数字。"""

        height, width = mask.shape
        thickness_x = max(1, int(width * 0.24))
        thickness_y = max(1, int(height * 0.16))
        middle_y = height // 2
        segments = {
            "a": (thickness_x, 0, width - thickness_x, thickness_y),
            "b": (
                width - thickness_x,
                thickness_y,
                width,
                middle_y - thickness_y // 2,
            ),
            "c": (
                width - thickness_x,
                middle_y + thickness_y // 2,
                width,
                height - thickness_y,
            ),
            "d": (
                thickness_x,
                height - thickness_y,
                width - thickness_x,
                height,
            ),
            "e": (
                0,
                middle_y + thickness_y // 2,
                thickness_x,
                height - thickness_y,
            ),
            "f": (0, thickness_y, thickness_x, middle_y - thickness_y // 2),
            "g": (
                thickness_x,
                middle_y - thickness_y // 2,
                width - thickness_x,
                middle_y + thickness_y // 2,
            ),
        }
        active: set[str] = set()
        for name, (x1, y1, x2, y2) in segments.items():
            region = mask[y1:y2, x1:x2]
            # 采样区中前景像素占比达到阈值，即认为对应笔段已点亮。
            if region.size and float((region > 0).mean()) >= self.activation_threshold:
                active.add(name)
        return decode_segments(active)
