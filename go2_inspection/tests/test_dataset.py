import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from app.dataset import assign_grouped_splits, inspect_dataset, write_dataset_reports


class DatasetTests(unittest.TestCase):
    def test_inspect_group_split_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            capture = root / "batch_a" / "capture_001"
            capture.mkdir(parents=True)
            metadata = {
                "capture_id": "capture_001",
                "batch_id": "batch_a",
                "station_id": "08",
                "camera_id": "go2_front",
                "robot_pose": {"x_m": 1, "y_m": 2, "yaw_deg": 3},
            }
            (capture / "metadata.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            for index in range(3):
                Image.new(
                    "RGB",
                    (640, 480),
                    (40 + index * 20, 80, 120),
                ).save(capture / f"frame_{index + 1:02d}.jpg")

            records = inspect_dataset(root)
            self.assertEqual(len(records), 3)
            self.assertTrue(all(item.capture_id == "capture_001" for item in records))
            assigned = assign_grouped_splits(records, seed="test")
            self.assertEqual(len({item.split for item in assigned}), 1)
            same_batch_other_capture = replace(
                records[0],
                capture_id="capture_002",
                relative_path="other/frame.jpg",
            )
            batch_assigned = assign_grouped_splits(
                [records[0], same_batch_other_capture],
                seed="test",
            )
            self.assertEqual(batch_assigned[0].split, batch_assigned[1].split)
            three_batches = [
                replace(
                    records[0],
                    capture_id=f"capture_{index}",
                    batch_id=f"batch_{index}",
                    relative_path=f"batch_{index}/frame.jpg",
                )
                for index in range(3)
            ]
            balanced = assign_grouped_splits(three_batches, seed="test")
            self.assertEqual(
                {item.split for item in balanced},
                {"train", "val", "test"},
            )

            outputs = write_dataset_reports(assigned, root / "reports")
            summary = json.loads(outputs["summary"].read_text(encoding="utf-8"))
            self.assertEqual(summary["images_total"], 3)
            self.assertEqual(summary["captures_not_three_frames"], [])
            self.assertEqual(len(summary["empty_splits"]), 2)


if __name__ == "__main__":
    unittest.main()
