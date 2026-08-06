"""读取 JSON 配置，并将其中的相对路径统一解析为项目内绝对路径。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .errors import ConfigurationError


@dataclass(frozen=True)
class Settings:
    """应用启动后保持不变的配置快照。"""

    project_root: Path
    dataset_inbox_path: Path
    storage_root: Path
    database_path: Path
    detector_backend: str
    detector_weights: Path | None
    detector_confidence: float
    fusion_iou: float
    classes_path: Path
    stations_path: Path
    modules_path: Path
    max_image_bytes: int = 20 * 1024 * 1024

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        """加载主配置；相对配置路径和资源路径均以项目根目录为基准。"""

        project_root = Path(__file__).resolve().parents[1]
        config_path = Path(path) if path else project_root / "configs" / "app.json"
        if not config_path.is_absolute():
            config_path = project_root / config_path
        try:
            data: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError(f"settings file not found: {config_path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"invalid settings JSON: {config_path}") from exc

        def resolve(value: str | None) -> Path | None:
            # 集中处理路径可避免服务从不同工作目录启动时读写到意外位置。
            if value is None:
                return None
            result = Path(value)
            resolved = result if result.is_absolute() else project_root / result
            return resolved.resolve()

        detector = data.get("detector", {})
        pipeline = data.get("pipeline", {})
        storage_root = resolve(data.get("storage_root", "data"))
        dataset_inbox_path = resolve(
            data.get("dataset_inbox_path", "dataset_inbox")
        )
        database_path = resolve(data.get("database_path", "data/database/inspection.db"))
        classes_path = resolve(data.get("classes_path", "configs/classes.json"))
        stations_path = resolve(data.get("stations_path", "configs/stations.json"))
        modules_path = resolve(data.get("modules_path", "configs/modules.json"))
        if None in (
            storage_root,
            dataset_inbox_path,
            database_path,
            classes_path,
            stations_path,
            modules_path,
        ):
            raise ConfigurationError("required settings paths cannot be null")

        confidence = float(detector.get("confidence", 0.35))
        if not 0.0 <= confidence <= 1.0:
            raise ConfigurationError("detector.confidence must be in [0, 1]")
        fusion_iou = float(pipeline.get("fusion_iou", 0.45))
        if not 0.0 <= fusion_iou <= 1.0:
            raise ConfigurationError("pipeline.fusion_iou must be in [0, 1]")

        return cls(
            project_root=project_root,
            dataset_inbox_path=dataset_inbox_path,
            storage_root=storage_root,
            database_path=database_path,
            detector_backend=str(detector.get("backend", "noop")),
            detector_weights=resolve(detector.get("weights")),
            detector_confidence=confidence,
            fusion_iou=fusion_iou,
            classes_path=classes_path,
            stations_path=stations_path,
            modules_path=modules_path,
            max_image_bytes=int(data.get("max_image_bytes", 20 * 1024 * 1024)),
        )


def load_json_mapping(path: Path) -> dict[str, Any]:
    """加载必须以 JSON 对象为顶层结构的辅助配置文件。"""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"configuration file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"invalid configuration JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"configuration must be a JSON object: {path}")
    return value
