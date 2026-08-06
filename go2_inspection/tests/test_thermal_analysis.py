from datetime import datetime, timedelta, timezone
import json
import unittest

from app.errors import ValidationError
from app.thermal_analysis import (
    THERMAL_STATS_METADATA_KEY,
    analyze_thermal_samples,
    parse_thermal_stats,
)


CHINA_TIMEZONE = timezone(timedelta(hours=8))


def sample(timestamp: str, maximum_c: float, sample_id: int) -> dict:
    compact = datetime.fromisoformat(timestamp).strftime("%Y%m%d_%H%M%S")
    return {
        "sample_key": f"{compact}_{sample_id:06d}",
        "captured_at": timestamp,
        "sample_id": f"{sample_id:06d}",
        "thermal_stored_path": f"thermal_{compact}_{sample_id:06d}.png",
        "thermal_maximum_c": maximum_c,
        "thermal_metadata_status": "valid",
    }


def capture(timestamp: str, number: int, suffix: str) -> dict:
    return {
        "capture_id": f"capture_{suffix}",
        "capture_time": timestamp,
        "result": {
            "modules": {
                "station_number": {
                    "status": "confirmed",
                    "number": number,
                    "confidence": 0.9,
                }
            }
        },
    }


class ThermalMetadataTests(unittest.TestCase):
    def test_parses_versioned_stats_and_checks_filename_identity(self) -> None:
        raw = json.dumps(
            {
                "schema_version": 1,
                "captured_at": "2026-08-04T15:30:05+08:00",
                "sample_id": 7,
                "width": 32,
                "height": 24,
                "minimum_c": 24.0,
                "maximum_c": 65.01,
                "average_c": 30.0,
            }
        )

        stats = parse_thermal_stats(
            raw,
            expected_timestamp=datetime(
                2026, 8, 4, 15, 30, 5, tzinfo=CHINA_TIMEZONE
            ),
            expected_sample_id="000007",
        )

        self.assertEqual(stats.maximum_c, 65.01)
        self.assertEqual(stats.sample_id, "000007")
        with self.assertRaisesRegex(ValidationError, "sample_id does not match"):
            parse_thermal_stats(
                raw,
                expected_timestamp=datetime(
                    2026, 8, 4, 15, 30, 5, tzinfo=CHINA_TIMEZONE
                ),
                expected_sample_id="000008",
            )

    def test_rejects_missing_and_inconsistent_stats(self) -> None:
        with self.assertRaisesRegex(ValidationError, THERMAL_STATS_METADATA_KEY):
            parse_thermal_stats(
                None,
                expected_timestamp=datetime.now(CHINA_TIMEZONE),
                expected_sample_id="000001",
            )
        raw = json.dumps(
            {
                "schema_version": 1,
                "captured_at": "2026-08-04T15:30:05+08:00",
                "sample_id": 1,
                "width": 32,
                "height": 24,
                "minimum_c": 30.0,
                "maximum_c": 20.0,
                "average_c": 25.0,
            }
        )
        with self.assertRaisesRegex(ValidationError, "minimum <= average <= maximum"):
            parse_thermal_stats(
                raw,
                expected_timestamp=datetime(
                    2026, 8, 4, 15, 30, 5, tzinfo=CHINA_TIMEZONE
                ),
                expected_sample_id="000001",
            )


class ThermalAnalysisTests(unittest.TestCase):
    def test_threshold_and_cooldown_boundaries_use_reported_event_time(self) -> None:
        samples = [
            sample("2026-08-05T08:59:59+08:00", 64.99, 1),
            sample("2026-08-05T09:00:00+08:00", 65.0, 2),
            sample("2026-08-05T09:00:01+08:00", 65.01, 3),
            sample("2026-08-05T09:00:10.999000+08:00", 70.0, 4),
            sample("2026-08-05T09:00:11+08:00", 71.0, 5),
        ]
        captures = [
            capture("2026-08-05T09:00:01+08:00", 7, "one"),
            capture("2026-08-05T09:00:10.999000+08:00", 7, "two"),
            capture("2026-08-05T09:00:11+08:00", 7, "three"),
        ]

        analysis = analyze_thermal_samples(samples, captures)

        self.assertEqual(len(analysis.candidates), 3)
        self.assertEqual(analysis.suppressed_count, 1)
        self.assertEqual(
            [event.sample_key for event in analysis.events],
            [samples[2]["sample_key"], samples[4]["sample_key"]],
        )

    def test_different_numbers_and_unknown_numbers_are_not_suppressed(self) -> None:
        samples = [
            sample("2026-08-05T09:00:00+08:00", 70.0, 1),
            sample("2026-08-05T09:00:01+08:00", 71.0, 2),
        ]
        different = analyze_thermal_samples(
            samples,
            [
                capture("2026-08-05T09:00:00+08:00", 7, "seven"),
                capture("2026-08-05T09:00:01+08:00", 8, "eight"),
            ],
            association_window_seconds=0.1,
        )
        unknown = analyze_thermal_samples(samples, [])

        self.assertEqual([event.station_number for event in different.events], [7, 8])
        self.assertEqual(len(unknown.events), 2)
        self.assertTrue(all(event.station_number is None for event in unknown.events))

    def test_nearest_number_must_be_within_three_seconds(self) -> None:
        samples = [sample("2026-08-05T09:00:05+08:00", 70.0, 1)]
        inside = analyze_thermal_samples(
            samples, [capture("2026-08-05T09:00:07.900000+08:00", 9, "inside")]
        )
        outside = analyze_thermal_samples(
            samples, [capture("2026-08-05T09:00:08.001000+08:00", 9, "outside")]
        )

        self.assertEqual(inside.events[0].station_number, 9)
        self.assertIsNone(outside.events[0].station_number)


if __name__ == "__main__":
    unittest.main()
