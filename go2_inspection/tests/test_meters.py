import unittest

from app.meters.analog import (
    angular_distance_deg,
    classify_pointer_status,
    detect_red_pointer_angle_image,
)
from app.meters.digital import (
    SevenSegmentReader,
    decode_segments,
    majority_vote_readings,
)


class MeterTests(unittest.TestCase):
    def test_angular_distance_wraps(self) -> None:
        self.assertEqual(angular_distance_deg(359, 1), 2)

    def test_pointer_status(self) -> None:
        normal = classify_pointer_status(44, 42, 5, 0.9)
        abnormal = classify_pointer_status(60, 42, 5, 0.9)
        uncertain = classify_pointer_status(None, 42, 5, 0.1)
        self.assertEqual(normal.status, "normal")
        self.assertEqual(abnormal.status, "abnormal")
        self.assertEqual(uncertain.status, "uncertain")

    def test_segment_mapping(self) -> None:
        self.assertEqual(decode_segments("acdefg"), "6")
        self.assertEqual(decode_segments("abcdefg"), "8")
        self.assertIsNone(decode_segments("a"))

    def test_digital_majority_vote(self) -> None:
        reading = majority_vote_readings(["600.0", "600.0", "600.8"])
        self.assertEqual(reading.status, "confirmed")
        self.assertEqual(reading.raw_text, "600.0")
        self.assertEqual(reading.value, 600.0)

        unreadable = majority_vote_readings(["1.0", "2.0", "3.0"])
        self.assertEqual(unreadable.status, "unreadable")

    def test_red_pointer_angle_on_synthetic_roi(self) -> None:
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("OpenCV not available")
        image = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.line(image, (100, 100), (175, 100), (0, 0, 255), 5)
        angle, confidence = detect_red_pointer_angle_image(
            image,
            center_xy=(100, 100),
        )
        self.assertIsNotNone(angle)
        self.assertLess(angular_distance_deg(float(angle), 0.0), 3.0)
        self.assertGreater(confidence, 0.5)

    def test_seven_segment_reader_decodes_synthetic_eight(self) -> None:
        try:
            import numpy as np
        except ImportError:
            self.skipTest("NumPy not available")
        mask = np.full((100, 60), 255, dtype=np.uint8)
        self.assertEqual(SevenSegmentReader()._decode_digit(mask), "8")


if __name__ == "__main__":
    unittest.main()
