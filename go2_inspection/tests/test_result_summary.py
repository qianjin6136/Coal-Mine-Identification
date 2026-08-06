import unittest

from app.result_summary import build_recognition_summary


class ResultSummaryTests(unittest.TestCase):
    def test_retired_historical_results_are_hidden_during_migration(self) -> None:
        summary = build_recognition_summary(
            capture_status="processed",
            result={
                "objects": [
                    {"type": "tool", "class": "legacy", "confidence": 0.9},
                    {"type": "safety_sign", "class": "legacy", "confidence": 0.8},
                ],
                "modules": {
                    "tool_and_safety_sign": {
                        "enabled": True,
                        "status": "confirmed",
                    }
                },
            },
        )

        self.assertEqual(summary["status"], "unrecognized")
        self.assertEqual(summary["items"], [])

    def test_yolo_detection_and_unreadable_number_are_kept_separate(self) -> None:
        summary = build_recognition_summary(
            capture_status="processed",
            result={
                "objects": [
                    {
                        "type": "station_marker",
                        "class": "station_marker",
                        "class_cn": "区段编号牌",
                        "confidence": 0.982186,
                    }
                ],
                "modules": {
                    "station_number": {
                        "enabled": True,
                        "status": "unreadable",
                        "number": None,
                        "confidence": 0.0,
                        "reason": "no_confirmed_station_number_readings",
                    },
                    "digital_meter": {
                        "enabled": False,
                        "status": "disabled",
                    },
                },
                "processing_parameters": {"detector": {"mode": "gpu"}},
            },
        )

        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["primary"]["source_id"], "yolo")
        self.assertEqual(summary["primary"]["label"], "区段编号牌")
        station = next(
            item for item in summary["items"] if item["source_id"] == "station_number"
        )
        self.assertEqual(station["status"], "unrecognized")
        self.assertIsNone(station["value"])

    def test_confirmed_module_value_is_the_primary_final_result(self) -> None:
        summary = build_recognition_summary(
            capture_status="processed",
            result={
                "objects": [
                    {
                        "type": "station_marker",
                        "class": "station_marker",
                        "class_cn": "区段编号牌",
                        "confidence": 0.96,
                    }
                ],
                "modules": {
                    "station_number": {
                        "enabled": True,
                        "status": "confirmed",
                        "number": 8,
                        "confidence": 0.91,
                    }
                },
                "processing_parameters": {"detector": {"mode": "gpu"}},
            },
        )

        self.assertEqual(summary["status"], "recognized")
        self.assertEqual(summary["primary"]["source_id"], "station_number")
        self.assertEqual(summary["primary"]["value"], 8)
        self.assertEqual(summary["primary"]["display_value"], "8 号")

    def test_failed_capture_does_not_publish_stale_results(self) -> None:
        summary = build_recognition_summary(
            capture_status="failed",
            result={"objects": [{"class": "station_marker"}]},
            error="CUDA out of memory",
        )

        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["items"], [])
        self.assertEqual(summary["error"], "CUDA out of memory")


if __name__ == "__main__":
    unittest.main()
