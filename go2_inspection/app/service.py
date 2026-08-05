"""协调幂等校验、文件入库和视觉推理的应用服务。"""

from __future__ import annotations

from threading import RLock
from typing import Any, Callable, Mapping, Sequence, TYPE_CHECKING

from .domain import BoundingBox, CaptureMetadata
from .errors import ReportNotReadyError, ValidationError
from .inference import detector_runtime_mode
from .pipeline import InspectionPipeline
from .reporting import build_batch_report, render_report_docx
from .result_summary import build_recognition_summary
from .runtime_settings import RuntimeSettingsManager
from .storage import CaptureRepository

if TYPE_CHECKING:
    from .offline_import import OfflineBatchManager


class InspectionService:
    """串联存储层与检测流水线，对 HTTP 层提供稳定的业务接口。"""

    def __init__(
        self,
        repository: CaptureRepository,
        pipeline: InspectionPipeline,
        max_image_bytes: int,
        runtime_settings: RuntimeSettingsManager | None = None,
        apply_runtime_settings: Callable[[Mapping[str, Any]], None] | None = None,
        inference_status_provider: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self.repository = repository
        self.pipeline = pipeline
        self.max_image_bytes = max_image_bytes
        self.runtime_settings = runtime_settings
        self._apply_runtime_settings = apply_runtime_settings
        self._inference_status_provider = inference_status_provider
        self._runtime_lock = RLock()
        self.offline_batches: OfflineBatchManager | None = None

    def attach_offline_batches(self, manager: "OfflineBatchManager") -> None:
        self.offline_batches = manager

    def start_background_services(self) -> None:
        if self.offline_batches is not None:
            self.offline_batches.start()

    def stop_background_services(self) -> None:
        if self.offline_batches is not None:
            self.offline_batches.stop()

    def ingest_capture(
        self,
        metadata: CaptureMetadata,
        images: Sequence[tuple[str, bytes]],
    ) -> dict[str, Any]:
        """接收一次抓拍；相同 capture_id 的重传直接返回既有结果。"""

        # GO2 断网补传可能重复提交同一任务，以 capture_id 作为幂等键避免重复推理。
        if self.repository.capture_exists(metadata.capture_id):
            self.repository.assert_replay_matches(metadata, images)
            existing = self.get_capture(metadata.capture_id)
            existing["idempotent_replay"] = True
            return existing

        image_paths = self.repository.save_capture(
            metadata, images, self.max_image_bytes
        )
        try:
            with self._runtime_lock:
                parameters = self._processing_parameters()
                result = self.pipeline.process(metadata, image_paths)
                result.processing_parameters = parameters
            self.repository.save_result(result)
        except Exception as exc:
            # 图片和任务已入库时保留失败状态，便于诊断或后续离线重跑。
            self.repository.save_error(metadata.capture_id, str(exc))
            raise
        return self.get_capture(metadata.capture_id)

    def get_capture(self, capture_id: str) -> dict[str, Any]:
        capture = self.repository.get_capture(capture_id)
        return self._attach_recognition_summary(capture)

    def delete_capture(self, capture_id: str) -> dict[str, Any]:
        """删除一条巡检任务及其关联文件。"""

        return self.repository.delete_capture(capture_id)

    def list_offline_batches(self) -> dict[str, Any]:
        if self.offline_batches is None:
            raise ValidationError("offline batch service is not configured")
        return self.offline_batches.discover_batches()

    def queue_offline_batch(self, batch_id: str) -> dict[str, Any]:
        if self.offline_batches is None:
            raise ValidationError("offline batch service is not configured")
        return self.offline_batches.queue_import(batch_id)

    def confirm_offline_batch_detection(self, batch_id: str) -> dict[str, Any]:
        if self.offline_batches is None:
            raise ValidationError("offline batch service is not configured")
        return self.offline_batches.confirm_detection(batch_id)

    def confirm_offline_batch_report(self, batch_id: str) -> dict[str, Any]:
        if self.offline_batches is None:
            raise ValidationError("offline batch service is not configured")
        return self.offline_batches.confirm_report(batch_id)

    def get_offline_batch(self, batch_id: str) -> dict[str, Any]:
        if self.offline_batches is None:
            raise ValidationError("offline batch service is not configured")
        return self.offline_batches.get_batch(batch_id)

    def retry_offline_batch(self, batch_id: str) -> dict[str, Any]:
        if self.offline_batches is None:
            raise ValidationError("offline batch service is not configured")
        return self.offline_batches.retry_batch(batch_id)

    def generate_offline_batch_report(self, batch_id: str) -> bytes:
        """按需生成整批巡检 Word 报告，始终采用数据库中的有效结果。"""

        if self.offline_batches is None:
            raise ValidationError("offline batch service is not configured")
        batch = self.offline_batches.get_batch(batch_id)
        if batch["status"] not in {"completed", "completed_with_errors", "failed"}:
            raise ReportNotReadyError("offline batch detection is not complete")
        if not batch.get("report_confirmed_at"):
            raise ReportNotReadyError("offline batch report has not been confirmed")
        report = build_batch_report(self.repository, batch_id)
        return render_report_docx(report)

    def list_captures(
        self,
        *,
        status: str | None = None,
        station_id: str | None = None,
        capture_id: str | None = None,
        source_batch_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        if status not in {None, "received", "processed", "failed"}:
            raise ValidationError("status must be received, processed or failed")
        if not 1 <= limit <= 500:
            raise ValidationError("limit must be between 1 and 500")
        if offset < 0:
            raise ValidationError("offset cannot be negative")
        result = self.repository.list_captures(
            status=status,
            station_id=station_id,
            capture_id=capture_id,
            source_batch_id=source_batch_id,
            limit=limit,
            offset=offset,
        )
        effective_results = self.repository.effective_results(
            [item["capture_id"] for item in result["items"]]
        )
        # 人工修正不覆盖模型原始 objects 表；列表中对已修正记录显示有效对象数。
        for item in result["items"]:
            effective_result = effective_results.get(item["capture_id"])
            if item["manually_corrected"]:
                item["object_count"] = len(
                    (effective_result or {}).get("objects") or []
                )
            item["recognition_summary"] = build_recognition_summary(
                capture_status=item["status"],
                result=effective_result,
                error=item.get("error"),
                manually_corrected=item["manually_corrected"],
            )
        return result

    def correct_capture(
        self,
        capture_id: str,
        correction: dict[str, Any],
    ) -> dict[str, Any]:
        """验证并保存人工修正，自动保留原始推理结果和审计历史。"""

        existing = self.repository.get_capture(capture_id)
        result = existing.get("result")
        if not isinstance(result, dict):
            raise ValidationError("capture has no result to correct")
        operator = str(correction.get("operator", "")).strip()
        if not operator or len(operator) > 100:
            raise ValidationError("operator is required and must be at most 100 characters")
        reason_value = correction.get("reason")
        reason = str(reason_value).strip() if reason_value is not None else None
        if reason and len(reason) > 1000:
            raise ValidationError("reason must be at most 1000 characters")
        if "objects" not in correction:
            raise ValidationError("correction.objects is required")
        objects = correction["objects"]
        if not isinstance(objects, list):
            raise ValidationError("correction.objects must be a list")
        normalized_objects = [
            self._validate_corrected_object(item, index)
            for index, item in enumerate(objects)
        ]
        corrected_result = {
            **result,
            "objects": normalized_objects,
            "warnings": list(result.get("warnings") or [])
            + ["manually_corrected"],
        }
        # 先撤销报告确认，避免并发下载在结果变化后沿用旧确认状态。
        self.repository.invalidate_report_confirmation_for_capture(capture_id)
        self.repository.save_correction(
            capture_id,
            corrected_result,
            operator=operator,
            reason=reason,
        )
        return self.get_capture(capture_id)

    def reprocess_capture(self, capture_id: str) -> dict[str, Any]:
        """使用当前模型和配置重跑既有原图，并停用旧人工修正但保留审计。"""

        self.repository.invalidate_report_confirmation_for_capture(capture_id)
        metadata = self.repository.get_metadata(capture_id)
        image_paths = self.repository.image_paths(capture_id)
        try:
            with self._runtime_lock:
                parameters = self._processing_parameters()
                result = self.pipeline.process(metadata, image_paths)
                result.processing_parameters = parameters
            self.repository.save_result(result, deactivate_corrections=True)
        except Exception as exc:
            self.repository.save_error(capture_id, str(exc))
            raise
        return self.get_capture(capture_id)

    @staticmethod
    def _attach_recognition_summary(capture: dict[str, Any]) -> dict[str, Any]:
        capture["recognition_summary"] = build_recognition_summary(
            capture_status=capture["status"],
            result=capture.get("result"),
            error=capture.get("error"),
            manually_corrected=bool(capture.get("manually_corrected")),
        )
        return capture

    def export_captures(
        self,
        *,
        status: str | None = None,
        station_id: str | None = None,
        source_batch_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """输出扁平记录；每个识别目标一行，无目标抓拍仍保留一行。"""

        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            summaries = self.list_captures(
                status=status,
                station_id=station_id,
                source_batch_id=source_batch_id,
                limit=500,
                offset=offset,
            )
            for summary in summaries["items"]:
                capture = self.repository.get_capture(summary["capture_id"])
                result = capture.get("result") or {}
                objects = result.get("objects") or [None]
                for index, item in enumerate(objects):
                    object_data = item if isinstance(item, dict) else {}
                    rows.append(
                        {
                            "capture_id": capture["capture_id"],
                            "capture_time": capture["capture_time"],
                            "received_at": capture["received_at"],
                            "station_id": capture["station_id"],
                            "camera_id": capture["camera_id"],
                            "source_batch_id": capture.get("source_batch_id"),
                            "status": capture["status"],
                            "pose_frame": capture["robot_pose"].get("frame"),
                            "pose_x_m": capture["robot_pose"].get("x_m"),
                            "pose_y_m": capture["robot_pose"].get("y_m"),
                            "pose_yaw_deg": capture["robot_pose"].get("yaw_deg"),
                            "manually_corrected": capture["manually_corrected"],
                            "object_index": index if item is not None else None,
                            "object_type": object_data.get("type"),
                            "class": object_data.get("class"),
                            "class_cn": object_data.get("class_cn"),
                            "confidence": object_data.get("confidence"),
                            "bbox_xyxy": object_data.get("bbox_xyxy"),
                            "object_json": object_data or None,
                            "error": capture["error"],
                        }
                    )
            offset += len(summaries["items"])
            if offset >= summaries["total"] or not summaries["items"]:
                break
        return rows

    @staticmethod
    def _validate_corrected_object(item: Any, index: int) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise ValidationError(f"correction.objects[{index}] must be an object")
        result = dict(item)
        object_type = str(result.get("type", "")).strip()
        class_id = str(result.get("class", "")).strip()
        if not object_type or not class_id:
            raise ValidationError(
                f"correction.objects[{index}] requires type and class"
            )
        result["type"] = object_type
        result["class"] = class_id
        if "bbox_xyxy" in result and result["bbox_xyxy"] is not None:
            result["bbox_xyxy"] = BoundingBox.from_sequence(
                result["bbox_xyxy"]
            ).to_list()
        confidence = result.get("confidence")
        if confidence is not None:
            try:
                confidence_value = float(confidence)
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    f"correction.objects[{index}].confidence must be numeric"
                ) from exc
            if not 0.0 <= confidence_value <= 1.0:
                raise ValidationError(
                    f"correction.objects[{index}].confidence must be in [0, 1]"
                )
            result["confidence"] = confidence_value
        return result

    def health(self) -> dict[str, Any]:
        with self._runtime_lock:
            result = {
                "status": "ok",
                "detector": self.pipeline.detector.name,
                "detector_configured": self.pipeline.detector.configured,
                "inference_mode": detector_runtime_mode(self.pipeline.detector),
                **self.repository.health(),
            }
            if self.pipeline.module_registry is not None:
                result["modules"] = self.pipeline.module_registry.describe(
                    detector_configured=self.pipeline.detector.configured
                )
        return result

    def get_runtime_settings(self) -> dict[str, Any]:
        if self.runtime_settings is None:
            raise ValidationError("runtime settings are not configured")
        with self._runtime_lock:
            module_status = (
                self.pipeline.module_registry.describe(
                    detector_configured=self.pipeline.detector.configured
                )
                if self.pipeline.module_registry is not None
                else {}
            )
            payload = {
                "current": self.runtime_settings.snapshot(),
                "defaults": self.runtime_settings.defaults,
                "limits": {
                    "max_images": 5,
                    "max_image_bytes": self.max_image_bytes,
                    "accepted_image_types": [
                        "image/jpeg",
                        "image/png",
                        "image/bmp",
                    ],
                },
                "module_status": module_status,
            }
            if self._inference_status_provider is not None:
                payload["inference"] = dict(self._inference_status_provider())
            return payload

    def update_runtime_settings(
        self,
        patch: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.runtime_settings is None or self._apply_runtime_settings is None:
            raise ValidationError("runtime settings are not configured")
        with self._runtime_lock:
            previous = self.runtime_settings.snapshot()
            updated = self.runtime_settings.update(patch)
            try:
                self._apply_runtime_settings(updated)
            except Exception:
                self.runtime_settings.replace(previous)
                self._apply_runtime_settings(previous)
                raise
            return self.get_runtime_settings()

    def reset_runtime_settings(self) -> dict[str, Any]:
        if self.runtime_settings is None or self._apply_runtime_settings is None:
            raise ValidationError("runtime settings are not configured")
        with self._runtime_lock:
            previous = self.runtime_settings.snapshot()
            restored = self.runtime_settings.reset()
            try:
                self._apply_runtime_settings(restored)
            except Exception:
                self.runtime_settings.replace(previous)
                self._apply_runtime_settings(previous)
                raise
            return self.get_runtime_settings()

    def _processing_parameters(self) -> dict[str, Any]:
        return (
            self.runtime_settings.snapshot()
            if self.runtime_settings is not None
            else {}
        )
