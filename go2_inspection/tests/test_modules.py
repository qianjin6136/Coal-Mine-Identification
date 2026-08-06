from datetime import datetime, timezone
from pathlib import Path
import unittest

from app.domain import CaptureMetadata
from app.modules.analog_meter import AnalogMeterModule
from app.modules.base import ModuleContext
from app.modules.coal_presence import CoalPresenceModule
from app.modules.station_number import StationNumberModule
from app.modules.station_number_model import StationNumberRecognition


def _context(*, configured: bool = False, objects=()) -> ModuleContext:
    metadata = CaptureMetadata.from_mapping(
        {
            "capture_id": "module_test",
            "capture_time": datetime.now(timezone.utc).isoformat(),
            "station_id": "8",
            "robot_pose": {},
            "camera_id": "test",
            "images": ["frame.jpg"],
        }
    )
    return ModuleContext(
        metadata=metadata,
        image_paths=(Path("frame.jpg"),),
        objects=objects,
        detector_configured=configured,
    )


class IndependentModuleTests(unittest.TestCase):
    def test_unavailable_detector_is_not_reported_as_no_coal(self) -> None:
        result = CoalPresenceModule({"enabled": True, "model_ready": False}).run(
            _context(configured=True)
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["present"])
        self.assertEqual(result["reason"], "coal_field_model_not_trained")

    def test_binary_coal_module_only_reads_coal_objects(self) -> None:
        result = CoalPresenceModule({"enabled": True, "model_ready": True}).run(
            _context(
                configured=True,
                objects=(
                    {"type": "station_marker", "class": "station_marker"},
                    {"type": "coal_pile", "class": "coal_pile"},
                ),
            )
        )
        self.assertEqual(result["status"], "confirmed")
        self.assertTrue(result["present"])
        self.assertEqual(len(result["detections"]), 1)

    def test_station_number_is_limited_to_one_through_ten(self) -> None:
        result = StationNumberModule(
            {"enabled": True, "allowed_numbers": list(range(1, 11))}
        ).run(_context())
        self.assertEqual(result["number"], 8)
        self.assertEqual(result["source"], "capture_metadata")

    def test_station_number_uses_the_highest_confidence_yolo_marker_roi(self) -> None:
        class RecordingRecognizer:
            def __init__(self) -> None:
                self.calls = []

            def read(self, path, roi_bbox_xyxy=None):
                self.calls.append((path, roi_bbox_xyxy))
                return StationNumberRecognition(
                    status="confirmed",
                    number=7,
                    confidence=0.81,
                    sign_bbox_xyxy=(510, 210, 900, 590),
                    reason="station_number_template_confirmed",
                )

        module = StationNumberModule(
            {
                "enabled": True,
                "recognition_mode": "image_classifier",
                "allowed_numbers": [7, 8, 9, 10],
                "use_detector_roi": True,
                "allow_full_frame_fallback": False,
            }
        )
        recognizer = RecordingRecognizer()
        module.recognizer = recognizer
        result = module.run(
            _context(
                configured=True,
                objects=(
                    {
                        "type": "station_marker",
                        "confidence": 0.40,
                        "bbox_xyxy": [10, 20, 100, 120],
                    },
                    {
                        "type": "station_marker",
                        "confidence": 0.98,
                        "bbox_xyxy": [500, 200, 920, 620],
                    },
                ),
            )
        )

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["number"], 7)
        self.assertEqual(result["roi_source"], "yolo_station_marker")
        self.assertEqual(recognizer.calls[0][1], (500.0, 200.0, 920.0, 620.0))

    def test_station_number_does_not_scan_full_frame_without_yolo_marker(self) -> None:
        module = StationNumberModule(
            {
                "enabled": True,
                "recognition_mode": "image_classifier",
                "allowed_numbers": [7, 8, 9, 10],
                "use_detector_roi": True,
                "allow_full_frame_fallback": False,
            }
        )
        module.recognizer = object()

        result = module.run(_context(configured=True, objects=()))

        self.assertEqual(result["status"], "unreadable")
        self.assertEqual(result["reason"], "station_marker_not_detected")

    def test_deferred_modules_are_explicitly_disabled(self) -> None:
        self.assertEqual(
            AnalogMeterModule({"enabled": False}).run(_context())["status"],
            "disabled",
        )


if __name__ == "__main__":
    unittest.main()
