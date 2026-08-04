"""扫描现场数据并生成可追溯的数据清单、质检报告和分组切分。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.dataset import assign_grouped_splits, inspect_dataset, write_dataset_reports


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect GO2 images and create leakage-safe dataset manifests."
    )
    parser.add_argument("source", type=Path, help="原始数据根目录")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data" / "dataset_reports",
        help="报告输出目录",
    )
    parser.add_argument("--train", type=float, default=0.70)
    parser.add_argument("--val", type=float, default=0.15)
    parser.add_argument("--test", type=float, default=0.15)
    parser.add_argument("--seed", default="go2-inspection-v1")
    args = parser.parse_args()

    if not args.source.is_dir():
        parser.error(f"source directory not found: {args.source}")
    records = inspect_dataset(args.source)
    if not records:
        parser.error(f"no supported images found below: {args.source}")
    assigned = assign_grouped_splits(
        records,
        {"train": args.train, "val": args.val, "test": args.test},
        seed=args.seed,
    )
    paths = write_dataset_reports(assigned, args.output)
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    print(json.dumps({"outputs": {k: str(v) for k, v in paths.items()}, **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

