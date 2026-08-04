import json
from pathlib import Path
import tempfile
import unittest

from app.errors import ValidationError
from app.runtime_settings import (
    RuntimeSettingsManager,
    build_runtime_defaults,
    merge_module_runtime_settings,
)
from app.inference import gpu_inference_status, runtime_mode_for_backend


class RuntimeSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module_config = {
            "tool_and_safety_sign": {"enabled": False},
            "coal_presence": {"enabled": True},
            "station_number": {"enabled": True},
            "digital_meter": {
                "enabled": True,
                "minimum_frame_confidence": 0.55,
                "digit_count": 4,
            },
            "analog_meter": {"enabled": False},
        }
        self.defaults = build_runtime_defaults(
            detector_confidence=0.35,
            fusion_iou=0.45,
            module_config=self.module_config,
        )

    def test_update_persist_reload_and_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime_settings.json"
            manager = RuntimeSettingsManager(path, self.defaults)
            updated = manager.update(
                {
                    "detector": {"confidence": 0.62},
                    "modules": {"coal_presence": False},
                }
            )
            self.assertEqual(updated["detector"]["confidence"], 0.62)
            self.assertFalse(updated["modules"]["coal_presence"])
            self.assertTrue(path.is_file())

            reloaded = RuntimeSettingsManager(path, self.defaults)
            self.assertEqual(reloaded.snapshot(), updated)
            restored = reloaded.reset()
            self.assertEqual(restored, self.defaults)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                self.defaults,
            )

    def test_rejects_unknown_fields_and_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = RuntimeSettingsManager(
                Path(temp_dir) / "runtime_settings.json",
                self.defaults,
            )
            with self.assertRaises(ValidationError):
                manager.update({"unknown": {"value": 1}})
            with self.assertRaises(ValidationError):
                manager.update({"detector": {"confidence": 1.1}})
            with self.assertRaises(ValidationError):
                manager.update({"pipeline": {"fusion_iou": True}})
            with self.assertRaises(ValidationError):
                manager.update({"modules": {"not_a_module": True}})
            with self.assertRaises(ValidationError):
                manager.update({"detector": {"mode": "cpu"}})

    def test_module_overrides_preserve_non_runtime_configuration(self) -> None:
        runtime = {
            **self.defaults,
            "digital_meter": {"minimum_frame_confidence": 0.72},
            "modules": {
                **self.defaults["modules"],
                "digital_meter": False,
            },
        }
        merged = merge_module_runtime_settings(self.module_config, runtime)
        self.assertFalse(merged["digital_meter"]["enabled"])
        self.assertEqual(
            merged["digital_meter"]["minimum_frame_confidence"],
            0.72,
        )
        self.assertEqual(merged["digital_meter"]["digit_count"], 4)

    def test_backend_names_map_to_console_modes(self) -> None:
        self.assertEqual(runtime_mode_for_backend("noop"), "noop")
        self.assertEqual(runtime_mode_for_backend("ultralytics"), "gpu")
        self.assertEqual(runtime_mode_for_backend("json_replay"), "json_replay")

    def test_gpu_status_explains_missing_weights(self) -> None:
        status = gpu_inference_status(None)
        self.assertFalse(status["available"])
        self.assertEqual(status["reason_code"], "weights_not_configured")
        self.assertIn("detector.weights", status["reason"])


if __name__ == "__main__":
    unittest.main()
