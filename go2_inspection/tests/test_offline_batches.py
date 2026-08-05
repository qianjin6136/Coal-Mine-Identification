import csv
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from app.api import create_app
from app.detectors.noop import NoopDetector
from app.errors import ValidationError
from app.offline_import import OfflineBatchManager
from app.pipeline import InspectionPipeline
from app.service import InspectionService
from app.storage import CaptureRepository


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
    batch_id: str = "inspection-export-20260804-153500",
    *,
    capture_id: str = "rpi_20260804_153005_123456",
    bad_visible: bool = False,
    thermal_sample_id: str = "000001",
) -> Path:
    batch = inbox / batch_id
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
        MINIMAL_PNG
    )
    return batch


def write_flat_batch(
    inbox: Path,
    batch_id: str = "inspection-export-20260211-090000",
    *,
    bad_visible: bool = False,
) -> Path:
    batch = inbox / batch_id
    visible_root = batch / "visible"
    visible_root.mkdir(parents=True)
    (visible_root / "color_20260211_073025_706675.jpg").write_bytes(
        b"not-an-image" if bad_visible else MINIMAL_PNG
    )

    gas_root = batch / "gas"
    gas_root.mkdir(parents=True)
    with (gas_root / "gas_2026-02-11.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=CHINESE_GAS_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "时间": "2026-02-11T07:30:25+08:00",
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
    (thermal_root / "thermal_20260211_073025_000001.png").write_bytes(MINIMAL_PNG)
    return batch


def wait_for_batch(repository: CaptureRepository, batch_id: str) -> dict:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        batch = repository.get_offline_batch(batch_id)
        if batch["status"] not in {"queued", "running"}:
            return batch
        time.sleep(0.02)
    raise AssertionError("offline batch did not finish")


class OfflineBatchTests(unittest.TestCase):
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

                queued = manager.queue_import(source.name)
                self.assertEqual(queued["batch_id"], source.name)
                finished = wait_for_batch(repository, source.name)

                self.assertEqual(finished["status"], "completed")
                self.assertEqual(finished["capture_succeeded"], 1)
                self.assertEqual(finished["gas_row_count"], 1)
                self.assertEqual(finished["thermal_frame_count"], 1)
                listed = service.list_captures(source_batch_id=source.name)
                self.assertEqual(listed["total"], 1)
                self.assertEqual(
                    listed["items"][0]["source_batch_id"], source.name
                )
                capture = service.get_capture("rpi_20260804_153005_123456")
                self.assertEqual(capture["source_batch_id"], source.name)
                archive = root / "runtime" / "imported_batches" / source.name
                self.assertTrue((archive / "gas" / "gas_2026-08-04.csv").is_file())
                self.assertTrue(
                    (archive / "thermal" / "thermal_20260804_153005_000001.png").is_file()
                )
                sensor_samples = repository.sensor_samples_for_batch(source.name)
                self.assertEqual(len(sensor_samples), 1)
                self.assertEqual(sensor_samples[0]["ch4_value"], 1.25)
                self.assertIsNotNone(sensor_samples[0]["thermal_stored_path"])
                self.assertEqual(
                    next(source.rglob("metadata.json")).read_bytes(), original_metadata
                )
                self.assertFalse(list(source.rglob("upload_receipt.json")))
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

                manager.queue_import(source.name)
                finished = wait_for_batch(repository, source.name)

                self.assertEqual(finished["status"], "completed")
                self.assertEqual(finished["capture_succeeded"], 1)
                capture = service.get_capture("color_20260211_073025_706675")
                self.assertEqual(capture["capture_time"], "2026-02-11T07:30:25.706675+08:00")
                self.assertEqual(capture["station_id"], "")
                self.assertEqual(capture["camera_id"], "raspberry_pi_usb")
                self.assertEqual(len(capture["images"]), 1)
                samples = repository.sensor_samples_for_batch(source.name)
                self.assertEqual(len(samples), 1)
                self.assertEqual(samples[0]["o2_value"], 19.5)
                self.assertEqual(samples[0]["o2_unit"], "%VOL")
                self.assertEqual(samples[0]["gas_error"], "O2状态：low_alarm")
                archived_csv = (
                    root
                    / "runtime"
                    / "imported_batches"
                    / source.name
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
                manager.queue_import(source.name)
                failed = wait_for_batch(repository, source.name)
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["capture_failed"], 1)

                image.write_bytes(MINIMAL_PNG)
                manager.retry_batch(source.name)
                finished = wait_for_batch(repository, source.name)
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
                manager.queue_import(source.name)
                failed = wait_for_batch(repository, source.name)
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["capture_failed"], 1)

                for image in source.rglob("frame_*.png"):
                    image.write_bytes(MINIMAL_PNG)
                manager.retry_batch(source.name)
                finished = wait_for_batch(repository, source.name)
                self.assertEqual(finished["status"], "completed")
                self.assertEqual(finished["capture_succeeded"], 1)
            finally:
                manager.stop()

    def test_unmatched_sensor_data_is_preserved_as_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "dataset_inbox"
            source = write_batch(inbox, thermal_sample_id="000002")
            service, repository = build_service(root)
            manager = OfflineBatchManager(inbox, repository, service)
            try:
                manager.queue_import(source.name)
                finished = wait_for_batch(repository, source.name)
                self.assertEqual(finished["status"], "completed_with_errors")
                messages = [item["message"] for item in finished["diagnostics"]]
                self.assertTrue(any("missing_thermal_frame" in item for item in messages))
                self.assertTrue(any("missing_gas_row" in item for item in messages))
                self.assertEqual(finished["capture_succeeded"], 1)
            finally:
                manager.stop()

    def test_startup_resumes_interrupted_batch_from_unfinished_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "dataset_inbox"
            source = write_batch(inbox)
            service, repository = build_service(root)
            manager = OfflineBatchManager(inbox, repository, service)
            plan = manager._preflight(source)
            repository.create_offline_batch(
                source.name,
                source,
                plan["items"],
                gas_row_count=plan["gas_row_count"],
                thermal_frame_count=plan["thermal_frame_count"],
                diagnostics=plan["diagnostics"],
            )
            repository.claim_next_offline_batch()
            item = repository.pending_offline_items(source.name)[0]
            repository.update_offline_item(
                source.name, item["relative_path"], status="running"
            )
            try:
                manager.start()
                finished = wait_for_batch(repository, source.name)
                self.assertEqual(finished["status"], "completed")
                self.assertEqual(finished["capture_succeeded"], 1)
            finally:
                manager.stop()

    def test_rejects_missing_structure_duplicate_ids_and_unsafe_batch_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "dataset_inbox"
            incomplete = inbox / "inspection-export-incomplete"
            (incomplete / "visible").mkdir(parents=True)
            service, repository = build_service(root)
            manager = OfflineBatchManager(inbox, repository, service)
            with self.assertRaises(ValidationError):
                manager.queue_import(incomplete.name)
            with self.assertRaises(ValidationError):
                manager.queue_import("../outside")

            source = write_batch(inbox, "inspection-export-duplicate")
            first_package = next((source / "visible").rglob("metadata.json")).parent
            duplicate = source / "visible" / "2026-08-04" / "duplicate"
            shutil.copytree(first_package, duplicate)
            with self.assertRaises(ValidationError):
                manager.queue_import(source.name)

            mixed = write_batch(
                inbox,
                "inspection-export-mixed-duplicate",
                capture_id="color_20260804_153005_123456",
            )
            (mixed / "visible" / "color_20260804_153005_123456.jpg").write_bytes(
                MINIMAL_PNG
            )
            with self.assertRaises(ValidationError):
                manager.queue_import(mixed.name)

    def test_offline_batch_api_discovers_imports_and_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "dataset_inbox"
            source = write_batch(inbox)
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
                queued = client.post(
                    f"/api/v1/offline-batches/{source.name}/import"
                )
                self.assertEqual(queued.status_code, 200, queued.text)
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    detail = client.get(
                        f"/api/v1/offline-batches/{source.name}"
                    )
                    if detail.json()["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.02)
                self.assertEqual(detail.json()["status"], "completed")
                filtered = client.get(
                    "/api/v1/captures", params={"batch_id": source.name}
                )
                self.assertEqual(filtered.status_code, 200)
                self.assertEqual(filtered.json()["total"], 1)
                self.assertEqual(
                    filtered.json()["items"][0]["source_batch_id"], source.name
                )
                exported = client.get(
                    "/api/v1/export",
                    params={"format": "json", "batch_id": source.name},
                )
                self.assertEqual(exported.status_code, 200)
                self.assertEqual(exported.json()[0]["source_batch_id"], source.name)
                health = client.get("/health").json()
                self.assertEqual(health["offline_batches_queued"], 0)
                self.assertEqual(health["offline_batches_running"], 0)


if __name__ == "__main__":
    unittest.main()
