#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]] || ! mountpoint -q "$1"; then
  echo "用法: $0 /media/$USER/USB_NAME（参数必须是已挂载的 U 盘）" >&2
  exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for directory in gas thermal visible; do
  if [[ ! -d "$PROJECT_ROOT/data/$directory" ]]; then
    echo "没有找到 $PROJECT_ROOT/data/$directory，请先运行采集程序。" >&2
    exit 1
  fi
done

DESTINATION="$1/inspection-export-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DESTINATION"
cp -a "$PROJECT_ROOT/data/gas" "$DESTINATION/"
cp -a "$PROJECT_ROOT/data/thermal" "$DESTINATION/"
cp -a "$PROJECT_ROOT/data/visible" "$DESTINATION/"
sync

echo "导出完成：$DESTINATION"
echo "树莓派上的原始数据没有删除。"
