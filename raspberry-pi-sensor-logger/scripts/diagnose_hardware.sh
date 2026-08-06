#!/usr/bin/env bash
set -u

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON_EXE="${PROJECT_ROOT}/.venv/bin/python"
SERIAL_DEVICE="${SENSOR_LOGGER_SERIAL_PORT:-/dev/serial0}"
CAMERA_DEVICE="${SENSOR_LOGGER_CAMERA_DEVICE:-/dev/video0}"
FAILURES=0

pass() { printf '[通过] %s\n' "$1"; }
fail() { printf '[失败] %s\n' "$1" >&2; FAILURES=$((FAILURES + 1)); }
info() { printf '[信息] %s\n' "$1"; }

MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
info "设备：${MODEL:-无法读取型号}"
info "系统：$(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-unknown}")"
info "架构：$(uname -m)"

if [[ -x "$PYTHON_EXE" ]]; then
  if "$PYTHON_EXE" -c 'import adafruit_mlx90640, board, cv2, PIL, serial, sensor_logger' \
    >/dev/null 2>&1; then
    pass "Python 虚拟环境和全部运行模块"
  else
    fail "Python 模块导入失败；运行 $PYTHON_EXE -c 'import adafruit_mlx90640, board, cv2, PIL, serial, sensor_logger' 查看详情"
  fi
else
  fail "缺少 $PYTHON_EXE；请先运行 ./scripts/setup_raspberry_pi.sh"
fi

if id -nG | tr ' ' '\n' | grep -qx dialout; then
  pass "当前会话属于 dialout 组"
else
  fail "当前会话不属于 dialout 组；安装后需要重新登录或重启"
fi
if id -nG | tr ' ' '\n' | grep -qx video; then
  pass "当前会话属于 video 组"
else
  fail "当前会话不属于 video 组；安装后需要重新登录或重启"
fi

if [[ -e /dev/i2c-1 ]]; then
  pass "I2C 总线 /dev/i2c-1 存在"
  I2C_SCAN="$(i2cdetect -y 1 2>&1 || true)"
  if grep -Eq '(^|[[:space:]])33([[:space:]]|$)' <<<"$I2C_SCAN"; then
    pass "检测到 MLX90640 地址 0x33"
  else
    fail "I2C 总线上没有检测到 0x33；请断电检查接线和供电"
    printf '%s\n' "$I2C_SCAN"
  fi
else
  fail "缺少 /dev/i2c-1；I2C 尚未启用或系统尚未重启"
fi

if [[ -e "$SERIAL_DEVICE" ]]; then
  if [[ -r "$SERIAL_DEVICE" && -w "$SERIAL_DEVICE" ]]; then
    pass "气体串口 $SERIAL_DEVICE 存在且当前用户可读写"
  else
    fail "气体串口 $SERIAL_DEVICE 存在但当前用户无读写权限"
  fi
  info "串口指向：$(readlink -f "$SERIAL_DEVICE" 2>/dev/null || printf '%s' "$SERIAL_DEVICE")"
else
  fail "气体串口 $SERIAL_DEVICE 不存在；请确认 enable_uart=1 并已重启"
fi

if [[ -e "$CAMERA_DEVICE" ]]; then
  if v4l2-ctl --device "$CAMERA_DEVICE" --all >/dev/null 2>&1; then
    pass "USB 相机 $CAMERA_DEVICE 可由 V4L2 打开"
  else
    fail "USB 相机 $CAMERA_DEVICE 存在但无法打开；可能被占用或权限不足"
  fi
else
  fail "USB 相机 $CAMERA_DEVICE 不存在；请运行 v4l2-ctl --list-devices 确认编号"
fi

AVAILABLE_MB="$(df -Pm "$PROJECT_ROOT" | awk 'NR==2 {print $4}')"
if [[ "$AVAILABLE_MB" =~ ^[0-9]+$ && "$AVAILABLE_MB" -ge 2048 ]]; then
  pass "项目磁盘剩余 ${AVAILABLE_MB} MiB"
else
  fail "项目磁盘空间不足 2 GiB（当前 ${AVAILABLE_MB:-unknown} MiB）"
fi

printf '\n诊断完成：%d 项失败。\n' "$FAILURES"
if ((FAILURES > 0)); then
  exit 1
fi
