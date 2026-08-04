from datetime import datetime, timezone
import unittest

from app.domain import BoundingBox, CaptureMetadata
from app.errors import ValidationError


def valid_metadata() -> dict[str, object]:
    return {
        "capture_id": "go2_test_station08",
        "capture_time": datetime.now(timezone.utc).isoformat(),
        "station_id": "08",
        "robot_pose": {
            "frame": "map",
            "x_m": 1.2,
            "y_m": 3.4,
            "yaw_deg": 90,
        },
        "camera_id": "go2_front",
        "batch_id": "batch_001",
        "images": ["frame_01.jpg", "frame_02.jpg", "frame_03.jpg"],
    }


class DomainTests(unittest.TestCase):
    def test_valid_capture_metadata(self) -> None:
        metadata = CaptureMetadata.from_mapping(valid_metadata())
        self.assertEqual(metadata.station_id, "08")
        self.assertEqual(metadata.robot_pose.yaw_deg, 90.0)
        self.assertEqual(len(metadata.image_names), 3)
        self.assertEqual(metadata.batch_id, "batch_001")

    def test_rejects_unsafe_capture_id(self) -> None:
        data = valid_metadata()
        data["capture_id"] = "../escape"
        with self.assertRaises(ValidationError):
            CaptureMetadata.from_mapping(data)

    def test_bbox_iou(self) -> None:
        first = BoundingBox(0, 0, 100, 100)
        second = BoundingBox(50, 50, 150, 150)
        self.assertAlmostEqual(first.iou(second), 2500 / 17500)

    def test_bbox_rejects_non_numeric_values(self) -> None:
        with self.assertRaises(ValidationError):
            BoundingBox.from_sequence([1, None, 10, 20])


if __name__ == "__main__":
    unittest.main()
