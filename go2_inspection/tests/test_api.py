from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.api import create_app
from tests.test_service import MINIMAL_PNG


class ApiTests(unittest.TestCase):
    def test_health_upload_query_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            classes_path = root / "classes.json"
            stations_path = root / "stations.json"
            analog_path = root / "analog.json"
            modules_path = root / "modules.json"
            settings_path = root / "app.json"
            classes_path.write_text("{}", encoding="utf-8")
            stations_path.write_text(
                json.dumps({"08": {"location_name": "08号区段"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            analog_path.write_text("{}", encoding="utf-8")
            modules_path.write_text(
                json.dumps(
                    {
                        "tool_and_safety_sign": {"enabled": False},
                        "coal_presence": {"enabled": False},
                        "station_number": {"enabled": False},
                        "digital_meter": {"enabled": False},
                        "analog_meter": {"enabled": False},
                    }
                ),
                encoding="utf-8",
            )
            settings_path.write_text(
                json.dumps(
                    {
                        "storage_root": str(root / "data"),
                        "database_path": str(root / "data" / "inspection.db"),
                        "classes_path": str(classes_path),
                        "stations_path": str(stations_path),
                        "modules_path": str(modules_path),
                        "analog_references_path": str(analog_path),
                        "detector": {
                            "backend": "noop",
                            "weights": None,
                            "confidence": 0.35,
                        },
                    }
                ),
                encoding="utf-8",
            )
            previous = os.environ.get("GO2_INSPECTION_SETTINGS")
            os.environ["GO2_INSPECTION_SETTINGS"] = str(settings_path)
            try:
                client = TestClient(create_app())
            finally:
                if previous is None:
                    os.environ.pop("GO2_INSPECTION_SETTINGS", None)
                else:
                    os.environ["GO2_INSPECTION_SETTINGS"] = previous

            with client:
                health = client.get("/health")
                self.assertEqual(health.status_code, 200)
                self.assertFalse(health.json()["detector_configured"])

                metadata = {
                    "capture_id": "api_test",
                    "capture_time": datetime.now(timezone.utc).isoformat(),
                    "station_id": "08",
                    "robot_pose": {
                        "frame": "map",
                        "x_m": 1.0,
                        "y_m": 2.0,
                        "yaw_deg": 3.0,
                    },
                    "camera_id": "test",
                    "images": ["frame_01.png"],
                }
                response = client.post(
                    "/api/v1/captures",
                    data={"metadata": json.dumps(metadata)},
                    files={"images": ("frame_01.png", MINIMAL_PNG, "image/png")},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "processed")
                self.assertEqual(
                    response.json()["recognition_summary"]["status"],
                    "unrecognized",
                )
                self.assertEqual(
                    response.json()["result"]["processing_parameters"][
                        "detector"
                    ]["confidence"],
                    0.35,
                )

                queried = client.get("/api/v1/results/api_test")
                self.assertEqual(queried.status_code, 200)
                self.assertEqual(queried.json()["station_id"], "08")

                retry = client.post(
                    "/api/v1/captures",
                    data={"metadata": json.dumps(metadata)},
                    files={"images": ("frame_01.png", MINIMAL_PNG, "image/png")},
                )
                self.assertEqual(retry.status_code, 200)
                self.assertTrue(retry.json()["idempotent_replay"])

                listed = client.get("/api/v1/captures")
                self.assertEqual(listed.status_code, 200)
                self.assertEqual(listed.json()["total"], 1)
                self.assertIn(
                    "recognition_summary",
                    listed.json()["items"][0],
                )
                matching = client.get(
                    "/api/v1/captures?capture_id=API_TEST"
                )
                self.assertEqual(matching.status_code, 200)
                self.assertEqual(matching.json()["total"], 1)
                not_matching = client.get(
                    "/api/v1/captures?capture_id=missing"
                )
                self.assertEqual(not_matching.status_code, 200)
                self.assertEqual(not_matching.json()["total"], 0)

                corrected = client.patch(
                    "/api/v1/results/api_test/correction",
                    json={
                        "operator": "api-test",
                        "reason": "checked",
                        "objects": [
                            {
                                "type": "tool",
                                "class": "wrench",
                                "bbox_xyxy": [1, 1, 10, 10],
                                "confidence": 0.99,
                            }
                        ],
                    },
                )
                self.assertEqual(corrected.status_code, 200, corrected.text)
                self.assertTrue(corrected.json()["manually_corrected"])

                exported_json = client.get("/api/v1/export?format=json")
                self.assertEqual(exported_json.status_code, 200)
                self.assertEqual(
                    exported_json.json()[0]["class"],
                    "wrench",
                )
                exported_csv = client.get("/api/v1/export?format=csv")
                self.assertEqual(exported_csv.status_code, 200)
                self.assertIn("capture_id", exported_csv.text)

                original = client.get("/api/v1/captures/api_test/images/0")
                self.assertEqual(original.status_code, 200)

                reprocessed = client.post("/api/v1/results/api_test/reprocess")
                self.assertEqual(reprocessed.status_code, 200)
                self.assertFalse(reprocessed.json()["manually_corrected"])

                runtime = client.get("/api/v1/runtime-settings")
                self.assertEqual(runtime.status_code, 200)
                self.assertEqual(
                    runtime.json()["current"]["detector"]["mode"],
                    "noop",
                )
                self.assertFalse(runtime.json()["inference"]["gpu"]["available"])
                self.assertEqual(
                    runtime.json()["inference"]["gpu"]["reason_code"],
                    "weights_not_configured",
                )
                self.assertEqual(
                    runtime.json()["current"]["pipeline"]["fusion_iou"],
                    0.45,
                )
                updated_runtime = client.patch(
                    "/api/v1/runtime-settings",
                    json={
                        "detector": {"confidence": 0.61},
                        "pipeline": {"fusion_iou": 0.31},
                        "digital_meter": {
                            "minimum_frame_confidence": 0.72,
                        },
                        "modules": {"coal_presence": False},
                    },
                )
                self.assertEqual(
                    updated_runtime.status_code,
                    200,
                    updated_runtime.text,
                )
                self.assertEqual(
                    updated_runtime.json()["current"]["detector"]["confidence"],
                    0.61,
                )
                rejected_gpu = client.patch(
                    "/api/v1/runtime-settings",
                    json={"detector": {"mode": "gpu"}},
                )
                self.assertEqual(rejected_gpu.status_code, 400)
                self.assertIn("GPU 模式不可用", rejected_gpu.json()["detail"])
                self.assertEqual(
                    client.get("/api/v1/runtime-settings").json()["current"]
                    ["detector"]["mode"],
                    "noop",
                )
                self.assertTrue(
                    (root / "data" / "runtime_settings.json").is_file()
                )
                rerun_with_new_parameters = client.post(
                    "/api/v1/results/api_test/reprocess"
                )
                self.assertEqual(rerun_with_new_parameters.status_code, 200)
                self.assertEqual(
                    rerun_with_new_parameters.json()["result"][
                        "processing_parameters"
                    ]["pipeline"]["fusion_iou"],
                    0.31,
                )
                invalid_runtime = client.patch(
                    "/api/v1/runtime-settings",
                    json={"detector": {"unexpected": 1}},
                )
                self.assertEqual(invalid_runtime.status_code, 400)
                reset_runtime = client.post(
                    "/api/v1/runtime-settings/reset"
                )
                self.assertEqual(reset_runtime.status_code, 200)
                self.assertEqual(
                    reset_runtime.json()["current"]["detector"]["confidence"],
                    0.35,
                )

                page = client.get("/ui")
                self.assertEqual(page.status_code, 200)
                self.assertIn("GO2 图片传输与参数调试工作台", page.text)
                self.assertIn("U 盘离线数据", page.text)
                self.assertIn('id="batch-filter"', page.text)
                self.assertEqual(client.get("/static/ui.css").status_code, 200)
                ui_script = client.get("/static/ui.js")
                self.assertEqual(ui_script.status_code, 200)
                self.assertIn("下载Word报告", ui_script.text)

                incoming_capture = root / "data" / "incoming" / "api_test"
                processed_capture = root / "data" / "processed" / "api_test"
                self.assertTrue(incoming_capture.is_dir())
                self.assertTrue(processed_capture.is_dir())
                deleted = client.delete("/api/v1/captures/api_test")
                self.assertEqual(deleted.status_code, 200, deleted.text)
                self.assertTrue(deleted.json()["deleted"])
                self.assertEqual(deleted.json()["cleanup_warnings"], [])
                self.assertFalse(incoming_capture.exists())
                self.assertFalse(processed_capture.exists())
                self.assertEqual(client.get("/api/v1/captures").json()["total"], 0)
                self.assertEqual(client.get("/health").json()["captures_total"], 0)
                self.assertEqual(
                    client.get("/api/v1/results/api_test").status_code,
                    404,
                )
                self.assertEqual(
                    client.delete("/api/v1/captures/api_test").status_code,
                    404,
                )


if __name__ == "__main__":
    unittest.main()
