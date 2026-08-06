#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
VENV_ROOT="${PROJECT_ROOT}/.venv"

if ((EUID == 0)); then
  echo "请使用普通登录用户运行本脚本（脚本会在需要时调用 sudo），不要执行 sudo $0。" >&2
  exit 2
fi

if [[ ! -r /etc/os-release ]]; then
  echo "无法识别系统：缺少 /etc/os-release。" >&2
  exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release
case "${ID:-}" in
  debian|raspbian|ubuntu) ;;
  *)
    echo "仅支持 Raspberry Pi OS/Debian/Ubuntu，当前系统：${PRETTY_NAME:-unknown}。" >&2
    exit 1
    ;;
esac

MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
if [[ "$MODEL" != *"Raspberry Pi"* && "${SENSOR_LOGGER_ALLOW_NON_PI:-0}" != "1" ]]; then
  echo "未检测到树莓派硬件（model=${MODEL:-unknown}）。" >&2
  echo "如仅需在普通 Linux 主机验证安装，可设置 SENSOR_LOGGER_ALLOW_NON_PI=1。" >&2
  exit 1
fi

case "$(uname -m)" in
  aarch64|armv7l|armv6l) ;;
  *)
    echo "警告：当前架构 $(uname -m) 不是常见树莓派 ARM 架构。" >&2
    ;;
esac

BOOT_CONFIG=""
KERNEL_CMDLINE=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
  if [[ -f "$candidate" ]]; then
    BOOT_CONFIG="$candidate"
    break
  fi
done
for candidate in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
  if [[ -f "$candidate" ]]; then
    KERNEL_CMDLINE="$candidate"
    break
  fi
done
if [[ -z "$BOOT_CONFIG" ]]; then
  echo "未找到树莓派启动配置 /boot/firmware/config.txt 或 /boot/config.txt。" >&2
  exit 1
fi

echo "[1/6] 安装系统依赖（${PRETTY_NAME:-Linux}）"
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  i2c-tools \
  python3-dev \
  python3-opencv \
  python3-pil \
  python3-pip \
  python3-venv \
  v4l-utils

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "需要 Python 3.9 或更高版本，当前是 $(python3 --version 2>&1)。" >&2
  echo "建议安装 64 位 Raspberry Pi OS Bookworm 或 Ubuntu 22.04/24.04。" >&2
  exit 1
fi

echo "[2/6] 启用 I2C（400 kHz）和 GPIO 串口"
sudo cp -n -- "$BOOT_CONFIG" "${BOOT_CONFIG}.sensor-logger.bak" || true
for setting in \
  "dtparam=i2c_arm=on,i2c_arm_baudrate=400000" \
  "enable_uart=1"; do
  if ! sudo grep -qxF "$setting" "$BOOT_CONFIG"; then
    printf '%s\n' "$setting" | sudo tee -a "$BOOT_CONFIG" >/dev/null
  fi
done

if [[ -n "$KERNEL_CMDLINE" ]] \
  && sudo grep -Eq '(^|[[:space:]])console=(serial0|ttyAMA[0-9]*|ttyS[0-9]*),[0-9]+' "$KERNEL_CMDLINE"; then
  sudo cp -n -- "$KERNEL_CMDLINE" "${KERNEL_CMDLINE}.sensor-logger.bak" || true
  sudo sed -i -E \
    's/(^|[[:space:]])console=(serial0|ttyAMA[0-9]*|ttyS[0-9]*),[0-9]+//g; s/[[:space:]]+/ /g; s/^ //; s/ $//' \
    "$KERNEL_CMDLINE"
fi
sudo systemctl disable --now serial-getty@serial0.service 2>/dev/null || true
sudo systemctl disable --now serial-getty@ttyAMA0.service 2>/dev/null || true
printf '%s\n' i2c-dev | sudo tee /etc/modules-load.d/sensor-logger.conf >/dev/null
sudo modprobe i2c-dev || true

echo "[3/6] 配置当前用户的设备权限"
GROUPS_TO_ADD=(dialout video)
if getent group i2c >/dev/null; then
  GROUPS_TO_ADD+=(i2c)
fi
GROUP_LIST="$(IFS=,; echo "${GROUPS_TO_ADD[*]}")"
sudo usermod -aG "$GROUP_LIST" "$(id -un)"

echo "[4/6] 创建与系统架构匹配的 Python 虚拟环境"
cd -- "$PROJECT_ROOT"
# 即使从 Windows 复制来的目录里带有旧 .venv，这条命令也会创建 Linux 的 bin/python。
python3 -m venv --system-site-packages "$VENV_ROOT"
"$VENV_ROOT/bin/python" -m pip install --upgrade pip setuptools wheel

echo "[5/6] 安装采集程序和 ARM 兼容依赖"
"$VENV_ROOT/bin/python" -m pip install --editable "$PROJECT_ROOT"

echo "[6/6] 验证 Python 模块"
"$VENV_ROOT/bin/python" - <<'PY'
import sys

import PIL
import adafruit_mlx90640
import board
import cv2
import serial
import sensor_logger

print(f"Python {sys.version.split()[0]}")
print(f"Pillow {PIL.__version__}; OpenCV {cv2.__version__}")
print("采集程序及驱动模块导入成功")
PY

cat <<EOF

安装完成：${PROJECT_ROOT}
系统配置已更新，必须重启后设备权限和 UART/I2C 配置才会完整生效：
  sudo reboot

重启后执行：
  cd "${PROJECT_ROOT}"
  ./scripts/diagnose_hardware.sh
  .venv/bin/python -m sensor_logger --once --require-all-hardware
EOF
