"""同步执行短连拍检测、跨帧融合和证据图生成。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .annotation import annotate_image
from .detectors.base import DetectionBackend
from .domain import CaptureMetadata, Detection, InspectionResult
from .meters.analog import classify_pointer_status, angular_distance_deg
from .meters.digital import majority_vote_readings
from .meters.processor import MeterProcessor
from .modules.registry import ModuleRegistry


class InspectionPipeline:
    """与检测器实现解耦的单次巡检处理流水线。"""

    def __init__(
        self,
        detector: DetectionBackend,
        processed_root: Path,
        stations: dict[str, Any] | None = None,
        fusion_iou: float = 0.45,
        meter_processor: MeterProcessor | None = None,
        module_registry: ModuleRegistry | None = None,
    ) -> None:
        self.detector = detector
        self.processed_root = Path(processed_root)
        self.stations = stations or {}
        self.fusion_iou = fusion_iou
        self.meter_processor = meter_processor
        self.module_registry = module_registry

    def process(
        self, metadata: CaptureMetadata, image_paths: Sequence[Path]
    ) -> InspectionResult:
        """逐帧检测后汇总目标，并基于首帧生成标注证据图。"""

        frame_detections: list[list[Detection]] = []
        warnings: list[str] = []
        for frame_index, image_path in enumerate(image_paths):
            detections = self.detector.detect(image_path, metadata, frame_index)
            if self.meter_processor is not None:
                warnings.extend(
                    self.meter_processor.enrich_frame(
                        detections,
                        image_path,
                        metadata,
                    )
                )
            frame_detections.append(detections)

        # 短连拍中同一目标常被重复检出，先融合再对外输出可减少重复告警。
        fused = fuse_detections(frame_detections, self.fusion_iou)
        location_text = self._location_text(metadata.station_id)
        objects = [detection.to_dict(location_text) for detection in fused]
        if not self.detector.configured:
            warnings.append(
                "detector_not_configured: capture stored successfully; add model weights later"
            )
        modules = (
            self.module_registry.run(
                metadata,
                image_paths,
                objects,
                detector_configured=self.detector.configured,
            )
            if self.module_registry is not None
            else {}
        )

        annotated_relative: str | None = None
        if image_paths:
            # 首帧通常是主视角；证据图失败不应使已经得到的结构化结果作废。
            destination = (
                self.processed_root / metadata.capture_id / "annotated_frame_01.jpg"
            )
            try:
                annotate_image(image_paths[0], destination, objects)
                annotated_relative = str(destination)
            except (RuntimeError, OSError) as exc:
                warnings.append(f"annotation_failed: {exc}")

        return InspectionResult(
            capture_id=metadata.capture_id,
            station_id=metadata.station_id,
            capture_pose=metadata.robot_pose,
            objects=objects,
            modules=modules,
            warnings=warnings,
            annotated_image=annotated_relative,
        )

    def _location_text(self, station_id: str) -> str:
        station = self.stations.get(station_id, {})
        return str(station.get("location_name", f"{station_id}号区段"))


def fuse_detections(
    frame_detections: Sequence[Sequence[Detection]],
    iou_threshold: float = 0.45,
) -> list[Detection]:
    """按类别和空间重叠度融合短连拍中的重复目标，避免合并相邻同类物体。"""

    clusters: list[list[Detection]] = []
    for detections in frame_detections:
        for detection in detections:
            best_cluster: list[Detection] | None = None
            best_iou = 0.0
            for cluster in clusters:
                # 以簇内最高置信度框作为空间基准，降低低质量框造成的匹配漂移。
                representative = max(cluster, key=lambda item: item.confidence)
                if (
                    representative.type != detection.type
                    or representative.class_id != detection.class_id
                ):
                    continue
                overlap = representative.bbox.iou(detection.bbox)
                if overlap >= iou_threshold and overlap > best_iou:
                    best_iou = overlap
                    best_cluster = cluster
            if best_cluster is None:
                clusters.append([detection])
            else:
                best_cluster.append(detection)

    fused: list[Detection] = []
    for cluster in clusters:
        # 保留最可信的一次检测，同时记录该目标实际出现过的帧数。
        representative = max(cluster, key=lambda item: item.confidence)
        representative.attributes = {
            **representative.attributes,
            "observations": len({item.source_frame for item in cluster}),
        }
        _aggregate_meter_measurements(cluster, representative)
        fused.append(representative)
    return sorted(fused, key=lambda item: item.confidence, reverse=True)


def _aggregate_meter_measurements(
    cluster: Sequence[Detection],
    representative: Detection,
) -> None:
    """把各帧私有测量值汇总为对外稳定的仪表字段。"""

    if representative.type == "digital_meter":
        readings = [item.attributes.get("_digital_text") for item in cluster]
        reading = majority_vote_readings(
            [value if isinstance(value, str) else None for value in readings],
            min_agreement=2 if len(cluster) >= 2 else 1,
        )
        representative.attributes.update(
            {
                "status": reading.status,
                "raw_text": reading.raw_text,
                "value": reading.value,
                "reading_confidence": round(reading.confidence, 6),
                "reading_votes": reading.votes,
            }
        )
        _remove_private_attributes(representative)
        return
    if representative.type != "analog_meter":
        _remove_private_attributes(representative)
        return

    measurements = [
        (
            float(item.attributes["_pointer_angle_deg"]),
            float(item.attributes.get("_pointer_confidence", 0.0)),
        )
        for item in cluster
        if item.attributes.get("_pointer_angle_deg") is not None
    ]
    reference = next(
        (
            item.attributes.get("_analog_reference")
            for item in cluster
            if isinstance(item.attributes.get("_analog_reference"), dict)
        ),
        None,
    )
    if reference is None:
        representative.attributes.update(
            {
                "status": "uncertain",
                "meter_confidence": 0.0,
                "reason": "analog_reference_missing",
            }
        )
        _remove_private_attributes(representative)
        return
    angle: float | None = None
    confidence = 0.0
    if measurements:
        # 使用 medoid 而非普通中位数，可正确处理 359°、0°、1° 的跨零情况。
        angle, confidence = min(
            measurements,
            key=lambda candidate: sum(
                angular_distance_deg(candidate[0], other[0])
                for other in measurements
            ),
        )
    status = classify_pointer_status(
        angle,
        float(reference["normal_angle_deg"]),
        float(reference["tolerance_deg"]),
        confidence,
        float(reference.get("min_confidence", 0.55)),
    )
    status_dict = status.to_dict()
    representative.attributes.update(
        {
            "status": status_dict["status"],
            "meter_confidence": status_dict["confidence"],
            "angle_deg_internal": status_dict["angle_deg_internal"],
            "reference_angle_deg_internal": status_dict[
                "reference_angle_deg_internal"
            ],
            "delta_deg_internal": status_dict["delta_deg_internal"],
            "reason": status_dict["reason"],
        }
    )
    _remove_private_attributes(representative)


def _remove_private_attributes(detection: Detection) -> None:
    detection.attributes = {
        key: value
        for key, value in detection.attributes.items()
        if not key.startswith("_")
    }
