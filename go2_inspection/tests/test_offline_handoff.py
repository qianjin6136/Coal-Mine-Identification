from pathlib import Path
import unittest

from app.settings import Settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
        self.assertIn("20.04|22.04|24.04", installer)
        self.assertIn("python3.12-venv", installer)
        self.assertIn("--recreate-venv", installer)
        self.assertIn("GO2_BUILD_JOBS", installer)
        self.assertIn(
            "c08bc65a81971c1dd5783182826503369466c7e67374d1646519adf05207b684",
            installer,
        )
        attributes = (PROJECT_ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.sh text eol=lf", attributes)

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

if __name__ == "__main__":
    unittest.main()
