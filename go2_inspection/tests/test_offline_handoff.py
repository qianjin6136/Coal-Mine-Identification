from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest

from app.settings import Settings
from app.uploader import process_upload_queue


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JPEG = b"\xff\xd8\xffsynthetic"


class JsonResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class OfflineHandoffTests(unittest.TestCase):
    def test_runtime_and_rtx4060_dependency_profiles_are_complete(self) -> None:
        runtime_requirements = (
            PROJECT_ROOT / "requirements-runtime.txt"
        ).read_text(encoding="utf-8")
        gpu_requirements = (
            PROJECT_ROOT / "requirements-gpu-ubuntu4060.txt"
        ).read_text(encoding="utf-8")
        self.assertIn("python-docx", runtime_requirements)
        self.assertIn("torch==2.12.1+cu126", gpu_requirements)
        self.assertIn("torchvision==0.27.1+cu126", gpu_requirements)
        self.assertIn("download.pytorch.org/whl/cu126", gpu_requirements)

        for script_name in ("start_server.ps1", "start_server.sh"):
            script = (PROJECT_ROOT / "scripts" / script_name).read_text(
                encoding="utf-8"
            )
            self.assertIn("docx", script)
            self.assertIn("requirements-runtime.txt", script)

        installer = (PROJECT_ROOT / "scripts" / "install_ubuntu.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("gpu-4060", installer)
        self.assertIn("torch.cuda.is_available()", installer)
        self.assertIn("RTX 4060", installer)

    def test_project_paths_are_self_contained_and_use_sample(self) -> None:
        settings = Settings.load()

        self.assertEqual(settings.dataset_inbox_path, PROJECT_ROOT / "dataset_inbox")
        self.assertEqual(settings.storage_root, PROJECT_ROOT / "runtime_data")
        self.assertEqual(
            settings.database_path,
            PROJECT_ROOT / "runtime_data" / "database" / "inspection.db",
        )
        self.assertTrue((PROJECT_ROOT / "sample" / "编号").is_dir())
        self.assertTrue((PROJECT_ROOT / "models" / "base" / "yolo26n.pt").is_file())
        self.assertFalse((PROJECT_ROOT / "样本").exists())
        self.assertFalse((PROJECT_ROOT / "fig").exists())

        for config_path in (PROJECT_ROOT / "configs").glob("*.json"):
            text = config_path.read_text(encoding="utf-8")
            self.assertNotIn("../.." + "/runtime_data", text, config_path.name)
            self.assertNotIn("../.." + "/样本", text, config_path.name)

    def test_usb_visible_tree_is_recursively_imported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = Path(temp_dir) / "dataset_inbox"
            package = inbox / "visible" / "2026-08-04" / "rpi_test_001"
            package.mkdir(parents=True)
            image_names = ["frame_01.jpg", "frame_02.jpg", "frame_03.jpg"]
            for name in image_names:
                (package / name).write_bytes(JPEG)
            (package / "metadata.json").write_text(
                json.dumps(
                    {
                        "capture_id": "rpi_test_001",
                        "capture_time": "2026-08-04T15:30:05+08:00",
                        "station_id": "08",
                        "camera_id": "raspberry_pi_usb",
                        "robot_pose": {
                            "frame": "map",
                            "x_m": None,
                            "y_m": None,
                            "yaw_deg": None,
                        },
                        "images": image_names,
                        "batch_id": None,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            def opener(_request, timeout):
                self.assertEqual(timeout, 60.0)
                return JsonResponse(b'{"capture_id":"rpi_test_001"}')

            summary = process_upload_queue(
                inbox / "visible",
                "http://127.0.0.1:8000",
                opener=opener,
            )

            self.assertEqual(summary["uploaded"], ["rpi_test_001"])
            self.assertEqual(summary["failed"], [])
            self.assertTrue((package / "upload_receipt.json").is_file())


if __name__ == "__main__":
    unittest.main()
