#!/usr/bin/env bash
# 在已安装 ultralytics + torch 的环境中训练 field_detect_v1 多类检测权重。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python scripts/prepare_field_v3_samples.py
python scripts/train_detector.py \
  runtime_data/datasets/field_detect_v1/dataset.yaml \
  --weights models/base/yolo26n.pt \
  --epochs 40 \
  --imgsz 640 \
  --batch 8 \
  --project runtime_data/runs/detect \
  --name field_detect_v1
BEST="runtime_data/runs/detect/field_detect_v1/weights/best.pt"
if [[ -f "$BEST" ]]; then
  mkdir -p runtime_data/models
  cp "$BEST" runtime_data/models/field_detect_v1_best.pt
  echo "copied $BEST -> runtime_data/models/field_detect_v1_best.pt"
fi
