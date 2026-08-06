"""从配置构建并执行全部独立巡检模块。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..domain import CaptureMetadata
from ..errors import ConfigurationError
from .analog_meter import AnalogMeterModule
from .base import InspectionModule, ModuleContext
from .coal_presence import CoalPresenceModule
from .digital_meter import DigitalMeterModule
from .station_number import StationNumberModule


class ModuleRegistry:
    """保持固定模块顺序并隔离每个模块的错误。"""

    def __init__(self, modules: Sequence[InspectionModule]) -> None:
        self.modules = tuple(modules)

    def describe(self, *, detector_configured: bool) -> dict[str, dict[str, Any]]:
        """返回无需抓拍即可查看的模块启用/就绪状态。"""

        summary: dict[str, dict[str, Any]] = {}
        for module in self.modules:
            config = getattr(module, "config", {})
            enabled = bool(config.get("enabled", True))
            status = "ready"
            reason: str | None = None
            if not enabled:
                status = "disabled"
                reason = str(config.get("reason", "disabled_by_configuration"))
            elif isinstance(module, AnalogMeterModule) and config.get("reason"):
                status = "unavailable"
                reason = str(config["reason"])
            elif isinstance(module, DigitalMeterModule) and module.recognizer is None:
                status = "unavailable"
                reason = "digital_meter_model_not_trained"
            elif isinstance(module, CoalPresenceModule) and not config.get(
                "model_ready", False
            ):
                status = "unavailable"
                reason = str(config.get("reason") or "coal_field_model_not_trained")
            elif isinstance(module, CoalPresenceModule) and not detector_configured:
                status = "unavailable"
                reason = "coal_detector_not_configured"
            elif (
                isinstance(module, StationNumberModule)
                and module.recognition_mode == "image_classifier"
                and module.recognizer is None
            ):
                status = "unavailable"
                reason = "station_image_classifier_not_trained"
            elif (
                isinstance(module, StationNumberModule)
                and module.recognition_mode != "image_classifier"
            ):
                status = "metadata_only"
                reason = "station_number_uses_capture_metadata"
            summary[module.module_id] = {
                "enabled": enabled,
                "status": status,
            }
            if reason is not None:
                summary[module.module_id]["reason"] = reason
        return summary

    def run(
        self,
        metadata: CaptureMetadata,
        image_paths: Sequence[Path],
        objects: Sequence[dict[str, Any]],
        *,
        detector_configured: bool,
    ) -> dict[str, dict[str, Any]]:
        context = ModuleContext(
            metadata=metadata,
            image_paths=image_paths,
            objects=objects,
            detector_configured=detector_configured,
        )
        results: dict[str, dict[str, Any]] = {}
        for module in self.modules:
            try:
                results[module.module_id] = module.run(context)
            except Exception as exc:
                # 一个小模块失败不能阻止其他模块保存结果。
                results[module.module_id] = {
                    "enabled": True,
                    "status": "failed",
                    "reason": str(exc),
                }
        return results


def build_module_registry(
    config_path: Path,
    project_root: Path,
) -> ModuleRegistry:
    try:
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"module configuration not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"invalid module configuration: {config_path}") from exc
    if not isinstance(config, Mapping):
        raise ConfigurationError("module configuration must be a JSON object")
    return build_module_registry_from_config(config, project_root)


def build_module_registry_from_config(
    config: Mapping[str, Any],
    project_root: Path,
) -> ModuleRegistry:
    """从已合并的配置快照构建模块，供运行时热更新使用。"""

    return ModuleRegistry(
        [
            CoalPresenceModule(config.get("coal_presence", {})),
            StationNumberModule(config.get("station_number", {}), project_root),
            DigitalMeterModule(config.get("digital_meter", {}), project_root),
            AnalogMeterModule(config.get("analog_meter", {})),
        ]
    )
