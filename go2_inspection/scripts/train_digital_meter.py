"""训练数字表逐位模板，并输出留一图片评估报告。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.meters.digital_model import train_template_model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--samples",
        type=Path,
        default=PROJECT_ROOT / "configs" / "digital_meter_samples.json",
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=PROJECT_ROOT
        / ".."
        / ".."
        / "runtime_data"
        / "models"
        / "digital_meter_templates.npz",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT
        / ".."
        / ".."
        / "runtime_data"
        / "digital_meter"
        / "training_metrics.json",
    )
    parser.add_argument(
        "--require-string-accuracy",
        type=float,
        default=0.80,
    )
    args = parser.parse_args()

    model, metrics = train_template_model(args.samples)
    model.save(args.model)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "model": str(args.model.resolve()),
                "report": str(args.report.resolve()),
                **{
                    key: value
                    for key, value in metrics.items()
                    if key != "samples_detail"
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if (
        metrics["string_accuracy_leave_one_image_out"]
        < args.require_string_accuracy
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
