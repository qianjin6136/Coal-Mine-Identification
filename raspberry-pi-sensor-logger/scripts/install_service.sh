#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"
SERVICE_TEMPLATE="${PROJECT_ROOT}/deploy/sensor-logger.service"
INSTALL_USER="$(id -un)"

if ((EUID == 0)); then
  echo "请使用普通登录用户运行本脚本，不要执行 sudo $0。" >&2
  exit 2
fi
if (($# > 1)) || [[ ${1:-} && ! ${1:-} =~ ^[A-Za-z0-9._-]{1,64}$ ]]; then
  echo "用法: $0 [STATION_ID]（工位编号可省略）" >&2
  exit 2
fi
STATION_ID="${1:-}"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "缺少 Linux 虚拟环境 $VENV_PYTHON，请先运行 ./scripts/setup_raspberry_pi.sh。" >&2
  exit 1
fi
if [[ ! -r "$SERVICE_TEMPLATE" ]]; then
  echo "缺少服务模板：$SERVICE_TEMPLATE" >&2
  exit 1
fi

escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}

ESCAPED_USER="$(escape_sed_replacement "$INSTALL_USER")"
ESCAPED_ROOT="$(escape_sed_replacement "$PROJECT_ROOT")"
ESCAPED_PYTHON="$(escape_sed_replacement "$VENV_PYTHON")"
TEMP_SERVICE="$(mktemp)"
trap 'rm -f -- "$TEMP_SERVICE"' EXIT
sed \
  -e "s|@@SENSOR_LOGGER_USER@@|${ESCAPED_USER}|g" \
  -e "s|@@PROJECT_ROOT@@|${ESCAPED_ROOT}|g" \
  -e "s|@@VENV_PYTHON@@|${ESCAPED_PYTHON}|g" \
  "$SERVICE_TEMPLATE" >"$TEMP_SERVICE"

if grep -q '@@' "$TEMP_SERVICE"; then
  echo "服务模板中仍有未替换的占位符。" >&2
  exit 1
fi

sudo install -m 0644 "$TEMP_SERVICE" /etc/systemd/system/sensor-logger.service
printf 'SENSOR_LOGGER_STATION_ID=%s\n' "$STATION_ID" \
  | sudo tee /etc/default/sensor-logger >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now sensor-logger.service

echo "服务已启动"
echo "运行用户：$INSTALL_USER"
echo "项目目录：$PROJECT_ROOT"
echo "工位编号：${STATION_ID:-未设置（当前单图格式不需要）}"
echo "查看状态：sudo systemctl status sensor-logger.service"
echo "实时日志：journalctl -u sensor-logger.service -f"
