"""对 number/light/pointertable 各抽一张图跑通三条链路并写证据图。"""

from __future__ import annotations

import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.detectors.field_cv_backend import FieldCvDetector
from app.domain import CaptureMetadata, RobotPose
from app.modules.registry import build_module_registry_from_config
from app.pipeline import InspectionPipeline, merge_module_detections
from app.annotation import annotate_image


def _run_one(kind: str, image: Path, output_dir: Path) -> dict:
    config = json.loads((PROJECT_ROOT / "configs" / "modules.json").read_text())
    registry = build_module_registry_from_config(config, PROJECT_ROOT)
    detector = FieldCvDetector(0.35)
    pipeline = InspectionPipeline(
        detector=detector,
        processed_root=output_dir,
        module_registry=registry,
    )
    meta = CaptureMetadata(
        capture_id=f"smoke_{kind}",
        capture_time="2026-08-07T20:00:00+08:00",
        station_id="1",
        robot_pose=RobotPose(),
        camera_id="cam0",
        image_names=(image.name,),
    )
    result = pipeline.process(meta, [image])
    return {
        "kind": kind,
        "image": str(image),
        "object_types": sorted({item.get("type") for item in result.objects}),
        "modules": {
            key: {
                "status": value.get("status"),
                "present": value.get("present"),
                "raw_text": value.get("raw_text"),
                "count": value.get("count"),
                "red": (value.get("red") or {}).get("on")
                if isinstance(value.get("red"), dict)
                else None,
                "green": (value.get("green") or {}).get("on")
                if isinstance(value.get("green"), dict)
                else None,
            }
            for key, value in result.modules.items()
        },
        "annotated_image": result.annotated_image,
        "warnings": result.warnings,
    }


def main() -> None:
    sample_root = PROJECT_ROOT.parent / "sample"
    output_dir = PROJECT_ROOT / "runtime_data" / "smoke_field"
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = {
        "number": next(iter(sorted((sample_root / "number").glob("*.jpg")))),
        "light": sample_root / "light" / "微信图片_20260807164511_38_26.jpg",
        "pointertable": sample_root
        / "pointertable"
        / "微信图片_20260807164550_54_26.jpg",
    }
    summary = []
    for kind, image in cases.items():
        if not image.is_file():
            summary.append({"kind": kind, "error": f"missing {image}"})
            continue
        summary.append(_run_one(kind, image, output_dir))
    out = output_dir / "smoke_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
