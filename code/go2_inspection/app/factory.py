"""根据 JSON 配置组装检测器、存储库、流水线和应用服务。"""

from __future__ import annotations

from .detectors.noop import NoopDetector
from .detectors.replay import JsonReplayDetector
from .errors import ConfigurationError
from .modules.registry import build_module_registry_from_config
from .pipeline import InspectionPipeline
from .runtime_settings import (
    RuntimeSettingsManager,
    build_runtime_defaults,
    merge_module_runtime_settings,
)
from .service import InspectionService
from .settings import Settings, load_json_mapping
from .storage import CaptureRepository


def build_service(settings_path: str | None = None) -> InspectionService:
    """构建完整服务对象，并在启动阶段尽早暴露配置错误。"""

    settings = Settings.load(settings_path)
    class_config = load_json_mapping(settings.classes_path)
    stations = load_json_mapping(settings.stations_path)
    base_module_config = load_json_mapping(settings.modules_path)
    runtime_settings = RuntimeSettingsManager(
        settings.storage_root / "runtime_settings.json",
        build_runtime_defaults(
            detector_confidence=settings.detector_confidence,
            fusion_iou=settings.fusion_iou,
            module_config=base_module_config,
        ),
    )
    runtime_snapshot = runtime_settings.snapshot()
    # 独立模块注册表负责决定哪些任务启用；当前指针表模块禁用，不加载或执行旧处理器。
    module_registry = build_module_registry_from_config(
        merge_module_runtime_settings(base_module_config, runtime_snapshot),
        settings.project_root,
    )

    if settings.detector_backend == "noop":
        detector = NoopDetector()
    elif settings.detector_backend == "json_replay":
        detector = JsonReplayDetector()
    elif settings.detector_backend == "ultralytics":
        if settings.detector_weights is None:
            raise ConfigurationError(
                "detector.weights is required for the ultralytics backend"
            )
        # 模型后端是可选能力，延迟导入可让无模型环境正常使用 noop/回放模式。
        from .detectors.ultralytics_backend import UltralyticsDetector

        detector = UltralyticsDetector(
            weights=settings.detector_weights,
            class_config=class_config,
            confidence=runtime_snapshot["detector"]["confidence"],
            config_dir=settings.storage_root,
        )
    else:
        raise ConfigurationError(
            f"unsupported detector backend: {settings.detector_backend}"
        )

    repository = CaptureRepository(
        database_path=settings.database_path,
        storage_root=settings.storage_root,
    )
    pipeline = InspectionPipeline(
        detector=detector,
        processed_root=repository.processed_root,
        stations=stations,
        fusion_iou=runtime_snapshot["pipeline"]["fusion_iou"],
        module_registry=module_registry,
    )

    def apply_runtime(values: dict[str, object]) -> None:
        detector_values = values["detector"]
        pipeline_values = values["pipeline"]
        if not isinstance(detector_values, dict) or not isinstance(
            pipeline_values, dict
        ):
            raise ConfigurationError("invalid runtime settings snapshot")
        if hasattr(detector, "confidence"):
            detector.confidence = float(detector_values["confidence"])
        pipeline.fusion_iou = float(pipeline_values["fusion_iou"])
        pipeline.module_registry = build_module_registry_from_config(
            merge_module_runtime_settings(base_module_config, values),
            settings.project_root,
        )

    return InspectionService(
        repository,
        pipeline,
        settings.max_image_bytes,
        runtime_settings=runtime_settings,
        apply_runtime_settings=apply_runtime,
    )
