from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from app.domain import BoundingBox, CaptureMetadata, Detection
from app.pipeline import InspectionPipeline


class StaticDetector:
    name = "static_test"
    configured = True

    def detect(self, image_path, metadata, frame_index):
        return [
            Detection(
                type="tool",
                class_id="wrench",
                class_cn="扳手",
                bbox=BoundingBox(10 + frame_index, 20, 100 + frame_index, 120),
                confidence=0.9 + frame_index * 0.01,
                source_frame=frame_index,
            )
        ]


class StaticMeterDetector:
    name = "static_meter_test"
    configured = True

    def detect(self, image_path, metadata, frame_index):
        reading = ["600.0", "600.0", "600.8"][frame_index]
        angle = [359.0, 0.0, 1.0][frame_index]
        reference = {
            "normal_angle_deg": 0.0,
            "tolerance_deg": 5.0,
            "min_confidence": 0.55,
        }
        return [
            Detection(
                type="digital_meter",
                class_id="digital_meter",
                class_cn="数字表",
                bbox=BoundingBox(10, 10, 90, 70),
                confidence=0.9,
                attributes={"_digital_text": reading},
                source_frame=frame_index,
            ),
            Detection(
                type="analog_meter",
                class_id="analog_meter",
                class_cn="指针表",
                bbox=BoundingBox(100, 10, 180, 90),
                confidence=0.9,
                attributes={
                    "_pointer_angle_deg": angle,
                    "_pointer_confidence": 0.9,
                    "_analog_reference": reference,
                },
                source_frame=frame_index,
            ),
        ]


class StaticModuleRegistry:
    def run(self, metadata, image_paths, objects, *, detector_configured):
        return {
            "digital_meter": {
                "enabled": True,
                "status": "confirmed",
                "raw_text": "120.0",
            }
        }


class PipelineTests(unittest.TestCase):
    def test_three_frame_fusion_and_location(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_paths = []
            try:
                from PIL import Image
            except ImportError:
                self.skipTest("Pillow not available")
            for index in range(3):
                path = root / f"frame_{index}.jpg"
                Image.new("RGB", (200, 150), "white").save(path)
                image_paths.append(path)

            metadata = CaptureMetadata.from_mapping(
                {
                    "capture_id": "pipeline_test",
                    "capture_time": datetime.now(timezone.utc).isoformat(),
                    "station_id": "08",
                    "robot_pose": {"x_m": 1, "y_m": 2, "yaw_deg": 3},
                    "camera_id": "test",
                    "images": [path.name for path in image_paths],
                }
            )
            pipeline = InspectionPipeline(
                StaticDetector(),
                root / "processed",
                stations={"08": {"location_name": "08号区段传送带"}},
            )
            result = pipeline.process(metadata, image_paths)
            self.assertEqual(len(result.objects), 1)
            self.assertEqual(result.objects[0]["class"], "wrench")
            self.assertEqual(result.objects[0]["observations"], 3)
            self.assertEqual(result.objects[0]["location_text"], "08号区段传送带")
            self.assertTrue(Path(result.annotated_image).exists())

    def test_independent_module_results_are_exposed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            from PIL import Image

            image_path = root / "frame.jpg"
            Image.new("RGB", (200, 150), "white").save(image_path)
            metadata = CaptureMetadata.from_mapping(
                {
                    "capture_id": "module_pipeline_test",
                    "capture_time": datetime.now(timezone.utc).isoformat(),
                    "station_id": "08",
                    "robot_pose": {},
                    "camera_id": "test",
                    "images": [image_path.name],
                }
            )
            result = InspectionPipeline(
                StaticDetector(),
                root / "processed",
                module_registry=StaticModuleRegistry(),
            ).process(metadata, [image_path])
            self.assertEqual(
                result.modules["digital_meter"]["raw_text"],
                "120.0",
            )
            self.assertIn("modules", result.to_dict())

    def test_meter_measurements_are_aggregated_across_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            from PIL import Image

            image_paths = []
            for index in range(3):
                path = root / f"meter_{index}.jpg"
                Image.new("RGB", (200, 120), "white").save(path)
                image_paths.append(path)
            metadata = CaptureMetadata.from_mapping(
                {
                    "capture_id": "meter_pipeline_test",
                    "capture_time": datetime.now(timezone.utc).isoformat(),
                    "station_id": "08",
                    "robot_pose": {},
                    "camera_id": "test",
                    "images": [path.name for path in image_paths],
                }
            )
            result = InspectionPipeline(
                StaticMeterDetector(),
                root / "processed",
            ).process(metadata, image_paths)
            by_type = {item["type"]: item for item in result.objects}
            self.assertEqual(by_type["digital_meter"]["raw_text"], "600.0")
            self.assertEqual(by_type["digital_meter"]["status"], "confirmed")
            self.assertEqual(by_type["analog_meter"]["status"], "normal")
            self.assertNotIn("_pointer_angle_deg", by_type["analog_meter"])


if __name__ == "__main__":
    unittest.main()
