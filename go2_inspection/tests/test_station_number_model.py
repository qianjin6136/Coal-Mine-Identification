import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from app.modules.station_number_model import (
    StationNumberRecognizer,
    StationNumberTemplateModel,
    load_station_number_samples,
    segment_station_number,
    train_station_number_model,
)


def _station_sign(text: str) -> np.ndarray:
    image = np.zeros((420, 420, 3), dtype=np.uint8)
    cv2.circle(image, (210, 210), 125, (255, 0, 0), thickness=-1)
    font_scale = 3.3 if len(text) == 1 else 2.5
    thickness = 10
    (width, height), _ = cv2.getTextSize(
        text,
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        thickness,
    )
    cv2.putText(
        image,
        text,
        ((420 - width) // 2, (420 + height) // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        (255, 255, 255),
        thickness,
        lineType=cv2.LINE_AA,
    )
    return image


class StationNumberModelTests(unittest.TestCase):
    def test_batched_prediction_matches_pairwise_dice_distance(self) -> None:
        from app.modules.station_number_model import _dice_distance_with_shift

        signs = [_station_sign("2"), _station_sign("7"), _station_sign("10")]
        features = np.asarray(
            [segment_station_number(image)["feature"] for image in signs]
        )
        model = StationNumberTemplateModel(
            features,
            [2, 7, 10],
            ["synthetic-2", "synthetic-7", "synthetic-10"],
        )
        query = np.roll(features[1], 2, axis=1)

        expected_index = min(
            range(len(features)),
            key=lambda index: _dice_distance_with_shift(query, features[index]),
        )
        predicted, _ = model.predict(query)

        self.assertEqual(predicted, model.labels[expected_index])

    def test_blue_sign_is_segmented_and_classified(self) -> None:
        signs = [_station_sign("3"), _station_sign("8"), _station_sign("10")]
        features = [segment_station_number(image)["feature"] for image in signs]
        model = StationNumberTemplateModel(
            np.asarray(features),
            [3, 8, 10],
            ["synthetic-3", "synthetic-8", "synthetic-10"],
        )
        recognizer = StationNumberRecognizer(model, minimum_confidence=0.55)

        for expected, image in zip((3, 8, 10), signs):
            result = recognizer.read_image(image)
            self.assertEqual(result.status, "confirmed")
            self.assertEqual(result.number, expected)
            self.assertIsNotNone(result.sign_bbox_xyxy)

    def test_non_blue_image_is_unreadable(self) -> None:
        template = np.zeros((1, 96, 96), dtype=np.uint8)
        template[:, 20:70, 40:55] = 1
        recognizer = StationNumberRecognizer(
            StationNumberTemplateModel(template, [1], ["synthetic-1"])
        )

        result = recognizer.read_image(np.zeros((200, 200, 3), dtype=np.uint8))

        self.assertEqual(result.status, "unreadable")
        self.assertEqual(result.reason, "blue_station_sign_not_found")

    def test_yolo_roi_avoids_larger_blue_distractor(self) -> None:
        sign = _station_sign("7")
        feature = segment_station_number(sign)["feature"]
        recognizer = StationNumberRecognizer(
            StationNumberTemplateModel(
                np.asarray([feature]),
                [7],
                ["synthetic-7"],
            ),
            minimum_confidence=0.55,
        )
        image = np.zeros((720, 1000, 3), dtype=np.uint8)
        cv2.circle(image, (230, 360), 220, (255, 0, 0), thickness=-1)
        image[150:570, 500:920] = sign

        full_frame = recognizer.read_image(image)
        yolo_crop = recognizer.read_image(
            image,
            roi_bbox_xyxy=[500, 150, 920, 570],
        )

        self.assertEqual(full_frame.status, "unreadable")
        self.assertEqual(yolo_crop.status, "confirmed")
        self.assertEqual(yolo_crop.number, 7)
        self.assertGreaterEqual(yolo_crop.sign_bbox_xyxy[0], 500)
        self.assertGreaterEqual(yolo_crop.sign_bbox_xyxy[1], 150)

    def test_numeric_directories_support_multiple_samples_per_class(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dataset = root / "numbers"
            for label in (7, 8):
                class_dir = dataset / str(label)
                class_dir.mkdir(parents=True)
                for index in (1, 2):
                    cv2.imwrite(
                        str(class_dir / f"{label} ({index}).png"),
                        _station_sign(str(label)),
                    )
            config = root / "samples.json"
            config.write_text(
                json.dumps({"dataset_root": str(dataset)}),
                encoding="utf-8",
            )

            samples = load_station_number_samples(config)
            model, metrics = train_station_number_model(config)

            self.assertEqual([label for label, _ in samples], [7, 7, 8, 8])
            self.assertEqual(model.labels, (7, 7, 8, 8))
            self.assertEqual(metrics["samples_per_class"], {"7": 2, "8": 2})
            self.assertEqual(metrics["validation_samples"], 4)
            self.assertEqual(metrics["validation_accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
