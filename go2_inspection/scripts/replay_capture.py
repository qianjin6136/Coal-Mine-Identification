"""仅使用 Python 标准库上传本地图片和模拟位姿，便于现场回放联调。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.uploader import upload_capture


def main() -> None:
    """解析回放参数、构造抓拍元数据并提交到巡检服务。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--station", default="08")
    parser.add_argument("--capture-id")
    parser.add_argument("--batch-id")
    parser.add_argument("--x", type=float, default=0.0)
    parser.add_argument("--y", type=float, default=0.0)
    parser.add_argument("--yaw", type=float, default=0.0)
    args = parser.parse_args()

    for image_path in args.images:
        if not image_path.is_file():
            parser.error(f"image not found: {image_path}")
    capture_id = args.capture_id or (
        "replay_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    used_names: set[str] = set()
    upload_names: list[str] = []
    for index, path in enumerate(args.images):
        name = path.name
        if name in used_names:
            name = f"{index + 1:02d}_{name}"
        used_names.add(name)
        upload_names.append(name)
    metadata = {
        "capture_id": capture_id,
        "capture_time": datetime.now(timezone.utc).isoformat(),
        "station_id": args.station,
        "robot_pose": {
            "frame": "map",
            "x_m": args.x,
            "y_m": args.y,
            "yaw_deg": args.yaw,
        },
        "camera_id": "replay",
        "images": upload_names,
        "batch_id": args.batch_id,
    }
    response = upload_capture(args.server, metadata, args.images)
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
