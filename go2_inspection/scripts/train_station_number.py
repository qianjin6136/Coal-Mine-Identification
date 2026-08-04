"""训练编号牌模板分类器，并输出稳健性评估报告。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.modules.station_number_model import train_station_number_model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--samples",
        type=Path,
        default=PROJECT_ROOT / "configs" / "station_number_samples_v2.json",
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=PROJECT_ROOT
        / ".."
        / ".."
        / "runtime_data"
        / "models"
        / "station_number_templates_v2.npz",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT
        / ".."
        / ".."
        / "runtime_data"
        / "station_number"
        / "training_metrics_v2.json",
    )
    parser.add_argument("--require-robustness-accuracy", type=float, default=0.95)
    parser.add_argument("--require-validation-accuracy", type=float, default=0.95)
    parser.add_argument("--minimum-confidence", type=float, default=0.46)
    args = parser.parse_args()

    model, metrics = train_station_number_model(
        args.samples,
        validation_minimum_confidence=args.minimum_confidence,
    )
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
                    if key
                    not in {
                        "samples_detail",
                        "robustness_failures",
                        "validation_samples_detail",
                        "validation_failures",
                        "validation_robustness_failures",
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if metrics["robustness_accuracy"] < args.require_robustness_accuracy:
        raise SystemExit(2)
    if (
        metrics["validation_accuracy"] is not None
        and metrics["validation_accuracy"] < args.require_validation_accuracy
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
