import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from scripts.build_station_marker_dataset import build_station_marker_dataset


class StationMarkerDatasetTests(unittest.TestCase):
    def test_manual_boxes_and_confirmed_negatives_are_exported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            samples = root / "samples"
            positive_dir = samples / "1"
            negative_dir = samples / "-1"
            positive_dir.mkdir(parents=True)
            negative_dir.mkdir(parents=True)
            cv2.imwrite(
                str(positive_dir / "1.png"),
                np.full((100, 120, 3), 220, dtype=np.uint8),
            )
            cv2.imwrite(
                str(negative_dir / "background.png"),
                np.zeros((100, 120, 3), dtype=np.uint8),
            )
            samples_config = root / "samples.json"
            samples_config.write_text(
                json.dumps(
                    {
                        "dataset_root": str(samples),
                        "extensions": [".png"],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "output"
            config = root / "dataset.json"
            config.write_text(
                json.dumps(
                    {
                        "samples_config": str(samples_config),
                        "output_root": str(output),
                        "negative_root": str(negative_dir),
                        "manual_bboxes": {"1/1.png": [[20, 25, 80, 75]]},
                    }
                ),
                encoding="utf-8",
            )

            summary = build_station_marker_dataset(config)

            self.assertEqual(summary["images"], 2)
            self.assertEqual(summary["positive_images"], 1)
            self.assertEqual(summary["negative_images"], 1)
            self.assertEqual(summary["annotations"], 1)
            labels = sorted((output / "labels" / "train").glob("*.txt"))
            self.assertEqual(len(labels), 2)
            self.assertEqual(
                sum(bool(path.read_text(encoding="utf-8")) for path in labels),
                1,
            )


if __name__ == "__main__":
    unittest.main()
