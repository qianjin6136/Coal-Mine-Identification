"""扫描 GO2 本地抓拍包并在网络恢复后幂等补传。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.uploader import process_upload_queue, watch_upload_queue


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("queue", type=Path)
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()
    if args.watch:
        watch_upload_queue(
            args.queue,
            args.server,
            interval_seconds=args.interval,
            timeout=args.timeout,
        )
        return
    summary = process_upload_queue(
        args.queue,
        args.server,
        timeout=args.timeout,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

