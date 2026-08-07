"""运行时可调参数的校验、持久化与快照管理。"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from .errors import ConfigurationError, ValidationError


MODULE_IDS = (
    "station_number",
    "coal_presence",
    "foreign_object",
    "digital_meter",
    "indicator_lights",
    "analog_meter",
)

RETIRED_MODULE_IDS = {"tool_and_safety_sign"}

INFERENCE_MODES = {"noop", "gpu", "json_replay", "field_cv"}


class RuntimeSettingsManager:
    """维护一份可原子保存、可恢复默认值的运行时参数。"""

    def __init__(self, path: Path, defaults: Mapping[str, Any]) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self._defaults = self._validate_complete(defaults)
        self._current = self._load()

    @property
    def defaults(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._defaults)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._current)

    def update(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, Mapping):
            raise ValidationError("runtime settings payload must be an object")
        with self._lock:
            merged = self._merge_patch(self._current, patch)
            self._persist(merged)
            self._current = merged
            return deepcopy(merged)

    def reset(self) -> dict[str, Any]:
        with self._lock:
            restored = deepcopy(self._defaults)
            self._persist(restored)
            self._current = restored
            return deepcopy(restored)

    def replace(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        """完整替换当前值；供应用更新失败时回滚。"""

        with self._lock:
            validated = self._validate_complete(settings)
            self._persist(validated)
            self._current = validated
            return deepcopy(validated)

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return deepcopy(self._defaults)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(
                f"invalid runtime settings file: {self.path}"
            ) from exc
        if not isinstance(raw, Mapping):
            raise ConfigurationError("runtime settings must be a JSON object")
        try:
            migrated = deepcopy(dict(raw))
            raw_modules = migrated.get("modules")
            retired_present = False
            if isinstance(raw_modules, Mapping):
                modules = dict(raw_modules)
                retired_present = bool(RETIRED_MODULE_IDS.intersection(modules))
                for module_id in RETIRED_MODULE_IDS:
                    modules.pop(module_id, None)
                migrated["modules"] = modules
            loaded = self._merge_patch(self._defaults, migrated)
            if retired_present:
                self._persist(loaded)
            return loaded
        except ValidationError as exc:
            raise ConfigurationError(f"invalid runtime settings: {exc}") from exc

    def _persist(self, settings: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            raise ValidationError(f"cannot save runtime settings: {exc}") from exc

    def _validate_complete(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        expected = {
            "detector": {"confidence", "mode"},
            "pipeline": {"fusion_iou"},
            "digital_meter": {"minimum_frame_confidence"},
            "modules": set(MODULE_IDS),
        }
        if set(settings) != set(expected):
            raise ValidationError(
                "runtime settings must contain detector, pipeline, digital_meter and modules"
            )
        for section, keys in expected.items():
            value = settings.get(section)
            if not isinstance(value, Mapping) or set(value) != keys:
                raise ValidationError(
                    f"runtime settings section '{section}' has invalid fields"
                )
        return self._normalize(settings)

    def _merge_patch(
        self,
        base: Mapping[str, Any],
        patch: Mapping[str, Any],
    ) -> dict[str, Any]:
        allowed = {
            "detector": {"confidence", "mode"},
            "pipeline": {"fusion_iou"},
            "digital_meter": {"minimum_frame_confidence"},
            "modules": set(MODULE_IDS),
        }
        unknown_sections = set(patch) - set(allowed)
        if unknown_sections:
            raise ValidationError(
                "unknown runtime settings fields: "
                + ", ".join(sorted(unknown_sections))
            )
        merged = deepcopy(dict(base))
        for section, changes in patch.items():
            if not isinstance(changes, Mapping):
                raise ValidationError(f"{section} must be an object")
            unknown = set(changes) - allowed[section]
            if unknown:
                raise ValidationError(
                    f"unknown {section} fields: " + ", ".join(sorted(unknown))
                )
            merged[section].update(changes)
        return self._validate_complete(merged)

    @staticmethod
    def _normalize(settings: Mapping[str, Any]) -> dict[str, Any]:
        detector_mode = str(settings["detector"]["mode"]).strip()
        if detector_mode not in INFERENCE_MODES:
            raise ValidationError(
                "detector.mode must be noop, gpu or json_replay"
            )
        detector_confidence = _bounded_float(
            settings["detector"]["confidence"],
            "detector.confidence",
        )
        fusion_iou = _bounded_float(
            settings["pipeline"]["fusion_iou"],
            "pipeline.fusion_iou",
        )
        meter_confidence = _bounded_float(
            settings["digital_meter"]["minimum_frame_confidence"],
            "digital_meter.minimum_frame_confidence",
        )
        modules: dict[str, bool] = {}
        for module_id in MODULE_IDS:
            enabled = settings["modules"][module_id]
            if not isinstance(enabled, bool):
                raise ValidationError(f"modules.{module_id} must be a boolean")
            modules[module_id] = enabled
        return {
            "detector": {
                "mode": detector_mode,
                "confidence": detector_confidence,
            },
            "pipeline": {"fusion_iou": fusion_iou},
            "digital_meter": {
                "minimum_frame_confidence": meter_confidence,
            },
            "modules": modules,
        }


def build_runtime_defaults(
    *,
    detector_confidence: float,
    fusion_iou: float,
    module_config: Mapping[str, Any],
    detector_mode: str = "noop",
) -> dict[str, Any]:
    """从项目默认配置提取 UI 允许调整的安全子集。"""

    digital_config = module_config.get("digital_meter", {})
    return {
        "detector": {
            "mode": detector_mode,
            "confidence": detector_confidence,
        },
        "pipeline": {"fusion_iou": fusion_iou},
        "digital_meter": {
            "minimum_frame_confidence": float(
                digital_config.get("minimum_frame_confidence", 0.55)
            )
        },
        "modules": {
            module_id: bool(
                (
                    module_config.get(module_id, {})
                    if isinstance(module_config.get(module_id, {}), Mapping)
                    else {}
                ).get("enabled", True)
            )
            for module_id in MODULE_IDS
        },
    }


def merge_module_runtime_settings(
    base_config: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> dict[str, Any]:
    """把受控运行时覆盖项合入完整模块配置。"""

    merged = deepcopy(dict(base_config))
    for module_id, enabled in runtime["modules"].items():
        module = merged.setdefault(module_id, {})
        if not isinstance(module, dict):
            module = {}
            merged[module_id] = module
        module["enabled"] = bool(enabled)
    digital = merged.setdefault("digital_meter", {})
    if not isinstance(digital, dict):
        digital = {}
        merged["digital_meter"] = digital
    digital["minimum_frame_confidence"] = runtime["digital_meter"][
        "minimum_frame_confidence"
    ]
    return merged


def _bounded_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValidationError(f"{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be a number") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValidationError(f"{name} must be between 0 and 1")
    return number
