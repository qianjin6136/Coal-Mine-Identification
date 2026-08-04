from datetime import datetime, timezone
from pathlib import Path
import tempfile
from threading import Event, Thread
import unittest

from app.detectors.noop import NoopDetector
from app.domain import CaptureMetadata
from app.pipeline import InspectionPipeline
from app.runtime_settings import RuntimeSettingsManager, build_runtime_defaults
from app.service import InspectionService
from app.storage import CaptureRepository
from app.errors import ValidationError


MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00"
    b"\x18\xdd\x8d\xb1\x00\x00\x00\x00IEND\xaeB`\x82"
)


class ServiceTests(unittest.TestCase):
    def test_ingest_store_process_and_idempotent_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = CaptureRepository(root / "inspection.db", root / "data")
            pipeline = InspectionPipeline(
                NoopDetector(),
                repository.processed_root,
                stations={"08": {"location_name": "08号区段"}},
            )
            service = InspectionService(repository, pipeline, 1024 * 1024)
            metadata = CaptureMetadata.from_mapping(
                {
                    "capture_id": "service_test",
                    "capture_time": datetime.now(timezone.utc).isoformat(),
                    "station_id": "08",
                    "robot_pose": {"x_m": 1, "y_m": 2, "yaw_deg": 3},
                    "camera_id": "test",
                    "images": ["frame_01.png"],
                }
            )
            first = service.ingest_capture(
                metadata, [("frame_01.png", MINIMAL_PNG)]
            )
            self.assertEqual(first["status"], "processed")
            self.assertEqual(first["recognition_summary"]["status"], "unrecognized")
            self.assertIn("detector_not_configured", first["result"]["warnings"][0])
            self.assertEqual(service.health()["captures_total"], 1)
            metadata_sidecar = (
                root / "data" / "incoming" / "service_test" / "metadata.json"
            )
            self.assertTrue(metadata_sidecar.is_file())

            retry = service.ingest_capture(
                metadata, [("frame_01.png", MINIMAL_PNG)]
            )
            self.assertTrue(retry["idempotent_replay"])
            self.assertEqual(service.health()["captures_total"], 1)

            with self.assertRaises(ValidationError):
                service.ingest_capture(
                    metadata,
                    [("frame_01.png", MINIMAL_PNG + b"different")],
                )

            listed = service.list_captures(station_id="08")
            self.assertEqual(listed["total"], 1)
            self.assertIn("recognition_summary", listed["items"][0])
            corrected = service.correct_capture(
                "service_test",
                {
                    "operator": "tester",
                    "reason": "人工复核",
                    "objects": [
                        {
                            "type": "tool",
                            "class": "wrench",
                            "class_cn": "扳手",
                            "bbox_xyxy": [1, 2, 10, 20],
                            "confidence": 1.0,
                        }
                    ],
                },
            )
            self.assertTrue(corrected["manually_corrected"])
            self.assertEqual(corrected["result"]["objects"][0]["class"], "wrench")
            self.assertEqual(corrected["original_result"]["objects"], [])
            self.assertEqual(
                corrected["recognition_summary"]["primary"]["source_id"],
                "manual_review",
            )
            corrected_list = service.list_captures(station_id="08")
            self.assertEqual(corrected_list["items"][0]["object_count"], 1)
            self.assertEqual(
                corrected_list["items"][0]["recognition_summary"]["primary"][
                    "source_id"
                ],
                "manual_review",
            )

            reprocessed = service.reprocess_capture("service_test")
            self.assertFalse(reprocessed["manually_corrected"])
            self.assertEqual(len(reprocessed["corrections"]), 1)
            self.assertFalse(reprocessed["corrections"][0]["active"])

            exported = service.export_captures()
            self.assertEqual(len(exported), 1)
            self.assertEqual(exported[0]["capture_id"], "service_test")

    def test_runtime_update_waits_for_a_consistent_processing_snapshot(self) -> None:
        class BlockingDetector:
            name = "blocking"
            configured = True

            def __init__(self) -> None:
                self.confidence = 0.35
                self.started = Event()
                self.release = Event()
                self.observed: list[float] = []

            def detect(self, image_path, metadata, frame_index):
                self.observed.append(self.confidence)
                self.started.set()
                self.release.wait(timeout=2)
                self.observed.append(self.confidence)
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            detector = BlockingDetector()
            pipeline = InspectionPipeline(
                detector,
                root / "processed",
                fusion_iou=0.45,
            )
            defaults = build_runtime_defaults(
                detector_confidence=0.35,
                fusion_iou=0.45,
                module_config={
                    "tool_and_safety_sign": {"enabled": False},
                    "coal_presence": {"enabled": False},
                    "station_number": {"enabled": False},
                    "digital_meter": {"enabled": False},
                    "analog_meter": {"enabled": False},
                },
            )
            runtime = RuntimeSettingsManager(
                root / "runtime_settings.json",
                defaults,
            )

            def apply_runtime(values) -> None:
                detector.confidence = values["detector"]["confidence"]
                pipeline.fusion_iou = values["pipeline"]["fusion_iou"]

            service = InspectionService(
                CaptureRepository(root / "inspection.db", root / "data"),
                pipeline,
                1024 * 1024,
                runtime_settings=runtime,
                apply_runtime_settings=apply_runtime,
            )
            metadata = CaptureMetadata.from_mapping(
                {
                    "capture_id": "concurrent_runtime_test",
                    "capture_time": datetime.now(timezone.utc).isoformat(),
                    "station_id": "manual",
                    "robot_pose": {},
                    "camera_id": "test",
                    "images": ["frame.png"],
                }
            )
            processing_result: dict[str, object] = {}

            def process_capture() -> None:
                processing_result.update(
                    service.ingest_capture(
                        metadata,
                        [("frame.png", MINIMAL_PNG)],
                    )
                )

            update_result: dict[str, object] = {}

            def update_settings() -> None:
                update_result.update(
                    service.update_runtime_settings(
                        {"detector": {"confidence": 0.8}}
                    )
                )

            processing_thread = Thread(target=process_capture)
            processing_thread.start()
            self.assertTrue(detector.started.wait(timeout=1))
            update_thread = Thread(target=update_settings)
            update_thread.start()
            detector.release.set()
            processing_thread.join(timeout=2)
            update_thread.join(timeout=2)

            self.assertFalse(processing_thread.is_alive())
            self.assertFalse(update_thread.is_alive())
            self.assertEqual(detector.observed, [0.35, 0.35])
            self.assertEqual(
                processing_result["result"]["processing_parameters"][
                    "detector"
                ]["confidence"],
                0.35,
            )
            self.assertEqual(
                update_result["current"]["detector"]["confidence"],
                0.8,
            )

    def test_runtime_mode_switch_replaces_detector(self) -> None:
        class FakeGpuDetector:
            name = "ultralytics"
            runtime_mode = "gpu"
            configured = True

            def detect(self, image_path, metadata, frame_index):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pipeline = InspectionPipeline(NoopDetector(), root / "processed")
            defaults = build_runtime_defaults(
                detector_confidence=0.35,
                fusion_iou=0.45,
                module_config={
                    "tool_and_safety_sign": {"enabled": False},
                    "coal_presence": {"enabled": False},
                    "station_number": {"enabled": False},
                    "digital_meter": {"enabled": False},
                    "analog_meter": {"enabled": False},
                },
            )
            runtime = RuntimeSettingsManager(
                root / "runtime_settings.json",
                defaults,
            )

            def apply_runtime(values) -> None:
                mode = values["detector"]["mode"]
                pipeline.detector = (
                    FakeGpuDetector() if mode == "gpu" else NoopDetector()
                )

            service = InspectionService(
                CaptureRepository(root / "inspection.db", root / "data"),
                pipeline,
                1024 * 1024,
                runtime_settings=runtime,
                apply_runtime_settings=apply_runtime,
            )
            enabled = service.update_runtime_settings(
                {"detector": {"mode": "gpu"}}
            )
            self.assertEqual(enabled["current"]["detector"]["mode"], "gpu")
            self.assertEqual(service.health()["inference_mode"], "gpu")
            self.assertTrue(service.health()["detector_configured"])

            disabled = service.update_runtime_settings(
                {"detector": {"mode": "noop"}}
            )
            self.assertEqual(disabled["current"]["detector"]["mode"], "noop")
            self.assertEqual(service.health()["inference_mode"], "noop")


if __name__ == "__main__":
    unittest.main()
