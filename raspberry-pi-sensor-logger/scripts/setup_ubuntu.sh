#!/usr/bin/env bash
set -Eeuo pipefail

# 兼容旧命令；新的统一入口同时支持 Raspberry Pi OS、Debian 和 Ubuntu。
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$SCRIPT_DIR/setup_raspberry_pi.sh" "$@"
