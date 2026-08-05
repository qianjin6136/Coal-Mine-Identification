"""U 盘离线数据的安全扫描、传感器归档与后台视觉处理。"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
from threading import Event, RLock, Thread
from typing import Any, Mapping, TYPE_CHECKING

from PIL import Image

from .domain import CaptureMetadata, RobotPose
from .errors import CaptureNotFoundError, ValidationError
from .storage import CaptureRepository, validate_image_bytes

if TYPE_CHECKING:
    from .service import InspectionService


_BATCH_ID_RE = re.compile(r"^direct-[0-9a-f]{16}$")
_INPUT_DIRECTORIES = ("gas", "thermal", "visible")
_VISIBLE_MANIFEST_SUFFIXES = {".jpg", ".jpeg", ".png"}
_THERMAL_NAME_RE = re.compile(
    r"^thermal_(?P<date>\d{8})_(?P<time>\d{6})_(?P<sample>\d{6})\.png$",
    re.IGNORECASE,
)
_FLAT_VISIBLE_NAME_RE = re.compile(
    r"^color_(?P<date>\d{8})_(?P<time>\d{6})_(?P<microsecond>\d{6})\.(?:jpg|jpeg)$",
    re.IGNORECASE,
)
_CHINA_TIMEZONE = timezone(timedelta(hours=8))
_FLAT_CAMERA_ID = "raspberry_pi_usb"
_GAS_CHANNELS = ("ch4", "o2", "co", "h2s")
_GAS_FIELDS = ["timestamp", "sample_id"] + [
    f"{channel}_{suffix}"
    for channel in _GAS_CHANNELS
    for suffix in ("value", "unit", "status")
] + ["error"]
_CHINESE_GAS_FIELDS = [
    "时间",
    "编号",
    "CH4(%LEL)",
    "O2(%VOL)",
    "CO(ppm)",
    "H2S(ppm)",
    "状态",
]
_CHINESE_GAS_VALUES = {
    "ch4": ("CH4(%LEL)", "%LEL"),
    "o2": ("O2(%VOL)", "%VOL"),
    "co": ("CO(ppm)", "ppm"),
    "h2s": ("H2S(ppm)", "ppm"),
}


@dataclass(frozen=True)
class PackageData:
    relative_path: str
    metadata: CaptureMetadata
    image_paths: tuple[Path, ...]


class OfflineBatchManager:
    """把配置收件箱中的 ``gas/thermal/visible`` 作为一批离线数据。"""

    def __init__(
        self,
        inbox_root: Path,
        repository: CaptureRepository,
        inspection_service: "InspectionService",
    ) -> None:
        self.inbox_root = Path(inbox_root).resolve()
        self.inbox_root.mkdir(parents=True, exist_ok=True)
        self.repository = repository
        self.inspection_service = inspection_service
        self._stop_event = Event()
        self._wake_event = Event()
        self._thread: Thread | None = None
        self._thread_lock = RLock()

    def start(self) -> None:
        """启动唯一后台工作线程，并恢复上次意外中断的任务。"""

        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self.repository.reset_interrupted_offline_batches()
            self._stop_event.clear()
            self._thread = Thread(
                target=self._worker_loop,
                name="offline-batch-worker",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._thread_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
            self._wake_event.set()
        thread.join(timeout=5.0)
        with self._thread_lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None

    def discover_batches(self) -> dict[str, Any]:
        """合并收件箱中的轻量发现结果和数据库中的持久化处理状态。"""

        registered = {
            item["batch_id"]: item for item in self.repository.list_offline_batches()
        }
        result: list[dict[str, Any]] = []
        current_batch_id = self._current_batch_id()
        if current_batch_id is not None:
            if current_batch_id in registered:
                item = dict(registered[current_batch_id])
                item["source_available"] = True
                result.append(item)
            else:
                visible_root = self.inbox_root / "visible"
                gas_root = self.inbox_root / "gas"
                thermal_root = self.inbox_root / "thermal"
                result.append(
                    {
                        "batch_id": current_batch_id,
                        "source_path": str(self.inbox_root),
                        "source_available": True,
                        "status": "discovered",
                        "sensor_status": "not_imported",
                        "capture_total": len(self._visible_items(visible_root)),
                        "capture_succeeded": 0,
                        "capture_failed": 0,
                        "capture_pending": 0,
                        "gas_row_count": self._count_gas_rows(gas_root),
                        "thermal_frame_count": (
                            sum(1 for _ in thermal_root.rglob("*.png"))
                            if thermal_root.is_dir() else 0
                        ),
                        "warning_count": 0,
                        "diagnostics": [],
                        "progress_percent": 0.0,
                    }
                )
        for batch_id, item in registered.items():
            if batch_id == current_batch_id:
                continue
            missing = dict(item)
            missing["source_available"] = False
            result.append(missing)
        result.sort(key=lambda item: str(item["batch_id"]), reverse=True)
        return {"inbox_path": str(self.inbox_root), "items": result}

    def queue_import(self, batch_id: str) -> dict[str, Any]:
        source = self._resolve_batch_source(batch_id)
        if self.repository.offline_batch_exists(batch_id):
            return self.repository.get_offline_batch(batch_id)
        plan = self._preflight(source)
        try:
            batch = self.repository.create_offline_batch(
                batch_id,
                source,
                plan["items"],
                gas_row_count=plan["gas_row_count"],
                thermal_frame_count=plan["thermal_frame_count"],
                diagnostics=plan["diagnostics"],
            )
        except ValidationError:
            if not self.repository.offline_batch_exists(batch_id):
                raise
            batch = self.repository.get_offline_batch(batch_id)
        self.start()
        self._wake_event.set()
        return batch

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        return self.repository.get_offline_batch(batch_id)

    def retry_batch(self, batch_id: str) -> dict[str, Any]:
        self._resolve_batch_source(batch_id)
        batch = self.repository.retry_offline_batch(batch_id)
        self.start()
        self._wake_event.set()
        return batch

    def run_pending_once(self) -> bool:
        """领取并完整处理一个批次；测试也可直接调用此确定性入口。"""

        batch = self.repository.claim_next_offline_batch()
        if batch is None:
            return False
        batch_id = str(batch["batch_id"])
        try:
            source = self._resolve_batch_source(batch_id)
            if batch["sensor_status"] != "completed":
                self.repository.mark_sensor_import_running(batch_id)
                try:
                    self._archive_sensors(batch_id, source)
                except Exception as exc:
                    self.repository.mark_sensor_import_failed(batch_id, str(exc))

            for item in self.repository.pending_offline_items(batch_id):
                if self._stop_event.is_set():
                    self.repository.requeue_running_offline_batch(batch_id)
                    return True
                relative_path = str(item["relative_path"])
                self.repository.update_offline_item(
                    batch_id, relative_path, status="running"
                )
                try:
                    package = self._load_package(source, relative_path)
                    planned_id = item.get("capture_id")
                    if planned_id and planned_id != package.metadata.capture_id:
                        raise ValidationError(
                            "capture_id changed after batch preflight"
                        )
                    payloads = [
                        (name, path.read_bytes())
                        for name, path in zip(
                            package.metadata.image_names, package.image_paths
                        )
                    ]
                    if self.repository.capture_exists(package.metadata.capture_id):
                        self.repository.assert_replay_matches(
                            package.metadata, payloads
                        )
                        existing = self.repository.get_capture(
                            package.metadata.capture_id
                        )
                        if existing["status"] != "processed":
                            self.inspection_service.reprocess_capture(
                                package.metadata.capture_id
                            )
                    else:
                        self.inspection_service.ingest_capture(
                            package.metadata, payloads
                        )
                    self.repository.update_offline_item(
                        batch_id,
                        relative_path,
                        status="succeeded",
                        capture_id=package.metadata.capture_id,
                    )
                except Exception as exc:
                    self.repository.update_offline_item(
                        batch_id,
                        relative_path,
                        status="failed",
                        error=str(exc)[:2000],
                    )
            self.repository.finish_offline_batch(batch_id)
        except Exception as exc:
            self.repository.finish_offline_batch(batch_id, error=str(exc))
        return True

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            if self.run_pending_once():
                continue
            self._wake_event.wait(timeout=1.0)
            self._wake_event.clear()

    def _preflight(self, source: Path) -> dict[str, Any]:
        missing = [
            name for name in ("gas", "thermal", "visible")
            if not (source / name).is_dir()
        ]
        if missing:
            raise ValidationError(
                f"offline batch is missing required directories: {', '.join(missing)}"
            )
        visible_root = source / "visible"
        visible_items = self._visible_items(visible_root)
        if not visible_items:
            raise ValidationError("offline batch contains no visible captures")

        items: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        capture_paths: dict[str, str] = {}
        fatal_errors: list[str] = []
        for visible_item in visible_items:
            relative_path = visible_item.relative_to(source).as_posix()
            capture_id: str | None = None
            error: str | None = None
            try:
                package = self._load_package(source, relative_path)
                capture_id = package.metadata.capture_id
            except Exception as exc:
                error = str(exc)
                if "escapes" in error or "relative" in error:
                    fatal_errors.append(f"{relative_path}: {error}")
                if visible_item.is_dir():
                    try:
                        raw = json.loads(
                            (visible_item / "metadata.json").read_text(encoding="utf-8")
                        )
                        value = raw.get("capture_id") if isinstance(raw, Mapping) else None
                        capture_id = str(value).strip() or None
                    except Exception:
                        capture_id = None
                else:
                    capture_id = visible_item.stem
            if capture_id:
                previous = capture_paths.get(capture_id)
                if previous is not None:
                    fatal_errors.append(
                        f"duplicate capture_id {capture_id}: {previous}, {relative_path}"
                    )
                else:
                    capture_paths[capture_id] = relative_path
            items.append(
                {
                    "relative_path": relative_path,
                    "capture_id": capture_id,
                    "error": error,
                }
            )
        if fatal_errors:
            raise ValidationError("; ".join(fatal_errors))
        return {
            "items": items,
            "gas_row_count": self._count_gas_rows(source / "gas"),
            "thermal_frame_count": sum(
                1 for _ in (source / "thermal").rglob("*.png")
            ),
            "diagnostics": diagnostics,
        }

    def _load_package(self, source: Path, relative_path: str) -> PackageData:
        normalized = str(relative_path).replace("\\", "/")
        if normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
            raise ValidationError("capture package path must be relative")
        package_root = (source / normalized).resolve()
        self._assert_within(package_root, source, "capture path escapes batch")
        self._assert_within(
            package_root,
            (source / "visible").resolve(),
            "capture path escapes visible directory",
        )
        if package_root.is_file():
            return self._load_flat_visible(source, relative_path, package_root)
        metadata_path = package_root / "metadata.json"
        if not metadata_path.is_file():
            raise ValidationError("metadata.json is missing")
        metadata = CaptureMetadata.from_json(metadata_path.read_bytes())
        if len(metadata.image_names) != 3:
            raise ValidationError("offline visible package must declare exactly 3 images")
        image_paths: list[Path] = []
        for name in metadata.image_names:
            portable = str(name).replace("\\", "/")
            if portable.startswith("/") or ":" in portable.split("/", 1)[0]:
                raise ValidationError(f"image path must be relative: {name}")
            path = (package_root / portable).resolve()
            self._assert_within(path, package_root, f"image path escapes package: {name}")
            if not path.is_file():
                raise ValidationError(f"queued image not found: {name}")
            validate_image_bytes(path.read_bytes(), self.inspection_service.max_image_bytes)
            image_paths.append(path)
        return PackageData(relative_path, metadata, tuple(image_paths))

    def _load_flat_visible(
        self, source: Path, relative_path: str, image_path: Path
    ) -> PackageData:
        match = _FLAT_VISIBLE_NAME_RE.fullmatch(image_path.name)
        if match is None:
            raise ValidationError("invalid flat visible image name")
        payload = image_path.read_bytes()
        validate_image_bytes(payload, self.inspection_service.max_image_bytes)
        captured_at = datetime.strptime(
            match.group("date") + match.group("time") + match.group("microsecond"),
            "%Y%m%d%H%M%S%f",
        ).replace(tzinfo=_CHINA_TIMEZONE)
        metadata = CaptureMetadata(
            capture_id=image_path.stem,
            capture_time=captured_at.isoformat(),
            station_id="",
            robot_pose=RobotPose.from_mapping({}),
            camera_id=_FLAT_CAMERA_ID,
            image_names=(image_path.name,),
        )
        return PackageData(relative_path, metadata, (image_path,))

    def _archive_sensors(self, batch_id: str, source: Path) -> None:
        archive_root = self.repository.imported_batches_root / batch_id
        gas_samples, gas_diagnostics = self._load_gas_samples(
            source / "gas", archive_root / "gas"
        )
        thermal_samples, thermal_diagnostics = self._load_thermal_samples(
            source / "thermal", archive_root / "thermal"
        )
        diagnostics = [*gas_diagnostics, *thermal_diagnostics]
        samples: list[dict[str, Any]] = []
        for sample_key in sorted(set(gas_samples) | set(thermal_samples)):
            gas = gas_samples.get(sample_key)
            thermal = thermal_samples.get(sample_key)
            warnings: list[str] = []
            if gas is None:
                warnings.append("missing_gas_row")
            if thermal is None:
                warnings.append("missing_thermal_frame")
            if warnings:
                diagnostics.append(
                    {
                        "scope": "sensors",
                        "level": "warning",
                        "sample_key": sample_key,
                        "message": ", ".join(warnings),
                    }
                )
            merged = dict(gas or {})
            merged.update(
                {
                    "sample_key": sample_key,
                    "sample_id": (
                        (gas or thermal or {}).get("sample_id") or ""
                    ),
                    "captured_at": (
                        (gas or {}).get("captured_at")
                        or (thermal or {}).get("captured_at")
                        or ""
                    ),
                    "thermal_stored_path": (
                        (thermal or {}).get("thermal_stored_path")
                    ),
                    "thermal_sha256": (thermal or {}).get("thermal_sha256"),
                    "warning": "; ".join(warnings) or None,
                }
            )
            samples.append(merged)
        self.repository.replace_sensor_samples(
            batch_id,
            samples,
            gas_row_count=len(gas_samples),
            thermal_frame_count=len(thermal_samples),
            diagnostics=diagnostics,
        )

    def _load_gas_samples(
        self, source_root: Path, archive_root: Path
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        samples: dict[str, dict[str, Any]] = {}
        diagnostics: list[dict[str, Any]] = []
        csv_paths = sorted(source_root.rglob("*.csv"))
        if not csv_paths:
            diagnostics.append(
                {"scope": "gas", "level": "warning", "message": "no gas CSV files"}
            )
        for source_path in csv_paths:
            relative = source_path.relative_to(source_root)
            self._assert_within(
                source_path.resolve(), source_root, "gas file escapes batch"
            )
            destination = archive_root / relative
            self._copy_atomic(source_path, destination)
            try:
                with source_path.open("r", encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    if reader.fieldnames not in (_GAS_FIELDS, _CHINESE_GAS_FIELDS):
                        raise ValidationError(
                            f"invalid gas CSV header: {source_path.name}"
                        )
                    for row_number, row in enumerate(reader, start=2):
                        try:
                            sample = self._normalize_gas_row(row)
                            key = str(sample["sample_key"])
                            if key in samples:
                                raise ValidationError(f"duplicate sensor sample: {key}")
                            samples[key] = sample
                        except Exception as exc:
                            diagnostics.append(
                                {
                                    "scope": "gas",
                                    "level": "error",
                                    "path": relative.as_posix(),
                                    "row": row_number,
                                    "message": str(exc),
                                }
                            )
            except Exception as exc:
                diagnostics.append(
                    {
                        "scope": "gas",
                        "level": "error",
                        "path": relative.as_posix(),
                        "message": str(exc),
                    }
                )
        return samples, diagnostics

    @staticmethod
    def _normalize_gas_row(row: Mapping[str, Any]) -> dict[str, Any]:
        original_row = dict(row)
        if "时间" in row:
            status = str(row.get("状态", "")).strip()
            translated: dict[str, Any] = {
                "timestamp": row.get("时间", ""),
                "sample_id": row.get("编号", ""),
                "error": "" if status in ("", "正常") else status,
            }
            for channel, (value_field, unit) in _CHINESE_GAS_VALUES.items():
                translated[f"{channel}_value"] = row.get(value_field, "")
                translated[f"{channel}_unit"] = unit
                translated[f"{channel}_status"] = None
            row = translated
        timestamp = str(row.get("timestamp", "")).strip()
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError("gas timestamp must be ISO-8601") from exc
        sample_id = str(row.get("sample_id", "")).strip()
        if not sample_id.isdigit() or len(sample_id) > 12:
            raise ValidationError("gas sample_id must be numeric")
        normalized_id = sample_id.zfill(6)
        result: dict[str, Any] = {
            "sample_key": f"{parsed:%Y%m%d_%H%M%S}_{normalized_id}",
            "captured_at": timestamp,
            "sample_id": normalized_id,
            "gas_error": str(row.get("error", "")).strip() or None,
            "raw_row_json": json.dumps(original_row, ensure_ascii=False),
        }
        for channel in _GAS_CHANNELS:
            raw_value = str(row.get(f"{channel}_value", "")).strip()
            value: float | None = None
            if raw_value:
                try:
                    value = float(Decimal(raw_value))
                except (InvalidOperation, ValueError) as exc:
                    raise ValidationError(
                        f"{channel}_value must be numeric or blank"
                    ) from exc
                if not math.isfinite(value):
                    raise ValidationError(f"{channel}_value must be finite")
            result[f"{channel}_value"] = value
            result[f"{channel}_unit"] = (
                str(row.get(f"{channel}_unit", "")).strip() or None
            )
            result[f"{channel}_status"] = (
                str(row.get(f"{channel}_status", "")).strip() or None
            )
        return result

    @classmethod
    def _visible_items(cls, visible_root: Path) -> list[Path]:
        if not visible_root.is_dir():
            return []
        items: list[Path] = []
        for metadata_path in sorted(visible_root.rglob("metadata.json")):
            relative = metadata_path.relative_to(visible_root)
            if not cls._has_hidden_part(relative):
                items.append(metadata_path.parent)
        for image_path in sorted(visible_root.rglob("color_*")):
            relative = image_path.relative_to(visible_root)
            if (
                image_path.is_file()
                and not cls._has_hidden_part(relative)
                and _FLAT_VISIBLE_NAME_RE.fullmatch(image_path.name)
            ):
                items.append(image_path)
        return sorted(set(items), key=lambda path: path.as_posix())

    def _load_thermal_samples(
        self, source_root: Path, archive_root: Path
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        samples: dict[str, dict[str, Any]] = {}
        diagnostics: list[dict[str, Any]] = []
        png_paths = sorted(source_root.rglob("*.png"))
        if not png_paths:
            diagnostics.append(
                {"scope": "thermal", "level": "warning", "message": "no thermal PNG files"}
            )
        for source_path in png_paths:
            relative = source_path.relative_to(source_root)
            self._assert_within(
                source_path.resolve(), source_root, "thermal file escapes batch"
            )
            match = _THERMAL_NAME_RE.fullmatch(source_path.name)
            if match is None:
                diagnostics.append(
                    {
                        "scope": "thermal",
                        "level": "error",
                        "path": relative.as_posix(),
                        "message": "invalid thermal file name",
                    }
                )
                continue
            try:
                payload = source_path.read_bytes()
                validate_image_bytes(payload, self.inspection_service.max_image_bytes)
                with Image.open(source_path) as image:
                    if image.format != "PNG":
                        raise ValidationError("thermal file must be PNG")
                    image.verify()
                destination = archive_root / relative
                self._copy_atomic(source_path, destination)
                sample_id = match.group("sample")
                sample_key = (
                    f"{match.group('date')}_{match.group('time')}_{sample_id}"
                )
                if sample_key in samples:
                    raise ValidationError(f"duplicate thermal sample: {sample_key}")
                captured_at = datetime.strptime(
                    match.group("date") + match.group("time"), "%Y%m%d%H%M%S"
                ).isoformat()
                samples[sample_key] = {
                    "sample_id": sample_id,
                    "captured_at": captured_at,
                    "thermal_stored_path": str(destination),
                    "thermal_sha256": hashlib.sha256(payload).hexdigest(),
                }
            except Exception as exc:
                diagnostics.append(
                    {
                        "scope": "thermal",
                        "level": "error",
                        "path": relative.as_posix(),
                        "message": str(exc),
                    }
                )
        return samples, diagnostics

    def _resolve_batch_source(self, batch_id: str) -> Path:
        if not _BATCH_ID_RE.fullmatch(str(batch_id)):
            raise ValidationError("invalid offline batch id")
        if batch_id != self._current_batch_id():
            raise ValidationError(
                f"offline batch source is no longer available: {batch_id}"
            )
        if not self.inbox_root.is_dir():
            raise ValidationError("offline data directory not found")
        return self.inbox_root

    def _current_batch_id(self) -> str | None:
        """用受支持文件的相对路径生成不依赖文件内容的稳定批次标识。"""

        manifest: list[str] = []
        for directory in _INPUT_DIRECTORIES:
            root = self.inbox_root / directory
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                relative = path.relative_to(self.inbox_root)
                if self._has_hidden_part(relative):
                    continue
                suffix = path.suffix.lower()
                supported = (
                    (directory == "gas" and suffix == ".csv")
                    or (directory == "thermal" and suffix == ".png")
                    or (
                        directory == "visible"
                        and (
                            suffix in _VISIBLE_MANIFEST_SUFFIXES
                            or path.name.lower() == "metadata.json"
                        )
                    )
                )
                if supported:
                    manifest.append(relative.as_posix())
        if not manifest:
            return None
        canonical = "\n".join(sorted(manifest)).encode("utf-8")
        return f"direct-{hashlib.sha256(canonical).hexdigest()[:16]}"

    @staticmethod
    def _assert_within(path: Path, root: Path, message: str) -> None:
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise ValidationError(message) from exc

    @staticmethod
    def _has_hidden_part(path: Path) -> bool:
        return any(part.startswith(".") for part in path.parts)

    @staticmethod
    def _count_gas_rows(root: Path) -> int:
        if not root.is_dir():
            return 0
        count = 0
        for path in root.rglob("*.csv"):
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as stream:
                    reader = csv.reader(stream)
                    next(reader, None)
                    count += sum(1 for _ in reader)
            except OSError:
                continue
        return count

    @staticmethod
    def _copy_atomic(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        try:
            shutil.copy2(source, temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
