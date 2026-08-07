"""现场样本的 OpenCV 启发式检测，供指示灯/彩布/指针表等模块复用。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..image_io import read_bgr_image


def _contour_boxes(
    mask: Any,
    *,
    min_area: float,
    max_area: float,
    aspect_range: tuple[float, float] = (0.2, 5.0),
    limit: int = 5,
) -> list[dict[str, Any]]:
    import cv2

    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, __import__("numpy").ones((3, 3), __import__("numpy").uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, __import__("numpy").ones((5, 5), __import__("numpy").uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[dict[str, Any]] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < min_area or area > max_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        aspect = w / max(h, 1)
        if not aspect_range[0] <= aspect <= aspect_range[1]:
            continue
        boxes.append(
            {
                "bbox_xyxy": [float(x), float(y), float(x + w), float(y + h)],
                "area": area,
            }
        )
    boxes.sort(key=lambda item: item["area"], reverse=True)
    return boxes[:limit]


def detect_station_markers(image_path: Path | str) -> list[dict[str, Any]]:
    """蓝色圆形编号牌粗定位，供编号读数 ROI 使用。"""

    import cv2

    image = read_bgr_image(str(image_path))
    if image is None:
        return []
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (95, 60, 60), (135, 255, 255))
    height, width = image.shape[:2]
    boxes = _contour_boxes(
        mask,
        min_area=width * height * 0.00035,
        max_area=width * height * 0.08,
        aspect_range=(0.55, 1.45),
        limit=8,
    )
    detections: list[dict[str, Any]] = []
    for box in boxes:
        area_ratio = box["area"] / float(width * height)
        confidence = min(0.92, 0.45 + area_ratio * 10.0)
        detections.append(
            {
                "type": "station_marker",
                "class": "station_marker",
                "class_cn": "编号牌",
                "confidence": round(confidence, 4),
                "bbox_xyxy": box["bbox_xyxy"],
            }
        )
    return detections


def detect_indicator_lights(image_path: Path | str) -> dict[str, Any]:
    """检测红/绿指示灯亮灭状态。"""

    import cv2
    import numpy as np

    image = read_bgr_image(str(image_path))
    if image is None:
        return {
            "red": {"on": False, "confidence": 0.0, "detections": []},
            "green": {"on": False, "confidence": 0.0, "detections": []},
            "reason": "image_unreadable",
        }
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    height, width = image.shape[:2]
    min_area = width * height * 0.00012
    max_area = width * height * 0.05
    red_mask = cv2.bitwise_or(
        cv2.inRange(hsv, (0, 100, 120), (12, 255, 255)),
        cv2.inRange(hsv, (165, 100, 120), (179, 255, 255)),
    )
    green_mask = cv2.inRange(hsv, (40, 80, 100), (95, 255, 255))
    result: dict[str, Any] = {}
    for name, mask in (("red", red_mask), ("green", green_mask)):
        boxes = _contour_boxes(
            mask,
            min_area=min_area,
            max_area=max_area,
            aspect_range=(0.45, 2.2),
            limit=2,
        )
        detections = []
        best_score = 0.0
        for box in boxes:
            x1, y1, x2, y2 = [int(v) for v in box["bbox_xyxy"]]
            roi = hsv[y1:y2, x1:x2]
            if roi.size == 0:
                continue
            mean_v = float(np.mean(roi[:, :, 2]))
            mean_s = float(np.mean(roi[:, :, 1]))
            score = min(1.0, (mean_v / 255.0) * 0.65 + (mean_s / 255.0) * 0.35)
            detections.append(
                {
                    "type": f"indicator_{name}",
                    "class": f"indicator_{name}",
                    "class_cn": "红色指示灯" if name == "red" else "绿色指示灯",
                    "confidence": round(score, 4),
                    "bbox_xyxy": box["bbox_xyxy"],
                }
            )
            best_score = max(best_score, score)
        on = best_score >= 0.55 and bool(detections)
        result[name] = {
            "on": on,
            "confidence": round(best_score if detections else 0.0, 4),
            "detections": detections,
        }
    return result


def detect_foreign_objects(image_path: Path | str) -> list[dict[str, Any]]:
    """检测颜色鲜艳的布条/异物。"""

    import cv2
    import numpy as np

    image = read_bgr_image(str(image_path))
    if image is None:
        return []
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    height, width = image.shape[:2]
    masks = [
        cv2.inRange(hsv, (5, 140, 140), (35, 255, 255)),
        cv2.inRange(hsv, (40, 110, 110), (90, 255, 255)),
        cv2.inRange(hsv, (145, 110, 110), (175, 255, 255)),
    ]
    mask = masks[0]
    for item in masks[1:]:
        mask = cv2.bitwise_or(mask, item)
    boxes = _contour_boxes(
        mask,
        min_area=width * height * 0.0012,
        max_area=width * height * 0.12,
        aspect_range=(0.2, 6.0),
        limit=5,
    )
    detections: list[dict[str, Any]] = []
    for box in boxes:
        x1, y1, x2, y2 = [int(v) for v in box["bbox_xyxy"]]
        w = x2 - x1
        h = y2 - y1
        if w > width * 0.45 or h > height * 0.45:
            continue
        if y1 < height * 0.05 and h > height * 0.3:
            continue
        # Floor mats / barriers near bottom edges are common false positives.
        if y1 > height * 0.72:
            continue
        roi = hsv[y1:y2, x1:x2]
        if roi.size == 0:
            continue
        mean_s = float(np.mean(roi[:, :, 1]))
        mean_v = float(np.mean(roi[:, :, 2]))
        if mean_s < 120 or mean_v < 120:
            continue
        area_ratio = box["area"] / float(width * height)
        confidence = min(0.95, 0.4 + area_ratio * 6.0 + mean_s / 255.0 * 0.25)
        if confidence < 0.55:
            continue
        detections.append(
            {
                "type": "foreign_object",
                "class": "foreign_object",
                "class_cn": "皮带异物/彩布",
                "confidence": round(confidence, 4),
                "bbox_xyxy": [float(x1), float(y1), float(x2), float(y2)],
            }
        )
    return detections


def detect_coal_piles(image_path: Path | str) -> list[dict[str, Any]]:
    """粗粒度堆煤区域检测（现场样本不足时的 CV 兜底）。"""

    import cv2

    image = read_bgr_image(str(image_path))
    if image is None:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    dark = cv2.inRange(gray, 0, 55)
    low_sat = cv2.inRange(hsv, (0, 0, 0), (179, 70, 70))
    mask = cv2.bitwise_and(dark, low_sat)
    height, width = image.shape[:2]
    boxes = _contour_boxes(
        mask,
        min_area=width * height * 0.012,
        max_area=width * height * 0.55,
        aspect_range=(0.4, 6.0),
        limit=2,
    )
    detections: list[dict[str, Any]] = []
    for box in boxes:
        x1, y1, x2, y2 = box["bbox_xyxy"]
        if y2 < height * 0.35:
            continue
        area_ratio = box["area"] / float(width * height)
        confidence = min(0.9, 0.4 + area_ratio * 3.0)
        detections.append(
            {
                "type": "coal_pile",
                "class": "coal_pile",
                "class_cn": "堆煤",
                "confidence": round(confidence, 4),
                "bbox_xyxy": box["bbox_xyxy"],
            }
        )
    return detections


def detect_analog_meters(image_path: Path | str) -> list[dict[str, Any]]:
    """圆形指针表外观检出（不读数）。"""

    import cv2
    import numpy as np

    image = read_bgr_image(str(image_path))
    if image is None:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 5)
    height, width = image.shape[:2]
    min_radius = int(min(width, height) * 0.12)
    max_radius = int(min(width, height) * 0.42)
    circles = cv2.HoughCircles(
        gray,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=min(width, height) // 3,
        param1=140,
        param2=55,
        minRadius=min_radius,
        maxRadius=max_radius,
    )
    if circles is None:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for cx, cy, radius in np.round(circles[0]).astype(int):
        x1 = float(max(0, cx - radius))
        y1 = float(max(0, cy - radius))
        x2 = float(min(width - 1, cx + radius))
        y2 = float(min(height - 1, cy + radius))
        roi = gray[int(y1) : int(y2), int(x1) : int(x2)]
        if roi.size == 0:
            continue
        mean_intensity = float(np.mean(roi))
        # Dial faces are bright; reject dark structural false circles.
        if mean_intensity < 90:
            continue
        center_dist = abs(cx - width / 2) / width + abs(cy - height / 2) / height
        confidence = min(
            0.95,
            0.35 + mean_intensity / 255.0 * 0.55 + max(0.0, 0.2 - center_dist) ,
        )
        scored.append(
            (
                confidence,
                {
                    "type": "analog_meter",
                    "class": "analog_meter",
                    "class_cn": "指针表",
                    "confidence": round(confidence, 4),
                    "bbox_xyxy": [x1, y1, x2, y2],
                    "value": None,
                    "raw_text": None,
                },
            )
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in scored[:1]]
