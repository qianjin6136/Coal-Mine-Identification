"""把检测目标和独立识别模块汇总为前端可直接展示的最终结果。"""

from __future__ import annotations

from typing import Any, Mapping


MODULE_NAMES = {
    "tool_and_safety_sign": "工具 / 安全标牌",
    "coal_presence": "煤堆检测",
    "station_number": "工位编号",
    "digital_meter": "数字表",
    "analog_meter": "指针表",
}

_PRIMARY_PRIORITY = {
    "station_number": 0,
    "digital_meter": 1,
    "analog_meter": 2,
    "coal_presence": 3,
    "tool_and_safety_sign": 4,
    "manual_review": 5,
    "yolo": 6,
    "json_replay": 7,
    "detector": 8,
}


def build_recognition_summary(
    *,
    capture_status: str,
    result: Mapping[str, Any] | None,
    error: str | None = None,
    manually_corrected: bool = False,
) -> dict[str, Any]:
    """生成不混淆“检测到目标”和“读出具体数值”的结构化摘要。"""

    if capture_status == "received":
        return {"status": "processing", "primary": None, "items": []}
    if capture_status == "failed":
        return {
            "status": "failed",
            "primary": None,
            "items": [],
            "error": error,
        }

    result = result if isinstance(result, Mapping) else {}
    items: list[dict[str, Any]] = []
    detector_source = _detector_source(result, manually_corrected)
    raw_objects = result.get("objects")
    if isinstance(raw_objects, list):
        for index, raw_object in enumerate(raw_objects):
            if not isinstance(raw_object, Mapping):
                continue
            label = str(
                raw_object.get("class_cn")
                or raw_object.get("class")
                or raw_object.get("type")
                or f"目标 {index + 1}"
            )
            class_id = str(
                raw_object.get("class") or raw_object.get("type") or "unknown"
            )
            items.append(
                {
                    "source_kind": detector_source["kind"],
                    "source_id": detector_source["id"],
                    "source_name": detector_source["name"],
                    "status": "recognized" if manually_corrected else "detected",
                    "raw_status": "manually_confirmed" if manually_corrected else "detected",
                    "result_type": str(raw_object.get("type") or class_id),
                    "label": label,
                    "value": class_id,
                    "display_value": label,
                    "confidence": _confidence(raw_object.get("confidence")),
                    "reason": None,
                }
            )

    raw_modules = result.get("modules")
    if isinstance(raw_modules, Mapping):
        for module_id, raw_module in raw_modules.items():
            if not isinstance(raw_module, Mapping):
                continue
            raw_status = str(raw_module.get("status") or "unknown")
            if raw_status == "disabled" or raw_module.get("enabled") is False:
                continue
            recognized = raw_status == "confirmed"
            value, display_value = _module_value(str(module_id), raw_module)
            items.append(
                {
                    "source_kind": "module",
                    "source_id": str(module_id),
                    "source_name": MODULE_NAMES.get(str(module_id), str(module_id)),
                    "status": "recognized" if recognized else "unrecognized",
                    "raw_status": raw_status,
                    "result_type": str(module_id),
                    "label": MODULE_NAMES.get(str(module_id), str(module_id)),
                    "value": value if recognized else None,
                    "display_value": display_value if recognized else "未识别成功",
                    "confidence": _confidence(raw_module.get("confidence")),
                    "reason": raw_module.get("reason"),
                }
            )

    successful = [
        item for item in items if item["status"] in {"recognized", "detected"}
    ]
    unsuccessful = [item for item in items if item["status"] == "unrecognized"]
    if successful and unsuccessful:
        summary_status = "partial"
    elif successful:
        summary_status = "recognized"
    else:
        summary_status = "unrecognized"

    primary = min(
        successful,
        key=lambda item: _PRIMARY_PRIORITY.get(str(item["source_id"]), 99),
        default=None,
    )
    return {"status": summary_status, "primary": primary, "items": items}


def _detector_source(
    result: Mapping[str, Any], manually_corrected: bool
) -> dict[str, str]:
    if manually_corrected:
        return {"kind": "manual", "id": "manual_review", "name": "人工复核"}
    parameters = result.get("processing_parameters")
    detector = parameters.get("detector") if isinstance(parameters, Mapping) else {}
    mode = detector.get("mode") if isinstance(detector, Mapping) else None
    if mode == "gpu":
        return {"kind": "detector", "id": "yolo", "name": "YOLO 目标检测"}
    if mode == "json_replay":
        return {"kind": "detector", "id": "json_replay", "name": "回放目标检测"}
    return {"kind": "detector", "id": "detector", "name": "目标检测"}


def _module_value(
    module_id: str, result: Mapping[str, Any]
) -> tuple[Any, str]:
    if module_id == "station_number":
        number = result.get("number")
        return number, f"{number} 号" if number is not None else "未识别成功"
    if module_id == "digital_meter":
        value = result.get("raw_text")
        if value is None:
            value = result.get("value")
        return value, str(value) if value is not None else "未识别成功"
    if module_id == "coal_presence":
        present = result.get("present")
        return present, "检测到煤堆" if present else "未检测到煤堆"
    if module_id == "tool_and_safety_sign":
        objects = result.get("objects")
        labels = [
            str(item.get("class_cn") or item.get("class") or item.get("type"))
            for item in objects or []
            if isinstance(item, Mapping)
        ]
        return labels, "、".join(labels) if labels else "未检测到工具 / 安全标牌"
    if module_id == "analog_meter":
        meters = result.get("meters")
        count = len(meters) if isinstance(meters, list) else 0
        return count, f"识别到 {count} 个指针表" if count else "未检测到指针表"
    for field in ("raw_text", "number", "value", "present"):
        if result.get(field) is not None:
            value = result[field]
            return value, str(value)
    return None, "识别完成"


def _confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None
    return round(confidence, 6)
