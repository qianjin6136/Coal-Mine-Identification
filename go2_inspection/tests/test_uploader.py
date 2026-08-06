from pathlib import Path
import tempfile
import unittest

from app.uploader import encode_multipart
from tests.test_service import MINIMAL_PNG


class UploaderTests(unittest.TestCase):
    def test_multipart_uses_unique_declared_upload_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "same.png"
            image.write_bytes(MINIMAL_PNG)
            body, _ = encode_multipart(
                {"images": ["same.png", "02_same.png"]},
                [image, image],
            )
            self.assertIn(b'filename="same.png"', body)
            self.assertIn(b'filename="02_same.png"', body)

    def test_multipart_normalizes_windows_path_on_linux(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "frame_01.png"
            image.write_bytes(MINIMAL_PNG)
            body, _ = encode_multipart(
                {"images": [r"C:\go2_queue\frame_01.png"]},
                [image],
            )
            self.assertIn(b'filename="frame_01.png"', body)
            self.assertNotIn(b'filename="C:', body)

if __name__ == "__main__":
    unittest.main()
