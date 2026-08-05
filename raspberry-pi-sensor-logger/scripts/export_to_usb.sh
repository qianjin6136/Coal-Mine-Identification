#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]] || ! mountpoint -q "$1"; then
  echo "用法: $0 /media/$USER/USB_NAME（参数必须是已挂载的 U 盘）" >&2
  exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINATION="$(realpath "$1")"
if [[ "$DESTINATION" == "/" ]]; then
  echo "拒绝把系统根目录作为导出目标。" >&2
  exit 2
fi

for directory in gas thermal visible; do
  if [[ ! -d "$PROJECT_ROOT/data/$directory" ]]; then
    echo "没有找到 $PROJECT_ROOT/data/$directory，请先运行采集程序。" >&2
    exit 1
  fi
done

STAGING="$(mktemp -d "$DESTINATION/.sensor-export.XXXXXX")"
cleanup() {
  if [[ -n "${STAGING:-}" && -d "$STAGING" ]]; then
    rm -rf -- "$STAGING"
  fi
}
trap cleanup EXIT

for directory in gas thermal visible; do
  cp -a "$PROJECT_ROOT/data/$directory" "$STAGING/"
done
sync "$STAGING"

for directory in gas thermal visible; do
  rm -rf -- "$DESTINATION/$directory"
  mv "$STAGING/$directory" "$DESTINATION/$directory"
done
rmdir "$STAGING"
STAGING=""
sync
trap - EXIT

echo "导出完成：$DESTINATION"
echo "U 盘根目录中的 gas、thermal、visible 已更新。"
echo "树莓派上的原始数据没有删除。"
