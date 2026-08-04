"""将审核后的 JSON 框标注转换为 YOLO 训练目录。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.training_data import build_yolo_dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--classes",
        type=Path,
        default=PROJECT_ROOT / "configs" / "classes.json",
    )
    parser.add_argument("--mode", choices=("copy", "hardlink"), default="copy")
    args = parser.parse_args()
    summary = build_yolo_dataset(
        args.manifest,
        args.classes,
        args.output,
        materialize_mode=args.mode,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

