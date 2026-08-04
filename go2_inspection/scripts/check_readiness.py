"""检查数据到达前的配置、依赖和运行目录准备状态。"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.settings import Settings, load_json_mapping


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    settings = Settings.load(args.settings)
    classes = load_json_mapping(settings.classes_path)
    stations = load_json_mapping(settings.stations_path)
    references = load_json_mapping(settings.analog_references_path)
    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    trainable = [
        name
        for name, config in classes.items()
        if not isinstance(config, dict) or config.get("train", True)
    ]
    add(
        "classes",
        "ok" if trainable else "blocker",
        f"{len(trainable)} 个可训练类别；正式标注前需替换工程占位清单",
    )
    add(
        "stations",
        "ok" if stations else "blocker",
        f"{len(stations)} 个工位配置",
    )
    missing_references = []
    invalid_references = []
    for reference_id, config in references.items():
        if not isinstance(config, dict):
            invalid_references.append(reference_id)
            continue
        if not all(
            name in config
            for name in ("station_id", "normal_angle_deg", "tolerance_deg")
        ):
            invalid_references.append(reference_id)
        image_value = config.get("normal_reference_image")
        if image_value:
            image_path = Path(str(image_value))
            if not image_path.is_absolute():
                image_path = PROJECT_ROOT / image_path
            if not image_path.is_file():
                missing_references.append(str(image_path))
    if invalid_references:
        add(
            "analog_references",
            "blocker",
            f"字段不完整：{', '.join(invalid_references)}",
        )
    elif missing_references:
        add(
            "analog_references",
            "warning",
            f"{len(missing_references)} 张正常参考图尚未到位",
        )
    else:
        add(
            "analog_references",
            "ok" if references else "warning",
            f"{len(references)} 个指针表参考配置",
        )

    add(
        "opencv",
        "ok" if importlib.util.find_spec("cv2") else "blocker",
        "仪表图像处理依赖",
    )
    ultralytics_ready = importlib.util.find_spec("ultralytics") is not None
    add(
        "ultralytics",
        "ok" if ultralytics_ready else "warning",
        "训练/YOLO 推理依赖；数据质检和 noop 服务不受影响",
    )
    if importlib.util.find_spec("torch") is None:
        add("gpu_training", "warning", "PyTorch 未安装")
    else:
        import torch

        if torch.cuda.is_available():
            add(
                "gpu_training",
                "ok",
                f"{torch.cuda.get_device_name(0)} · torch {torch.__version__}",
            )
        else:
            add(
                "gpu_training",
                "warning",
                f"当前 torch {torch.__version__} 未启用 CUDA，将使用 CPU",
            )
    detector_status = (
        "ok" if settings.detector_backend == "ultralytics" else "warning"
    )
    add(
        "detector",
        detector_status,
        f"当前后端：{settings.detector_backend}",
    )
    if args.manifest:
        if args.manifest.is_file():
            records = [
                json.loads(line)
                for line in args.manifest.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            split_counts: dict[str, int] = {}
            for record in records:
                split = str(record.get("split") or "unassigned")
                split_counts[split] = split_counts.get(split, 0) + 1
            missing_splits = [
                name
                for name in ("train", "val", "test")
                if split_counts.get(name, 0) == 0
            ]
            add(
                "dataset_manifest",
                "warning" if missing_splits else "ok",
                (
                    f"{len(records)} 张图片；缺少集合：{', '.join(missing_splits)}"
                    if missing_splits
                    else f"{len(records)} 张图片；三个集合均非空"
                ),
            )
        else:
            add("dataset_manifest", "blocker", f"文件不存在：{args.manifest}")
    summary = {
        "ready": not any(item["status"] == "blocker" for item in checks),
        "checks": checks,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.strict and not summary["ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
