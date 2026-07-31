#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "${script_dir}/.." && pwd -P)"
workspace_root="$(cd -- "${project_root}/../.." && pwd -P)"

project_venv_python="${project_root}/.venv/bin/python"
workspace_venv_python="${workspace_root}/.venv/bin/python"
local_python="${project_root}/.python/cpython-3.12.13/bin/python3.12"

if [[ -x "${project_venv_python}" ]]; then
    python_exe="${project_venv_python}"
elif [[ -x "${workspace_venv_python}" ]]; then
    python_exe="${workspace_venv_python}"
elif [[ -x "${local_python}" ]]; then
    python_exe="${local_python}"
elif command -v python3.12 >/dev/null 2>&1; then
    python_exe="$(command -v python3.12)"
else
    echo "Python 3.12 was not found. Run: bash scripts/install_ubuntu.sh" >&2
    exit 1
fi

if ! "${python_exe}" -c \
    'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)'
then
    echo "GO2 Inspection requires Python 3.12; selected: $("${python_exe}" --version 2>&1)" >&2
    exit 1
fi

cd -- "${project_root}"
export LANG="${LANG:-C.UTF-8}"
export YOLO_CONFIG_DIR="${YOLO_CONFIG_DIR:-${workspace_root}/runtime_data}"

host="${GO2_HOST:-0.0.0.0}"
port="${GO2_PORT:-8000}"

exec "${python_exe}" -m uvicorn app.api:app --host "${host}" --port "${port}"
