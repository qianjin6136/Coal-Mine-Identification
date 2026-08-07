"""根据 JSON 配置组装检测器、存储库、流水线和应用服务。"""

from __future__ import annotations

from .detectors.noop import NoopDetector
from .detectors.replay import JsonReplayDetector
from .errors import ConfigurationError
from .inference import (
    detector_runtime_mode,
    gpu_inference_status,
    runtime_mode_for_backend,
)
from .modules.registry import build_module_registry_from_config
from .offline_import import OfflineBatchManager
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
            detector_mode=runtime_mode_for_backend(settings.detector_backend),
        ),
    )
    runtime_snapshot = runtime_settings.snapshot()
    # 独立模块注册表负责决定哪些任务启用；当前指针表模块禁用，不加载或执行旧处理器。
    module_registry = build_module_registry_from_config(
        merge_module_runtime_settings(base_module_config, runtime_snapshot),
        settings.project_root,
    )

    def build_detector(mode: str, confidence: float):
        if mode == "noop":
            return NoopDetector()
        if mode == "json_replay":
            return JsonReplayDetector()
        if mode == "field_cv":
            from .detectors.field_cv_backend import FieldCvDetector

            return FieldCvDetector(confidence=confidence)
        if mode != "gpu":
            raise ConfigurationError(f"unsupported inference mode: {mode}")

        gpu_status = gpu_inference_status(settings.detector_weights)
        if not gpu_status["available"]:
            raise ConfigurationError(
                f"GPU 模式不可用：{gpu_status['reason']}"
            )
        # 大型依赖和模型只在实际切换到 GPU 时加载，noop 启动保持轻量。
        from .detectors.ultralytics_backend import UltralyticsDetector

        return UltralyticsDetector(
            weights=settings.detector_weights,
            class_config=class_config,
            confidence=confidence,
            config_dir=settings.storage_root,
            device=0,
            require_cuda=True,
        )

    startup_inference_error: str | None = None
    try:
        detector = build_detector(
            str(runtime_snapshot["detector"]["mode"]),
            float(runtime_snapshot["detector"]["confidence"]),
        )
    except ConfigurationError as exc:
        if runtime_snapshot["detector"]["mode"] != "gpu":
            raise
        # GPU 环境或权重在重启后失效时自动回退，确保控制台仍能打开。
        startup_inference_error = str(exc)
        runtime_settings.update({"detector": {"mode": "noop"}})
        runtime_snapshot = runtime_settings.snapshot()
        detector = NoopDetector()

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
        nonlocal detector, startup_inference_error
        detector_values = values["detector"]
        pipeline_values = values["pipeline"]
        if not isinstance(detector_values, dict) or not isinstance(
            pipeline_values, dict
        ):
            raise ConfigurationError("invalid runtime settings snapshot")
        requested_mode = str(detector_values["mode"])
        confidence = float(detector_values["confidence"])
        if requested_mode != detector_runtime_mode(detector):
            try:
                replacement = build_detector(requested_mode, confidence)
            except ConfigurationError as exc:
                startup_inference_error = str(exc)
                raise
            pipeline.detector = replacement
            detector = replacement
            startup_inference_error = None
        elif hasattr(detector, "confidence"):
            detector.confidence = confidence
        pipeline.fusion_iou = float(pipeline_values["fusion_iou"])
        pipeline.module_registry = build_module_registry_from_config(
            merge_module_runtime_settings(base_module_config, values),
            settings.project_root,
        )

    def inference_status() -> dict[str, object]:
        gpu_status = gpu_inference_status(settings.detector_weights)
        return {
            "active_mode": detector_runtime_mode(pipeline.detector),
            "noop": {
                "available": True,
                "reason": None,
            },
            "gpu": gpu_status,
            "last_error": startup_inference_error,
        }

    service = InspectionService(
        repository,
        pipeline,
        settings.max_image_bytes,
        runtime_settings=runtime_settings,
        apply_runtime_settings=apply_runtime,
        inference_status_provider=inference_status,
    )
    service.attach_offline_batches(
        OfflineBatchManager(settings.dataset_inbox_path, repository, service)
    )
    return service
