import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from app.errors import ValidationError
from app.training_data import build_yolo_dataset


class TrainingDataTests(unittest.TestCase):
    def test_build_yolo_dataset_skips_unlabelled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            source.mkdir()
            labelled = source / "frame_01.jpg"
            unlabelled = source / "frame_02.jpg"
            Image.new("RGB", (100, 80), "white").save(labelled)
            Image.new("RGB", (100, 80), "black").save(unlabelled)
            labelled.with_name(labelled.name + ".labels.json").write_text(
                json.dumps(
                    [{"class": "station_marker", "bbox_xyxy": [10, 10, 60, 50]}]
                ),
                encoding="utf-8",
            )
            manifest = root / "manifest.jsonl"
            rows = [
                {
                    "path": str(labelled),
                    "capture_id": "c1",
                    "sha256": "a" * 64,
                    "width": 100,
                    "height": 80,
                    "split": "train",
                },
                {
                    "path": str(unlabelled),
                    "capture_id": "c1",
                    "sha256": "b" * 64,
                    "width": 100,
                    "height": 80,
                    "split": "train",
                },
            ]
            manifest.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            classes = root / "classes.json"
            classes.write_text(
                json.dumps({"station_marker": {"id": "station_marker"}}),
                encoding="utf-8",
            )

            summary = build_yolo_dataset(manifest, classes, root / "yolo")
            self.assertEqual(summary["images_converted"], 1)
            self.assertEqual(summary["images_skipped_unlabelled"], 1)
            label = next((root / "yolo" / "labels" / "train").iterdir())
            self.assertTrue(label.read_text(encoding="utf-8").startswith("0 "))

    def test_refuses_nonempty_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "output"
            output.mkdir()
            (output / "old.txt").write_text("old", encoding="utf-8")
            manifest = root / "manifest.jsonl"
            manifest.write_text("", encoding="utf-8")
            classes = root / "classes.json"
            classes.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValidationError):
                build_yolo_dataset(manifest, classes, output)


if __name__ == "__main__":
    unittest.main()
