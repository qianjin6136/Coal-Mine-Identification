from pathlib import Path
import tempfile
import unittest

import numpy as np

from app.image_io import read_bgr_image, write_bgr_image


class UnicodeImageIoTests(unittest.TestCase):
    def test_read_and_write_unicode_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "数字表样本"
            folder.mkdir()
            source = folder / "读数一.png"
            expected = np.zeros((24, 32, 3), dtype=np.uint8)
            expected[:, :, 2] = 255
            self.assertTrue(write_bgr_image(source, expected))
            actual = read_bgr_image(source)
            self.assertIsNotNone(actual)
            self.assertEqual(actual.shape, expected.shape)
            self.assertGreater(int(actual[:, :, 2].mean()), 250)


if __name__ == "__main__":
    unittest.main()
