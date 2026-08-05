import unittest

import cv2
import numpy as np

from app.meters.digital_model import (
    DigitalMeterRecognizer,
    TemplateDigitModel,
    normalize_digit_mask,
)


SEGMENTS = {
    "0": "abcedf",
    "1": "bc",
    "2": "abdeg",
    "3": "abcdg",
    "4": "bcfg",
    "5": "acdfg",
    "6": "acdefg",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcdfg",
}


def _digit_mask(value: str) -> np.ndarray:
    mask = np.zeros((100, 64), dtype=np.uint8)
    coordinates = {
        "a": (12, 5, 52, 11),
        "b": (51, 9, 58, 49),
        "c": (51, 50, 58, 91),
        "d": (12, 89, 52, 96),
        "e": (6, 50, 13, 91),
        "f": (6, 9, 13, 49),
        "g": (12, 47, 52, 54),
    }
    for segment in SEGMENTS[value]:
        x1, y1, x2, y2 = coordinates[segment]
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, thickness=-1)
    return mask


def _model() -> TemplateDigitModel:
    templates = [normalize_digit_mask(_digit_mask(value)) for value in "0123456789"]
    return TemplateDigitModel(
        np.asarray(templates),
        list("0123456789"),
        [f"synthetic-{value}" for value in "0123456789"],
    )


def _display(text: str) -> np.ndarray:
    characters = list(text.replace(".", ""))
    image = np.zeros((140, 480, 3), dtype=np.uint8)
    x = 20
    digit_index = 0
    for character in characters:
        if character == "-":
            cv2.rectangle(image, (x + 12, 66), (x + 52, 72), (0, 0, 255), -1)
        else:
            mask = _digit_mask(character)
            roi = image[20:120, x : x + 64]
            roi[mask > 0] = (0, 0, 255)
            digit_index += 1
            if "." in text and digit_index == len(text.split(".")[0].lstrip("-")):
                cv2.circle(image, (x + 70, 113), 4, (0, 0, 255), -1)
        x += 84
    return image


class DigitalMeterWorstCaseFormatTests(unittest.TestCase):
    def test_leading_zero_and_nonzero_fraction(self) -> None:
        result = DigitalMeterRecognizer(
            _model(),
            digit_count=4,
            decimal_places=1,
            minimum_confidence=0.0,
        ).read_image(_display("067.8"))
        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.raw_text, "67.8")
        self.assertEqual(result.value, 67.8)

    def test_variable_three_or_four_digit_display(self) -> None:
        recognizer = DigitalMeterRecognizer(
            _model(),
            digit_count=(3, 4),
            decimal_places=2,
            minimum_confidence=0.0,
        )
        self.assertEqual(recognizer.read_image(_display("0.04")).raw_text, "0.04")
        self.assertEqual(recognizer.read_image(_display("12.34")).raw_text, "12.34")

    def test_negative_sign_can_use_a_dedicated_position(self) -> None:
        result = DigitalMeterRecognizer(
            _model(),
            digit_count=4,
            decimal_places=1,
            allow_negative=True,
            minimum_confidence=0.0,
        ).read_image(_display("-067.8"))
        self.assertEqual(result.raw_text, "-67.8")
        self.assertEqual(result.value, -67.8)

    def test_negative_sign_can_replace_the_highest_digit(self) -> None:
        result = DigitalMeterRecognizer(
            _model(),
            digit_count=4,
            decimal_places=1,
            allow_negative=True,
            minimum_confidence=0.0,
        ).read_image(_display("-67.8"))
        self.assertEqual(result.raw_text, "-67.8")
        self.assertEqual(result.value, -67.8)


if __name__ == "__main__":
    unittest.main()
