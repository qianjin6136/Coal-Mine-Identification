#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_ROOT="/home/aabb942218/sensor-reader"

if [[ $# -ne 1 ]] || [[ ! "$1" =~ ^[A-Za-z0-9._-]{1,64}$ ]]; then
  echo "用法: $0 STATION_ID（仅允许字母、数字、点、下划线和连字符）" >&2
  exit 2
fi
STATION_ID="$1"

if [[ "$PROJECT_ROOT" != "$EXPECTED_ROOT" ]]; then
  echo "systemd 服务要求项目位于 $EXPECTED_ROOT，当前是 $PROJECT_ROOT。" >&2
  exit 1
fi
if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  echo "请先运行 ./scripts/setup_ubuntu.sh。" >&2
  exit 1
fi

sudo install -m 0644 \
  "$PROJECT_ROOT/deploy/sensor-logger.service" \
  /etc/systemd/system/sensor-logger.service
printf 'SENSOR_LOGGER_STATION_ID=%s\n' "$STATION_ID" | \
  sudo tee /etc/default/sensor-logger >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now sensor-logger.service

echo "服务已启动，工位编号：$STATION_ID"
echo "查看状态：sudo systemctl status sensor-logger.service"
echo "实时日志：journalctl -u sensor-logger.service -f"
