"""在冻结验证/测试集上运行 Ultralytics 指标评估并保存摘要。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("weights", type=Path)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--split", choices=("val", "test"), default="test")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default=None)
    parser.add_argument("--output", type=Path, default=Path("evaluation_metrics.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for path, name in ((args.weights, "weights"), (args.dataset, "dataset")):
        if not path.is_file():
            parser.error(f"{name} file not found: {path}")
    options: dict[str, Any] = {
        "data": str(args.dataset.resolve()),
        "split": args.split,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "plots": True,
    }
    if args.device is not None:
        options["device"] = args.device
    if args.dry_run:
        print(
            json.dumps(
                {"weights": str(args.weights.resolve()), "validation": options},
                indent=2,
            )
        )
        return
    config_dir = PROJECT_ROOT / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(config_dir))
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(
            "ultralytics 未安装；请先安装项目 vision 依赖。"
        ) from exc
    metrics = YOLO(str(args.weights.resolve())).val(**options)
    summary = {
        "weights": str(args.weights.resolve()),
        "dataset": str(args.dataset.resolve()),
        "split": args.split,
        "results_dict": {
            str(key): _json_value(value)
            for key, value in getattr(metrics, "results_dict", {}).items()
        },
        "save_dir": str(getattr(metrics, "save_dir", "")),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _json_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            pass
    return str(value)


if __name__ == "__main__":
    main()
