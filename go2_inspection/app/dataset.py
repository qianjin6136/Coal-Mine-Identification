"""现场图片数据集的发现、质量检查和防泄漏分组切分。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping, Sequence


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


@dataclass(frozen=True)
class DatasetRecord:
    """一张候选图片及其来源、质量指标和数据切分分组。"""

    path: str
    relative_path: str
    capture_id: str
    batch_id: str
    station_id: str | None
    camera_id: str | None
    capture_time: str | None
    width: int | None
    height: int | None
    size_bytes: int
    sha256: str
    perceptual_hash: str | None
    brightness_mean: float | None
    dark_fraction: float | None
    bright_fraction: float | None
    edge_variance: float | None
    qc_flags: tuple[str, ...]
    metadata_path: str | None
    split: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "relative_path": self.relative_path,
            "capture_id": self.capture_id,
            "batch_id": self.batch_id,
            "station_id": self.station_id,
            "camera_id": self.camera_id,
            "capture_time": self.capture_time,
            "width": self.width,
            "height": self.height,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "perceptual_hash": self.perceptual_hash,
            "brightness_mean": self.brightness_mean,
            "dark_fraction": self.dark_fraction,
            "bright_fraction": self.bright_fraction,
            "edge_variance": self.edge_variance,
            "qc_flags": list(self.qc_flags),
            "metadata_path": self.metadata_path,
            "split": self.split,
        }


def discover_images(source_root: Path) -> list[Path]:
    """递归发现支持的图片，并跳过常见的生成目录。"""

    source_root = Path(source_root).resolve()
    ignored_parts = {"labels", "processed", "evidence", "__pycache__"}
    return sorted(
        (
            path
            for path in source_root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in IMAGE_SUFFIXES
            and not ignored_parts.intersection(path.relative_to(source_root).parts)
        ),
        key=lambda path: path.as_posix().lower(),
    )


def inspect_dataset(
    source_root: Path,
    *,
    dark_mean_threshold: float = 35.0,
    bright_mean_threshold: float = 225.0,
    dark_pixel_threshold: float = 0.55,
    bright_pixel_threshold: float = 0.55,
    blur_edge_variance_threshold: float = 80.0,
) -> list[DatasetRecord]:
    """扫描图片、解析同目录元数据并计算可重复的轻量质量指标。"""

    source_root = Path(source_root).resolve()
    database_metadata, database_path = _database_metadata_index(source_root)
    records: list[DatasetRecord] = []
    digest_owner: dict[str, int] = {}
    perceptual_owner: dict[str, int] = {}
    for image_path in discover_images(source_root):
        relative_path = image_path.relative_to(source_root)
        metadata, metadata_path = _load_metadata(image_path)
        if metadata_path is None:
            database_value = database_metadata.get(image_path.parent.name)
            if database_value is not None:
                metadata = database_value
                metadata_path = database_path
        capture_id = _capture_id(metadata, image_path)
        batch_id = str(
            metadata.get("batch_id")
            or metadata.get("capture_batch")
            or image_path.parent.name
            or capture_id
        )
        size_bytes = image_path.stat().st_size
        digest = _sha256(image_path)
        flags: list[str] = []
        width: int | None = None
        height: int | None = None
        perceptual_hash: str | None = None
        brightness_mean: float | None = None
        dark_fraction: float | None = None
        bright_fraction: float | None = None
        edge_variance: float | None = None
        try:
            (
                width,
                height,
                perceptual_hash,
                brightness_mean,
                dark_fraction,
                bright_fraction,
                edge_variance,
            ) = _image_metrics(image_path)
            if width < 320 or height < 240:
                flags.append("low_resolution")
            if (
                brightness_mean < dark_mean_threshold
                or dark_fraction >= dark_pixel_threshold
            ):
                flags.append("too_dark")
            if (
                brightness_mean > bright_mean_threshold
                or bright_fraction >= bright_pixel_threshold
            ):
                flags.append("too_bright")
            if edge_variance < blur_edge_variance_threshold:
                flags.append("possibly_blurry")
        except Exception:
            flags.append("unreadable_image")

        is_exact_duplicate = digest in digest_owner
        if is_exact_duplicate:
            flags.append("exact_duplicate")
        else:
            digest_owner[digest] = len(records)
        if perceptual_hash:
            if perceptual_hash in perceptual_owner and not is_exact_duplicate:
                flags.append("near_duplicate")
            else:
                perceptual_owner.setdefault(perceptual_hash, len(records))
        if metadata_path is None:
            flags.append("metadata_missing")
        pose = metadata.get("robot_pose")
        if not isinstance(pose, Mapping) or all(
            pose.get(name) is None for name in ("x_m", "y_m", "yaw_deg")
        ):
            flags.append("pose_missing")

        records.append(
            DatasetRecord(
                path=str(image_path),
                relative_path=relative_path.as_posix(),
                capture_id=capture_id,
                batch_id=batch_id,
                station_id=_optional_text(metadata.get("station_id")),
                camera_id=_optional_text(metadata.get("camera_id")),
                capture_time=_optional_text(metadata.get("capture_time")),
                width=width,
                height=height,
                size_bytes=size_bytes,
                sha256=digest,
                perceptual_hash=perceptual_hash,
                brightness_mean=brightness_mean,
                dark_fraction=dark_fraction,
                bright_fraction=bright_fraction,
                edge_variance=edge_variance,
                qc_flags=tuple(flags),
                metadata_path=str(metadata_path) if metadata_path else None,
            )
        )
    return records


def assign_grouped_splits(
    records: Sequence[DatasetRecord],
    ratios: Mapping[str, float] | None = None,
    *,
    seed: str = "go2-inspection-v1",
) -> list[DatasetRecord]:
    """按批次和 capture ID 分组切分，保证三连拍不会跨集合。"""

    ratios = ratios or {"train": 0.7, "val": 0.15, "test": 0.15}
    if set(ratios) != {"train", "val", "test"}:
        raise ValueError("ratios must contain train, val and test")
    if any(float(value) < 0 for value in ratios.values()):
        raise ValueError("split ratios cannot be negative")
    total = sum(float(value) for value in ratios.values())
    if total <= 0:
        raise ValueError("split ratios must have a positive sum")
    normalized = {name: float(value) / total for name, value in ratios.items()}
    group_keys = sorted(
        {_split_group_key(record) for record in records},
        key=lambda key: hashlib.sha256(f"{seed}|{key}".encode("utf-8")).digest(),
    )
    group_counts = _allocate_group_counts(len(group_keys), normalized)
    group_splits: dict[str, str] = {}
    position = 0
    for split_name in ("train", "val", "test"):
        for key in group_keys[position : position + group_counts[split_name]]:
            group_splits[key] = split_name
        position += group_counts[split_name]

    assigned: list[DatasetRecord] = []
    for record in records:
        split = group_splits[_split_group_key(record)]
        values = record.__dict__ | {"split": split}
        assigned.append(DatasetRecord(**values))
    return assigned


def _split_group_key(record: DatasetRecord) -> str:
    # 显式 batch_id 表示同一次摆放/采集批次，整批不能跨集合；没有批次时
    # 至少按 capture_id 保证一次三连拍不拆分。
    return (
        f"batch:{record.batch_id}"
        if record.batch_id and record.batch_id != record.capture_id
        else f"capture:{record.capture_id}"
    )


def _allocate_group_counts(
    total_groups: int,
    ratios: Mapping[str, float],
) -> dict[str, int]:
    """按最大余数法分配分组，并在组数允许时保证非零集合都有样本。"""

    names = ("train", "val", "test")
    raw = {name: ratios[name] * total_groups for name in names}
    counts = {name: int(raw[name]) for name in names}
    remaining = total_groups - sum(counts.values())
    for name in sorted(
        names,
        key=lambda item: (raw[item] - counts[item], ratios[item]),
        reverse=True,
    )[:remaining]:
        counts[name] += 1
    positive_names = [name for name in names if ratios[name] > 0]
    if total_groups >= len(positive_names):
        for missing in (name for name in positive_names if counts[name] == 0):
            donor = max(
                (name for name in positive_names if counts[name] > 1),
                key=lambda name: counts[name],
            )
            counts[donor] -= 1
            counts[missing] += 1
    return counts


def write_dataset_reports(records: Sequence[DatasetRecord], output_dir: Path) -> dict[str, Path]:
    """写出 JSONL 清单、汇总报告和待复核问题 CSV。"""

    import csv

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.jsonl"
    summary_path = output_dir / "quality_summary.json"
    issues_path = output_dir / "quality_issues.csv"

    with manifest_path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    split_counts: dict[str, int] = {}
    flag_counts: dict[str, int] = {}
    capture_frames: dict[str, int] = {}
    for record in records:
        split_counts[record.split or "unassigned"] = (
            split_counts.get(record.split or "unassigned", 0) + 1
        )
        capture_frames[record.capture_id] = capture_frames.get(record.capture_id, 0) + 1
        for flag in record.qc_flags:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
    incomplete = sorted(
        capture_id for capture_id, count in capture_frames.items() if count != 3
    )
    summary = {
        "images_total": len(records),
        "captures_total": len(capture_frames),
        "split_counts": split_counts,
        "empty_splits": [
            name for name in ("train", "val", "test") if split_counts.get(name, 0) == 0
        ],
        "qc_flag_counts": flag_counts,
        "captures_not_three_frames": incomplete,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with issues_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                "relative_path",
                "capture_id",
                "batch_id",
                "split",
                "qc_flags",
            ),
        )
        writer.writeheader()
        for record in records:
            if record.qc_flags:
                writer.writerow(
                    {
                        "relative_path": record.relative_path,
                        "capture_id": record.capture_id,
                        "batch_id": record.batch_id,
                        "split": record.split,
                        "qc_flags": "|".join(record.qc_flags),
                    }
                )
    return {
        "manifest": manifest_path,
        "summary": summary_path,
        "issues": issues_path,
    }


def _load_metadata(image_path: Path) -> tuple[dict[str, Any], Path | None]:
    candidates = (
        image_path.with_name(image_path.name + ".metadata.json"),
        image_path.with_suffix(".json"),
        image_path.parent / "metadata.json",
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(value, Mapping):
            return dict(value), candidate
    return {}, None


def _database_metadata_index(
    source_root: Path,
) -> tuple[dict[str, dict[str, Any]], Path | None]:
    """在扫描 runtime_data/incoming 时兼容读取早期 SQLite 元数据。"""

    candidates = (
        source_root.parent / "database" / "inspection.db",
        source_root / "database" / "inspection.db",
    )
    database_path = next((path for path in candidates if path.is_file()), None)
    if database_path is None:
        return {}, None
    try:
        connection = sqlite3.connect(database_path)
        rows = connection.execute(
            "SELECT capture_id, metadata_json FROM captures"
        ).fetchall()
    except sqlite3.Error:
        return {}, None
    finally:
        if "connection" in locals():
            connection.close()
    result: dict[str, dict[str, Any]] = {}
    for capture_id, payload in rows:
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            result[str(capture_id)] = dict(value)
    return result, database_path


def _capture_id(metadata: Mapping[str, Any], image_path: Path) -> str:
    value = _optional_text(metadata.get("capture_id"))
    if value:
        return value
    parent = image_path.parent.name.strip()
    return parent or image_path.stem


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_metrics(
    path: Path,
) -> tuple[int, int, str, float, float, float, float]:
    from PIL import Image, ImageFilter, ImageStat

    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        grayscale = image.convert("L")
        width, height = grayscale.size
        analysis = grayscale.copy()
        analysis.thumbnail((640, 640))
        histogram = analysis.histogram()
        pixels = max(1, analysis.width * analysis.height)
        brightness_mean = float(ImageStat.Stat(analysis).mean[0])
        dark_fraction = sum(histogram[:32]) / pixels
        bright_fraction = sum(histogram[224:]) / pixels
        edges = analysis.filter(ImageFilter.FIND_EDGES)
        edge_variance = float(ImageStat.Stat(edges).var[0])
        perceptual_hash = _difference_hash(analysis)
    return (
        width,
        height,
        perceptual_hash,
        round(brightness_mean, 4),
        round(dark_fraction, 6),
        round(bright_fraction, 6),
        round(edge_variance, 4),
    )


def _difference_hash(grayscale: Any) -> str:
    from PIL import Image

    resized = grayscale.resize((9, 8), Image.Resampling.LANCZOS)
    get_flattened = getattr(resized, "get_flattened_data", None)
    pixels = list(get_flattened() if get_flattened else resized.getdata())
    bits = 0
    for row in range(8):
        for column in range(8):
            left = pixels[row * 9 + column]
            right = pixels[row * 9 + column + 1]
            bits = (bits << 1) | int(left > right)
    return f"{bits:016x}"
