#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOT_CONFIG="/boot/firmware/config.txt"
KERNEL_CMDLINE="/boot/firmware/cmdline.txt"

if [[ ! -f "$BOOT_CONFIG" ]]; then
  echo "未找到 $BOOT_CONFIG，请确认系统是树莓派 Ubuntu 24.04。" >&2
  exit 1
fi

echo "[1/5] 安装系统依赖"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip i2c-tools v4l-utils

echo "[2/5] 启用 I2C 400 kHz 和 UART"
sudo cp -n "$BOOT_CONFIG" "${BOOT_CONFIG}.sensor-logger.bak" || true
for setting in \
  "dtparam=i2c_arm=on,i2c_arm_baudrate=400000" \
  "enable_uart=1"; do
  if ! grep -qxF "$setting" "$BOOT_CONFIG"; then
    echo "$setting" | sudo tee -a "$BOOT_CONFIG" >/dev/null
  fi
done

if [[ -f "$KERNEL_CMDLINE" ]] && grep -Eq 'console=(serial0|ttyAMA0),[0-9]+' "$KERNEL_CMDLINE"; then
  sudo cp -n "$KERNEL_CMDLINE" "${KERNEL_CMDLINE}.sensor-logger.bak" || true
  sudo sed -i -E \
    's/(^| )console=(serial0|ttyAMA0),[0-9]+//g; s/  +/ /g; s/^ //; s/ $//' \
    "$KERNEL_CMDLINE"
fi
sudo systemctl disable --now serial-getty@serial0.service 2>/dev/null || true
sudo systemctl disable --now serial-getty@ttyAMA0.service 2>/dev/null || true

echo "[3/5] 配置设备访问权限"
groups_to_add=(dialout)
if getent group i2c >/dev/null; then
  groups_to_add+=(i2c)
fi
group_list="$(IFS=,; echo "${groups_to_add[*]}")"
sudo usermod -aG "$group_list" "$USER"

echo "[4/5] 创建 Python 环境并安装项目"
cd "$PROJECT_ROOT"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools
.venv/bin/python -m pip install -e .

echo "[5/5] 安装完成"
echo "请手动执行 sudo reboot。重启后先运行："
echo "  cd $PROJECT_ROOT"
echo "  v4l2-ctl --list-devices"
echo "  .venv/bin/python -m sensor_logger --once --station-id 08"
