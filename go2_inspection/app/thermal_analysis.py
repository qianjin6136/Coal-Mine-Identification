"""热像温度元数据校验、编号关联与异常去重。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from typing import Any, Mapping, Sequence

from .errors import ValidationError


THERMAL_STATS_METADATA_KEY = "thermal_stats_v1"
THERMAL_STATS_SCHEMA_VERSION = 1
THERMAL_SENSOR_WIDTH = 32
THERMAL_SENSOR_HEIGHT = 24
THERMAL_THRESHOLD_C = 45.0
THERMAL_ASSOCIATION_WINDOW_SECONDS = 3.0
THERMAL_COOLDOWN_SECONDS = 10.0
THERMAL_ANOMALY_TYPE = "roller_stuck_overheat"
CHINA_TIMEZONE = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class ThermalStats:
    captured_at: str
    sample_id: str
    minimum_c: float
    maximum_c: float
    average_c: float


@dataclass(frozen=True)
class ThermalEvent:
    sample_key: str
    captured_at: str
    maximum_c: float
    image_path: str | None
    station_number: int | None
    station_capture_id: str | None
    association_delta_seconds: float | None
    suppressed: bool


@dataclass(frozen=True)
class ThermalAnalysis:
    frame_count: int
    readable_count: int
    unreadable_count: int
    maximum_c: float | None
    candidates: tuple[ThermalEvent, ...]
    events: tuple[ThermalEvent, ...]
    suppressed_count: int


def parse_thermal_stats(
    raw: str | None,
    *,
    expected_timestamp: datetime,
    expected_sample_id: str,
) -> ThermalStats:
    """解析并交叉校验PNG中的机器可读温度统计。"""

    if not raw:
        raise ValidationError(f"missing {THERMAL_STATS_METADATA_KEY} metadata")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"invalid {THERMAL_STATS_METADATA_KEY} JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ValidationError(f"{THERMAL_STATS_METADATA_KEY} must be an object")
    if payload.get("schema_version") != THERMAL_STATS_SCHEMA_VERSION:
        raise ValidationError("unsupported thermal metadata schema version")
    if payload.get("width") != THERMAL_SENSOR_WIDTH or payload.get(
        "height"
    ) != THERMAL_SENSOR_HEIGHT:
        raise ValidationError("thermal metadata sensor dimensions must be 32x24")

    try:
        sample_number = int(payload.get("sample_id"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("thermal metadata sample_id must be an integer") from exc
    if f"{sample_number:06d}" != expected_sample_id:
        raise ValidationError("thermal metadata sample_id does not match file name")

    captured_at = _parse_timestamp(payload.get("captured_at"), require_timezone=True)
    expected = _normalize_timestamp(expected_timestamp)
    if captured_at != expected:
        raise ValidationError("thermal metadata captured_at does not match file name")

    minimum = _finite_float(payload.get("minimum_c"), "minimum_c")
    maximum = _finite_float(payload.get("maximum_c"), "maximum_c")
    average = _finite_float(payload.get("average_c"), "average_c")
    if not minimum <= average <= maximum:
        raise ValidationError("thermal metadata must satisfy minimum <= average <= maximum")
    return ThermalStats(
        captured_at=expected.isoformat(timespec="seconds"),
        sample_id=expected_sample_id,
        minimum_c=minimum,
        maximum_c=maximum,
        average_c=average,
    )


def analyze_thermal_samples(
    samples: Sequence[Mapping[str, Any]],
    captures: Sequence[Mapping[str, Any]],
    *,
    threshold_c: float = THERMAL_THRESHOLD_C,
    association_window_seconds: float = THERMAL_ASSOCIATION_WINDOW_SECONDS,
    cooldown_seconds: float = THERMAL_COOLDOWN_SECONDS,
) -> ThermalAnalysis:
    """按最高温生成异常候选，关联最近编号并执行同编号冷却。"""

    recognized = _recognized_station_captures(captures)
    thermal_rows = [sample for sample in samples if sample.get("thermal_stored_path")]
    readable: list[tuple[Mapping[str, Any], float, datetime]] = []
    for sample in thermal_rows:
        maximum = _optional_finite_float(sample.get("thermal_maximum_c"))
        metadata_status = str(sample.get("thermal_metadata_status") or "")
        if maximum is None or metadata_status not in {"", "valid"}:
            continue
        try:
            captured_at = _parse_timestamp(sample.get("captured_at"))
        except ValidationError:
            continue
        readable.append((sample, maximum, captured_at))

    raw_candidates: list[tuple[Mapping[str, Any], float, datetime]] = [
        item for item in readable if item[1] > threshold_c
    ]
    raw_candidates.sort(key=lambda item: (item[2], str(item[0].get("sample_key") or "")))

    candidates: list[ThermalEvent] = []
    events: list[ThermalEvent] = []
    last_reported: dict[tuple[str, int], datetime] = {}
    suppressed_count = 0
    for sample, maximum, captured_at in raw_candidates:
        station = _nearest_station(
            captured_at, recognized, association_window_seconds
        )
        station_number = station[1] if station is not None else None
        key = (
            (THERMAL_ANOMALY_TYPE, station_number)
            if station_number is not None
            else None
        )
        suppressed = False
        if key is not None and key in last_reported:
            suppressed = (
                captured_at - last_reported[key]
            ).total_seconds() < cooldown_seconds
        event = ThermalEvent(
            sample_key=str(sample.get("sample_key") or ""),
            captured_at=captured_at.isoformat(timespec="seconds"),
            maximum_c=maximum,
            image_path=str(sample.get("thermal_stored_path") or "") or None,
            station_number=station_number,
            station_capture_id=station[2] if station is not None else None,
            association_delta_seconds=(station[0] if station is not None else None),
            suppressed=suppressed,
        )
        candidates.append(event)
        if suppressed:
            suppressed_count += 1
            continue
        events.append(event)
        if key is not None:
            last_reported[key] = captured_at

    maximum_values = [item[1] for item in readable]
    return ThermalAnalysis(
        frame_count=len(thermal_rows),
        readable_count=len(readable),
        unreadable_count=len(thermal_rows) - len(readable),
        maximum_c=max(maximum_values) if maximum_values else None,
        candidates=tuple(candidates),
        events=tuple(events),
        suppressed_count=suppressed_count,
    )


def _recognized_station_captures(
    captures: Sequence[Mapping[str, Any]],
) -> list[tuple[datetime, int, str, float]]:
    result: list[tuple[datetime, int, str, float]] = []
    for capture in captures:
        inspection = capture.get("result")
        modules = inspection.get("modules") if isinstance(inspection, Mapping) else None
        station = modules.get("station_number") if isinstance(modules, Mapping) else None
        if not isinstance(station, Mapping) or station.get("status") != "confirmed":
            continue
        try:
            number = int(station.get("number"))
            captured_at = _parse_timestamp(capture.get("capture_time"))
        except (TypeError, ValueError, ValidationError):
            continue
        if not 1 <= number <= 10:
            continue
        confidence = _optional_finite_float(station.get("confidence")) or 0.0
        result.append(
            (captured_at, number, str(capture.get("capture_id") or ""), confidence)
        )
    return result


def _nearest_station(
    captured_at: datetime,
    recognized: Sequence[tuple[datetime, int, str, float]],
    window_seconds: float,
) -> tuple[float, int, str] | None:
    candidates = [
        (
            abs((station_time - captured_at).total_seconds()),
            number,
            capture_id,
            confidence,
            station_time,
        )
        for station_time, number, capture_id, confidence in recognized
        if abs((station_time - captured_at).total_seconds()) <= window_seconds
    ]
    if not candidates:
        return None
    delta, number, capture_id, _, _ = min(
        candidates,
        key=lambda item: (item[0], -item[3], item[4], item[2]),
    )
    return delta, number, capture_id


def _parse_timestamp(value: Any, *, require_timezone: bool = False) -> datetime:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("thermal timestamp must be ISO-8601") from exc
    if result.tzinfo is None:
        if require_timezone:
            raise ValidationError("thermal metadata captured_at must include timezone")
        result = result.replace(tzinfo=CHINA_TIMEZONE)
    return _normalize_timestamp(result)


def _normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=CHINA_TIMEZONE)
    return value.astimezone(CHINA_TIMEZONE)


def _finite_float(value: Any, name: str) -> float:
    result = _optional_finite_float(value)
    if result is None:
        raise ValidationError(f"thermal metadata {name} must be finite")
    return result


def _optional_finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None
