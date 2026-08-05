"""用带标签数字表样本评估已保存模型，并输出逐图结果。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.image_io import read_bgr_image
from app.meters.digital_model import (
    DigitalMeterRecognizer,
    TemplateDigitModel,
    configured_digit_counts,
    expand_digital_sample_items,
)


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
        / "runtime_data"
        / "models"
        / "digital_meter_templates_v2.npz",
    )
    parser.add_argument("--minimum-confidence", type=float, default=0.55)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = json.loads(args.samples.read_text(encoding="utf-8"))
    format_config = config["format"]
    recognizer = DigitalMeterRecognizer(
        TemplateDigitModel.load(args.model),
        digit_count=configured_digit_counts(format_config),
        decimal_places=int(format_config["decimal_places"]),
        allow_negative=bool(format_config.get("allow_negative", True)),
        minimum_confidence=args.minimum_confidence,
    )
    rows = []
    skipped = []
    correct = 0
    for sample in expand_digital_sample_items(config, PROJECT_ROOT):
        image_path = Path(sample["file"])
        if not image_path.is_absolute():
            image_path = (PROJECT_ROOT / image_path).resolve()
        image = read_bgr_image(image_path)
        skip_unreadable = bool(
            config.get("discovery", {}).get("skip_unreadable", False)
        )
        if image is None and skip_unreadable:
            skipped.append(str(image_path))
            continue
        result = recognizer.read_image(image)
        row = {
            "file": str(image_path),
            "expected": sample["text"],
            **result.to_dict(),
        }
        row["correct"] = result.raw_text == sample["text"]
        correct += int(row["correct"])
        rows.append(row)
    report = {
        "samples": len(rows),
        "correct": correct,
        "accuracy": correct / len(rows) if rows else 0.0,
        "skipped_unreadable_files": skipped,
        "results": rows,
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
