import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from app.image_io import read_bgr_image, write_bgr_image
from app.modules.station_number_model import segment_station_number
from scripts.build_station_number_crops import build_station_number_crops


def _station_sign(text: str) -> np.ndarray:
    image = np.zeros((420, 420, 3), dtype=np.uint8)
    cv2.circle(image, (210, 210), 125, (255, 0, 0), thickness=-1)
    (width, height), _ = cv2.getTextSize(
        text,
        cv2.FONT_HERSHEY_SIMPLEX,
        3.3,
        10,
    )
    cv2.putText(
        image,
        text,
        ((420 - width) // 2, (420 + height) // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        3.3,
        (255, 255, 255),
        10,
        lineType=cv2.LINE_AA,
    )
    return image


class StationNumberCropTests(unittest.TestCase):
    def test_manifest_is_matched_by_hash_after_label_directory_correction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            class_dir = root / "source" / "4"
            class_dir.mkdir(parents=True)
            source = class_dir / "4.png"
            self.assertTrue(write_bgr_image(source, _station_sign("4")))
            digest = hashlib.sha256(source.read_bytes()).hexdigest()

            samples_config = root / "samples.json"
            samples_config.write_text(
                json.dumps(
                    {
                        "dataset_root": str(root / "source"),
                        "labels": [4],
                    }
                ),
                encoding="utf-8",
            )
            annotation_manifest = root / "annotations.jsonl"
            annotation_manifest.write_text(
                json.dumps(
                    {
                        "sha256": digest,
                        "station_number": 3,
                        "annotation_source": "manual",
                        "bboxes_xyxy": [
                            [0, 0, 40, 40],
                            [80, 80, 340, 340],
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output = root / "crops"
            config = root / "crop_config.json"
            config.write_text(
                json.dumps(
                    {
                        "samples_config": str(samples_config),
                        "annotation_manifest": str(annotation_manifest),
                        "output_root": str(output),
                        "padding_fraction": 0.08,
                    }
                ),
                encoding="utf-8",
            )

            summary = build_station_number_crops(config)

            crop = read_bgr_image(output / "4" / "4.png")
            self.assertEqual(summary["classifier_samples"], 1)
            self.assertEqual(summary["corrected_manifest_labels"], 1)
            self.assertEqual(summary["samples_per_class"], {"4": 1})
            self.assertIsNotNone(crop)
            self.assertIsNone(segment_station_number(crop)["error"])


if __name__ == "__main__":
    unittest.main()
