import io
import json
from pathlib import Path
import tempfile
import unittest

from app.uploader import encode_multipart, process_upload_queue
from tests.test_service import MINIMAL_PNG


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


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

    def test_queue_keeps_package_and_writes_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / "capture_001"
            package.mkdir()
            (package / "frame_01.png").write_bytes(MINIMAL_PNG)
            (package / "metadata.json").write_text(
                json.dumps(
                    {
                        "capture_id": "capture_001",
                        "capture_time": "2026-07-29T12:00:00+08:00",
                        "station_id": "08",
                        "robot_pose": {"x_m": 1, "y_m": 2, "yaw_deg": 3},
                        "camera_id": "go2_front",
                        "images": ["frame_01.png"],
                    }
                ),
                encoding="utf-8",
            )

            def opener(http_request, timeout):
                self.assertEqual(http_request.method, "POST")
                self.assertEqual(timeout, 5)
                return FakeResponse(
                    json.dumps(
                        {"capture_id": "capture_001", "status": "processed"}
                    ).encode()
                )

            first = process_upload_queue(
                root,
                "http://example.invalid",
                timeout=5,
                opener=opener,
            )
            self.assertEqual(first["uploaded"], ["capture_001"])
            self.assertTrue((package / "frame_01.png").is_file())
            self.assertTrue((package / "upload_receipt.json").is_file())

            second = process_upload_queue(
                root,
                "http://example.invalid",
                timeout=5,
                opener=lambda *_args, **_kwargs: self.fail("must not upload twice"),
            )
            self.assertEqual(len(second["skipped_with_receipt"]), 1)

    def test_queue_accepts_windows_separators_on_linux(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / "capture_002"
            image_dir = package / "images"
            image_dir.mkdir(parents=True)
            (image_dir / "frame_01.png").write_bytes(MINIMAL_PNG)
            (package / "metadata.json").write_text(
                json.dumps(
                    {
                        "capture_id": "capture_002",
                        "capture_time": "2026-07-29T12:00:00+08:00",
                        "station_id": "08",
                        "robot_pose": {},
                        "camera_id": "go2_front",
                        "images": [r"images\frame_01.png"],
                    }
                ),
                encoding="utf-8",
            )

            def opener(http_request, timeout):
                self.assertIn(b'filename="frame_01.png"', http_request.data)
                return FakeResponse(
                    json.dumps(
                        {"capture_id": "capture_002", "status": "processed"}
                    ).encode()
                )

            result = process_upload_queue(root, "http://example.invalid", opener=opener)
            self.assertEqual(result["uploaded"], ["capture_002"])


if __name__ == "__main__":
    unittest.main()
