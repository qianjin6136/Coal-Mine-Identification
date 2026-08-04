"""配置化启动 Ultralytics 检测模型训练。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path, help="dataset.yaml")
    parser.add_argument(
        "--weights",
        default=str(PROJECT_ROOT / "models" / "base" / "yolo26n.pt"),
    )
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default=None)
    parser.add_argument("--project", type=Path, default=Path("runs/detect"))
    parser.add_argument("--name", default="go2_baseline")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dataset = args.dataset.resolve()
    if not dataset.is_file():
        parser.error(f"dataset file not found: {dataset}")
    if args.epochs <= 0 or args.imgsz <= 0 or args.batch == 0:
        parser.error("epochs/imgsz must be positive and batch cannot be zero")
    options = {
        "data": str(dataset),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "project": str(args.project.resolve()),
        "name": args.name,
        "plots": True,
        "save": True,
    }
    if args.device is not None:
        options["device"] = args.device
    if args.dry_run:
        print(json.dumps({"weights": args.weights, "train": options}, indent=2))
        return
    # Ultralytics 会在 YOLO_CONFIG_DIR 下再创建自己的 Ultralytics 子目录。
    config_dir = PROJECT_ROOT / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(config_dir))
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(
            "ultralytics 未安装；请先安装项目 vision 依赖。"
        ) from exc
    model = YOLO(args.weights)
    model.train(**options)


if __name__ == "__main__":
    main()
