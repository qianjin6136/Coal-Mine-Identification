"""把结构化检测结果绘制为便于复核的证据图。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence


def annotate_image(
    source_path: Path,
    destination_path: Path,
    objects: Sequence[dict[str, Any]],
) -> Path:
    """在原图副本上绘制目标框、类别和置信度，并保存为 JPEG。"""

    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError("Pillow is required for annotated evidence images") from exc

    with Image.open(source_path) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    # 线宽随图像尺寸缩放，兼顾低分辨率预览和高分辨率现场照片。
    line_width = max(2, round(min(width, height) / 350))
    for item in objects:
        bbox = item.get("bbox_xyxy")
        if not bbox or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = (float(value) for value in bbox)
        color = _color_for_type(str(item.get("type", "unknown")))
        draw.rectangle((x1, y1, x2, y2), outline=color, width=line_width)
        label = str(item.get("class_cn") or item.get("class") or item.get("type"))
        confidence = item.get("confidence")
        if isinstance(confidence, (int, float)):
            label = f"{label} {confidence:.2f}"
        text_bbox = draw.textbbox((x1, y1), label)
        text_height = text_bbox[3] - text_bbox[1]
        text_y = max(0, y1 - text_height - 4)
        draw.rectangle(
            (x1, text_y, x1 + text_bbox[2] - text_bbox[0] + 4, y1),
            fill=color,
        )
        draw.text((x1 + 2, text_y + 1), label, fill=(255, 255, 255))

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination_path, quality=92)
    return destination_path


def _color_for_type(object_type: str) -> tuple[int, int, int]:
    return {
        "coal_pile": (255, 140, 0),
        "analog_meter": (148, 0, 211),
        "digital_meter": (220, 20, 60),
        "station_marker": (0, 160, 70),
        "foreign_object": (255, 0, 128),
        "indicator_red": (220, 20, 60),
        "indicator_green": (0, 180, 80),
    }.get(object_type, (255, 215, 0))
