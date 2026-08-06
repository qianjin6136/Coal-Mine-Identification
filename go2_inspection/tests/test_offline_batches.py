import csv
import base64
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from app.api import create_app
from app.detectors.noop import NoopDetector
from app.errors import ValidationError
from app.offline_import import OfflineBatchManager
from app.pipeline import InspectionPipeline
from app.service import InspectionService
from app.storage import CaptureRepository
from app.thermal_analysis import THERMAL_STATS_METADATA_KEY


MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
GAS_FIELDS = ["timestamp", "sample_id"] + [
    f"{channel}_{suffix}"
    for channel in ("ch4", "o2", "co", "h2s")
    for suffix in ("value", "unit", "status")
] + ["error"]
CHINESE_GAS_FIELDS = [
    "时间",
    "编号",
    "CH4(%LEL)",
    "O2(%VOL)",
    "CO(ppm)",
    "H2S(ppm)",
    "状态",
]


def thermal_png(
    captured_at: str,
    sample_id: str,
    *,
    minimum_c: float = 24.0,
    maximum_c: float = 31.0,
    average_c: float = 27.0,
) -> bytes:
    image = Image.new("RGB", (640, 544), "navy")
    info = PngImagePlugin.PngInfo()
    info.add_text(
        THERMAL_STATS_METADATA_KEY,
        json.dumps(
            {
                "schema_version": 1,
                "captured_at": captured_at,
                "sample_id": int(sample_id),
                "width": 32,
                "height": 24,
                "minimum_c": minimum_c,
                "maximum_c": maximum_c,
                "average_c": average_c,
            },
            separators=(",", ":"),
        ),
    )
    output = BytesIO()
    image.save(output, format="PNG", pnginfo=info)
    return output.getvalue()


def build_service(root: Path) -> tuple[InspectionService, CaptureRepository]:
    repository = CaptureRepository(root / "inspection.db", root / "runtime")
    pipeline = InspectionPipeline(
        NoopDetector(),
        repository.processed_root,
        stations={"08": {"location_name": "08号区段"}},
    )
    return InspectionService(repository, pipeline, 1024 * 1024), repository


def write_batch(
    inbox: Path,
    *,
    capture_id: str = "rpi_20260804_153005_123456",
    bad_visible: bool = False,
    thermal_sample_id: str = "000001",
) -> Path:
    batch = inbox
    package = batch / "visible" / "2026-08-04" / capture_id
    package.mkdir(parents=True)
    image_names = ["frame_01.png", "frame_02.png", "frame_03.png"]
    for name in image_names:
        (package / name).write_bytes(b"not-an-image" if bad_visible else MINIMAL_PNG)
    (package / "metadata.json").write_text(
        json.dumps(
            {
                "capture_id": capture_id,
                "capture_time": "2026-08-04T15:30:05+08:00",
                "station_id": "08",
                "robot_pose": {
                    "frame": "map",
                    "x_m": None,
                    "y_m": None,
                    "yaw_deg": None,
                },
                "camera_id": "raspberry_pi_usb",
                "images": image_names,
                "batch_id": None,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    gas_root = batch / "gas"
    gas_root.mkdir(parents=True)
    row = dict.fromkeys(GAS_FIELDS, "")
    row.update(
        {
            "timestamp": "2026-08-04T15:30:05+08:00",
            "sample_id": "000001",
            "ch4_value": "1.25",
            "ch4_unit": "%LEL",
            "ch4_status": "ok",
            "o2_value": "20.9",
            "o2_unit": "%VOL",
            "o2_status": "ok",
            "co_value": "2",
            "co_unit": "ppm",
            "co_status": "ok",
            "h2s_value": "0",
            "h2s_unit": "ppm",
            "h2s_status": "ok",
        }
    )
    with (gas_root / "gas_2026-08-04.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=GAS_FIELDS)
        writer.writeheader()
        writer.writerow(row)

    thermal_root = batch / "thermal"
    thermal_root.mkdir(parents=True)
    (thermal_root / f"thermal_20260804_153005_{thermal_sample_id}.png").write_bytes(
        thermal_png(
            "2026-08-04T15:30:05+08:00",
            thermal_sample_id,
        )
    )
    return batch


def write_flat_batch(
    inbox: Path,
    *,
    bad_visible: bool = False,
    timestamp: str = "20260211_073025_706675",
) -> Path:
    batch = inbox
    compact_date, compact_time, _microsecond = timestamp.split("_")
    dashed_date = datetime.strptime(compact_date, "%Y%m%d").strftime("%Y-%m-%d")
    captured_at = (
        f"{dashed_date}T{compact_time[:2]}:{compact_time[2:4]}:"
        f"{compact_time[4:]}+08:00"
    )
    visible_root = batch / "visible"
    visible_root.mkdir(parents=True)
    (visible_root / f"color_{timestamp}.jpg").write_bytes(
        b"not-an-image" if bad_visible else MINIMAL_PNG
    )

    gas_root = batch / "gas"
    gas_root.mkdir(parents=True)
    with (gas_root / f"gas_{dashed_date}.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=CHINESE_GAS_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "时间": captured_at,
                "编号": "000001",
                "CH4(%LEL)": "0",
                "O2(%VOL)": "19.5",
                "CO(ppm)": "2",
                "H2S(ppm)": "0",
                "状态": "O2状态：low_alarm",
            }
        )

    thermal_root = batch / "thermal"
    thermal_root.mkdir(parents=True)
    (thermal_root / f"thermal_{compact_date}_{compact_time}_000001.png").write_bytes(
        thermal_png(captured_at, "000001")
    )
    return batch


def wait_for_batch(repository: CaptureRepository, batch_id: str) -> dict:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        batch = repository.get_offline_batch(batch_id)
        if batch["status"] not in {"queued", "running"}:
            return batch
        time.sleep(0.02)
    raise AssertionError("offline batch did not finish")


def confirm_and_wait(
    manager: OfflineBatchManager,
    repository: CaptureRepository,
    batch_id: str,
) -> dict:
    confirmed = manager.confirm_detection(batch_id)
    assert confirmed["detection_confirmed_at"]
    return wait_for_batch(repository, batch_id)


class OfflineBatchTests(unittest.TestCase):
    def test_legacy_queued_batch_migrates_to_detection_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "inspection.db"
            connection = sqlite3.connect(database)
            try:
                connection.executescript(
                    """
                    CREATE TABLE offline_batches (
                        batch_id TEXT PRIMARY KEY,
                        source_path TEXT NOT NULL,
                        discovered_at TEXT NOT NULL,
                        queued_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        status TEXT NOT NULL,
                        sensor_status TEXT NOT NULL DEFAULT 'pending',
                        capture_total INTEGER NOT NULL DEFAULT 0,
                        capture_succeeded INTEGER NOT NULL DEFAULT 0,
                        capture_failed INTEGER NOT NULL DEFAULT 0,
                        gas_row_count INTEGER NOT NULL DEFAULT 0,
                        thermal_frame_count INTEGER NOT NULL DEFAULT 0,
                        warning_count INTEGER NOT NULL DEFAULT 0,
                        diagnostics_json TEXT NOT NULL DEFAULT '[]',
                        error TEXT
                    );
                    INSERT INTO offline_batches (
                        batch_id, source_path, discovered_at, queued_at, status
                    ) VALUES (
                        'legacy-batch', '.', '2026-08-05T00:00:00+00:00',
                        '2026-08-05T00:00:00+00:00', 'queued'
                    );
                    """
                )
                connection.commit()
            finally:
                connection.close()

            repository = CaptureRepository(database, root / "runtime")
            migrated = repository.get_offline_batch("legacy-batch")
            self.assertEqual(
                migrated["status"], "awaiting_detection_confirmation"
            )
            self.assertIsNone(migrated["detection_confirmed_at"])
            self.assertIsNone(migrated["report_confirmed_at"])
            self.assertTrue(migrated["can_start_detection"])

    def test_discovers_imports_archives_and_filters_complete_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "dataset_inbox"
            source = write_batch(inbox)
            original_metadata = next(source.rglob("metadata.json")).read_bytes()
            service, repository = build_service(root)
            manager = OfflineBatchManager(inbox, repository, service)
            service.attach_offline_batches(manager)
            try:
                discovered = manager.discover_batches()
                self.assertEqual(discovered["items"][0]["status"], "discovered")
                self.assertEqual(discovered["items"][0]["capture_total"], 1)
                batch_id = discovered["items"][0]["batch_id"]
                self.assertRegex(batch_id, r"^direct-[0-9a-f]{16}$")
                self.assertEqual(
                    discovered["items"][0]["source_path"], str(source.resolve())
                )

                prepared = manager.queue_import(batch_id)
                self.assertEqual(prepared["batch_id"], batch_id)
                self.assertEqual(
                    prepared["status"], "awaiting_detection_confirmation"
                )
                self.assertFalse(repository.capture_exists("rpi_20260804_153005_123456"))
                finished = confirm_and_wait(manager, repository, batch_id)

                self.assertEqual(finished["status"], "completed")
                self.assertEqual(finished["capture_succeeded"], 1)
                self.assertEqual(finished["gas_row_count"], 1)
                self.assertEqual(finished["thermal_frame_count"], 1)
                listed = service.list_captures(source_batch_id=batch_id)
                self.assertEqual(listed["total"], 1)
                self.assertEqual(
                    listed["items"][0]["source_batch_id"], batch_id
                )
                capture = service.get_capture("rpi_20260804_153005_123456")
                self.assertEqual(capture["source_batch_id"], batch_id)
                archive = root / "runtime" / "imported_batches" / batch_id
                self.assertTrue((archive / "gas" / "gas_2026-08-04.csv").is_file())
                self.assertTrue(
                    (archive / "thermal" / "thermal_20260804_153005_000001.png").is_file()
                )
                sensor_samples = repository.sensor_samples_for_batch(batch_id)
                self.assertEqual(len(sensor_samples), 1)
                self.assertEqual(sensor_samples[0]["ch4_value"], 1.25)
                self.assertIsNotNone(sensor_samples[0]["thermal_stored_path"])
                self.assertEqual(sensor_samples[0]["thermal_minimum_c"], 24.0)
                self.assertEqual(sensor_samples[0]["thermal_maximum_c"], 31.0)
                self.assertEqual(sensor_samples[0]["thermal_average_c"], 27.0)
                self.assertEqual(
                    sensor_samples[0]["thermal_metadata_status"], "valid"
                )
                self.assertEqual(
                    next(source.rglob("metadata.json")).read_bytes(), original_metadata
                )
                self.assertFalse(list(source.rglob("upload_receipt.json")))
            finally:
                manager.stop()

    def test_missing_thermal_metadata_is_archived_and_marked_for_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "dataset_inbox"
            source = write_batch(inbox)
            thermal_source = next((source / "thermal").glob("thermal_*.png"))
            thermal_source.write_bytes(MINIMAL_PNG)
            service, repository = build_service(root)
            manager = OfflineBatchManager(inbox, repository, service)
            try:
                batch_id = manager.discover_batches()["items"][0]["batch_id"]
                manager.queue_import(batch_id)
                finished = confirm_and_wait(manager, repository, batch_id)

                self.assertEqual(finished["status"], "completed_with_errors")
                messages = [item["message"] for item in finished["diagnostics"]]
                self.assertTrue(
                    any(THERMAL_STATS_METADATA_KEY in message for message in messages)
                )
                stored = repository.sensor_samples_for_batch(batch_id)[0]
                self.assertEqual(stored["thermal_metadata_status"], "missing")
                self.assertIsNone(stored["thermal_maximum_c"])
                self.assertTrue(Path(stored["thermal_stored_path"]).is_file())
            finally:
                manager.stop()

    def test_imports_flat_color_image_and_chinese_gas_without_station(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "dataset_inbox"
            source = write_flat_batch(inbox)
            service, repository = build_service(root)
            manager = OfflineBatchManager(inbox, repository, service)
            try:
                discovered = manager.discover_batches()
                self.assertEqual(discovered["items"][0]["capture_total"], 1)
                batch_id = discovered["items"][0]["batch_id"]

                manager.queue_import(batch_id)
                finished = confirm_and_wait(manager, repository, batch_id)

                self.assertEqual(finished["status"], "completed")
                self.assertEqual(finished["capture_succeeded"], 1)
                capture = service.get_capture("color_20260211_073025_706675")
                self.assertEqual(capture["capture_time"], "2026-02-11T07:30:25.706675+08:00")
                self.assertEqual(capture["station_id"], "")
                self.assertEqual(capture["camera_id"], "raspberry_pi_usb")
                self.assertEqual(len(capture["images"]), 1)
                samples = repository.sensor_samples_for_batch(batch_id)
                self.assertEqual(len(samples), 1)
                self.assertEqual(samples[0]["o2_value"], 19.5)
                self.assertEqual(samples[0]["o2_unit"], "%VOL")
                self.assertEqual(samples[0]["gas_error"], "O2状态：low_alarm")
                archived_csv = (
                    root
                    / "runtime"
                    / "imported_batches"
                    / batch_id
                    / "gas"
                    / "gas_2026-02-11.csv"
                )
                self.assertIn("时间,编号", archived_csv.read_text(encoding="utf-8-sig"))
            finally:
                manager.stop()

    def test_bad_flat_image_can_be_fixed_and_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "dataset_inbox"
            source = write_flat_batch(inbox, bad_visible=True)
            image = next((source / "visible").glob("color_*.jpg"))
            service, repository = build_service(root)
            manager = OfflineBatchManager(inbox, repository, service)
            try:
                batch_id = manager.discover_batches()["items"][0]["batch_id"]
                manager.queue_import(batch_id)
                failed = confirm_and_wait(manager, repository, batch_id)
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["capture_failed"], 1)

                image.write_bytes(MINIMAL_PNG)
                self.assertEqual(
                    manager.discover_batches()["items"][0]["batch_id"], batch_id
                )
                manager.retry_batch(batch_id)
                self.assertEqual(
                    repository.get_offline_batch(batch_id)["status"],
                    "awaiting_detection_confirmation",
                )
                finished = confirm_and_wait(manager, repository, batch_id)
                self.assertEqual(finished["status"], "completed")
                self.assertEqual(finished["capture_succeeded"], 1)
            finally:
                manager.stop()

    def test_bad_visible_item_can_be_fixed_and_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "dataset_inbox"
            source = write_batch(inbox, bad_visible=True)
            service, repository = build_service(root)
            manager = OfflineBatchManager(inbox, repository, service)
            try:
                batch_id = manager.discover_batches()["items"][0]["batch_id"]
                manager.queue_import(batch_id)
                failed = confirm_and_wait(manager, repository, batch_id)
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["capture_failed"], 1)

                for image in source.rglob("frame_*.png"):
                    image.write_bytes(MINIMAL_PNG)
                self.assertEqual(
                    manager.discover_batches()["items"][0]["batch_id"], batch_id
                )
                report_confirmed = manager.confirm_report(batch_id)
                self.assertTrue(report_confirmed["report_available"])
                prepared_retry = manager.retry_batch(batch_id)
                self.assertFalse(prepared_retry["report_available"])
                self.assertIsNone(prepared_retry["detection_confirmed_at"])
                finished = confirm_and_wait(manager, repository, batch_id)
                self.assertEqual(finished["status"], "completed")
                self.assertEqual(finished["capture_succeeded"], 1)
            finally:
                manager.stop()

    def test_unmatched_sensor_data_is_preserved_as_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "dataset_inbox"
            write_batch(inbox, thermal_sample_id="000002")
            service, repository = build_service(root)
            manager = OfflineBatchManager(inbox, repository, service)
            try:
                batch_id = manager.discover_batches()["items"][0]["batch_id"]
                manager.queue_import(batch_id)
                finished = confirm_and_wait(manager, repository, batch_id)
                self.assertEqual(finished["status"], "completed_with_errors")
                messages = [item["message"] for item in finished["diagnostics"]]
                self.assertTrue(any("missing_thermal_frame" in item for item in messages))
                self.assertTrue(any("missing_gas_row" in item for item in messages))
                self.assertEqual(finished["capture_succeeded"], 1)
            finally:
                manager.stop()

    def test_replacing_three_directories_discovers_a_new_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "dataset_inbox"
            write_flat_batch(inbox)
            service, repository = build_service(root)
            manager = OfflineBatchManager(inbox, repository, service)
            try:
                first_id = next(
                    item["batch_id"]
                    for item in manager.discover_batches()["items"]
                    if item["source_available"]
                )
                manager.queue_import(first_id)
                self.assertEqual(
                    confirm_and_wait(manager, repository, first_id)["status"], "completed"
                )

                for directory in ("gas", "thermal", "visible"):
                    shutil.rmtree(inbox / directory)
                write_flat_batch(inbox, timestamp="20260212_083025_706675")

                discovered = manager.discover_batches()["items"]
                current = next(item for item in discovered if item["source_available"])
                previous = next(
                    item for item in discovered if item["batch_id"] == first_id
                )
                second_id = current["batch_id"]
                self.assertNotEqual(second_id, first_id)
                self.assertFalse(previous["source_available"])
                with self.assertRaises(ValidationError):
                    manager.retry_batch(first_id)

                manager.queue_import(second_id)
                self.assertEqual(
                    confirm_and_wait(manager, repository, second_id)["status"], "completed"
                )
                self.assertEqual(
                    service.list_captures(source_batch_id=first_id)["total"], 1
                )
                self.assertEqual(
                    service.list_captures(source_batch_id=second_id)["total"], 1
                )
            finally:
                manager.stop()

    def test_startup_resumes_interrupted_batch_from_unfinished_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "dataset_inbox"
            source = write_batch(inbox)
            service, repository = build_service(root)
            manager = OfflineBatchManager(inbox, repository, service)
            batch_id = manager.discover_batches()["items"][0]["batch_id"]
            plan = manager._preflight(source)
            repository.create_offline_batch(
                batch_id,
                source,
                plan["items"],
                gas_row_count=plan["gas_row_count"],
                thermal_frame_count=plan["thermal_frame_count"],
                diagnostics=plan["diagnostics"],
            )
            repository.confirm_offline_batch_detection(batch_id)
            repository.claim_next_offline_batch()
            item = repository.pending_offline_items(batch_id)[0]
            repository.update_offline_item(
                batch_id, item["relative_path"], status="running"
            )
            try:
                manager.start()
                finished = wait_for_batch(repository, batch_id)
                self.assertEqual(finished["status"], "completed")
                self.assertEqual(finished["capture_succeeded"], 1)
            finally:
                manager.stop()

    def test_rejects_missing_structure_duplicate_ids_and_unsafe_batch_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "dataset_inbox"
            (inbox / "visible").mkdir(parents=True)
            (inbox / "visible" / "color_20260804_153005_123456.jpg").write_bytes(
                MINIMAL_PNG
            )
            service, repository = build_service(root)
            manager = OfflineBatchManager(inbox, repository, service)
            incomplete_id = manager.discover_batches()["items"][0]["batch_id"]
            with self.assertRaises(ValidationError):
                manager.queue_import(incomplete_id)
            with self.assertRaises(ValidationError):
                manager.queue_import("../outside")

            shutil.rmtree(inbox)
            source = write_batch(inbox)
            first_package = next((source / "visible").rglob("metadata.json")).parent
            duplicate = source / "visible" / "2026-08-04" / "duplicate"
            shutil.copytree(first_package, duplicate)
            duplicate_id = manager.discover_batches()["items"][0]["batch_id"]
            with self.assertRaises(ValidationError):
                manager.queue_import(duplicate_id)

            shutil.rmtree(inbox)
            mixed = write_batch(inbox, capture_id="color_20260804_153005_123456")
            (mixed / "visible" / "color_20260804_153005_123456.jpg").write_bytes(
                MINIMAL_PNG
            )
            mixed_id = manager.discover_batches()["items"][0]["batch_id"]
            with self.assertRaises(ValidationError):
                manager.queue_import(mixed_id)

            legacy = inbox / "inspection-export-old"
            (legacy / "gas").mkdir(parents=True)
            self.assertEqual(
                manager.discover_batches()["items"][0]["batch_id"], mixed_id
            )
            with self.assertRaises(ValidationError):
                manager.queue_import(legacy.name)

    def test_offline_batch_api_discovers_imports_and_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "dataset_inbox"
            write_batch(inbox)
            config_paths = {
                name: root / f"{name}.json"
                for name in ("classes", "stations", "modules", "analog")
            }
            config_paths["classes"].write_text("{}", encoding="utf-8")
            config_paths["stations"].write_text(
                json.dumps({"08": {"location_name": "08号区段"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            config_paths["modules"].write_text(
                json.dumps(
                    {
                        name: {"enabled": False}
                        for name in (
                            "tool_and_safety_sign",
                            "coal_presence",
                            "station_number",
                            "digital_meter",
                            "analog_meter",
                        )
                    }
                ),
                encoding="utf-8",
            )
            config_paths["analog"].write_text("{}", encoding="utf-8")
            settings_path = root / "app.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "dataset_inbox_path": str(inbox),
                        "storage_root": str(root / "runtime"),
                        "database_path": str(root / "runtime" / "inspection.db"),
                        "classes_path": str(config_paths["classes"]),
                        "stations_path": str(config_paths["stations"]),
                        "modules_path": str(config_paths["modules"]),
                        "analog_references_path": str(config_paths["analog"]),
                        "detector": {"backend": "noop", "weights": None},
                    }
                ),
                encoding="utf-8",
            )
            previous = os.environ.get("GO2_INSPECTION_SETTINGS")
            os.environ["GO2_INSPECTION_SETTINGS"] = str(settings_path)
            try:
                app = create_app()
            finally:
                if previous is None:
                    os.environ.pop("GO2_INSPECTION_SETTINGS", None)
                else:
                    os.environ["GO2_INSPECTION_SETTINGS"] = previous

            with TestClient(app) as client:
                discovered = client.get("/api/v1/offline-batches")
                self.assertEqual(discovered.status_code, 200)
                self.assertEqual(discovered.json()["items"][0]["status"], "discovered")
                batch_id = discovered.json()["items"][0]["batch_id"]
                queued = client.post(
                    f"/api/v1/offline-batches/{batch_id}/import"
                )
                self.assertEqual(queued.status_code, 200, queued.text)
                self.assertEqual(
                    queued.json()["status"], "awaiting_detection_confirmation"
                )
                before_confirmation = client.get(
                    f"/api/v1/offline-batches/{batch_id}/report.docx"
                )
                self.assertEqual(before_confirmation.status_code, 409)
                confirmed = client.post(
                    f"/api/v1/offline-batches/{batch_id}/confirm-detection"
                )
                self.assertEqual(confirmed.status_code, 200, confirmed.text)
                self.assertTrue(confirmed.json()["detection_confirmed_at"])
                repeated_confirmation = client.post(
                    f"/api/v1/offline-batches/{batch_id}/confirm-detection"
                )
                self.assertEqual(repeated_confirmation.status_code, 200)
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    detail = client.get(
                        f"/api/v1/offline-batches/{batch_id}"
                    )
                    if detail.json()["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.02)
                self.assertEqual(detail.json()["status"], "completed")
                filtered = client.get(
                    "/api/v1/captures", params={"batch_id": batch_id}
                )
                self.assertEqual(filtered.status_code, 200)
                self.assertEqual(filtered.json()["total"], 1)
                self.assertEqual(
                    filtered.json()["items"][0]["source_batch_id"], batch_id
                )
                exported = client.get(
                    "/api/v1/export",
                    params={"format": "json", "batch_id": batch_id},
                )
                self.assertEqual(exported.status_code, 200)
                self.assertEqual(exported.json()[0]["source_batch_id"], batch_id)
                report = client.get(
                    f"/api/v1/offline-batches/{batch_id}/report.docx"
                )
                self.assertEqual(report.status_code, 409)
                report_confirmation = client.post(
                    f"/api/v1/offline-batches/{batch_id}/confirm-report"
                )
                self.assertEqual(report_confirmation.status_code, 200)
                self.assertTrue(report_confirmation.json()["report_available"])
                confirmed_at = report_confirmation.json()["report_confirmed_at"]
                repeated_report_confirmation = client.post(
                    f"/api/v1/offline-batches/{batch_id}/confirm-report"
                )
                self.assertEqual(repeated_report_confirmation.status_code, 200)
                self.assertEqual(
                    repeated_report_confirmation.json()["report_confirmed_at"],
                    confirmed_at,
                )
                report = client.get(
                    f"/api/v1/offline-batches/{batch_id}/report.docx"
                )
                self.assertEqual(report.status_code, 200, report.text)
                self.assertTrue(report.content.startswith(b"PK"))
                self.assertEqual(
                    report.headers["content-type"],
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
                self.assertIn("filename*=UTF-8", report.headers["content-disposition"])

                capture_id = "rpi_20260804_153005_123456"
                capture = client.get(f"/api/v1/results/{capture_id}").json()
                corrected = client.patch(
                    f"/api/v1/results/{capture_id}/correction",
                    json={
                        "operator": "test-reviewer",
                        "reason": "确认流程回归",
                        "objects": capture["result"]["objects"],
                    },
                )
                self.assertEqual(corrected.status_code, 200, corrected.text)
                self.assertFalse(
                    client.get(f"/api/v1/offline-batches/{batch_id}").json()[
                        "report_available"
                    ]
                )
                self.assertEqual(
                    client.get(
                        f"/api/v1/offline-batches/{batch_id}/report.docx"
                    ).status_code,
                    409,
                )

                client.post(f"/api/v1/offline-batches/{batch_id}/confirm-report")
                reprocessed = client.post(
                    f"/api/v1/results/{capture_id}/reprocess"
                )
                self.assertEqual(reprocessed.status_code, 200, reprocessed.text)
                self.assertFalse(
                    client.get(f"/api/v1/offline-batches/{batch_id}").json()[
                        "report_available"
                    ]
                )

                client.post(f"/api/v1/offline-batches/{batch_id}/confirm-report")
                deleted = client.delete(f"/api/v1/captures/{capture_id}")
                self.assertEqual(deleted.status_code, 200, deleted.text)
                after_delete = client.get(
                    f"/api/v1/offline-batches/{batch_id}"
                ).json()
                self.assertFalse(after_delete["report_available"])
                self.assertEqual(after_delete["capture_failed"], 1)
                health = client.get("/health").json()
                self.assertEqual(health["offline_batches_queued"], 0)
                self.assertEqual(health["offline_batches_running"], 0)


if __name__ == "__main__":
    unittest.main()
