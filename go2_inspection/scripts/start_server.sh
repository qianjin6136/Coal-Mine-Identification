#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "${script_dir}/.." && pwd -P)"

project_venv_python="${project_root}/.venv/bin/python"
local_python="${project_root}/.python/cpython-3.12.13/bin/python3.12"

if [[ -x "${project_venv_python}" ]]; then
    python_exe="${project_venv_python}"
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
export YOLO_CONFIG_DIR="${YOLO_CONFIG_DIR:-${project_root}/runtime_data}"

requirements_file="${project_root}/requirements-runtime.txt"
if [[ ! -f "${requirements_file}" ]]; then
    echo "Runtime requirements file not found: ${requirements_file}" >&2
    exit 1
fi

mkdir -p -- "${project_root}/runtime_data"
dependency_stamp="${project_root}/runtime_data/.runtime-requirements.sha256"
requirements_hash="$("${python_exe}" -c \
    'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' \
    "${requirements_file}")"
dependency_fingerprint="${requirements_hash}|${python_exe}"
saved_fingerprint=""
if [[ -f "${dependency_stamp}" ]]; then
    saved_fingerprint="$(tr -d '\r\n' < "${dependency_stamp}")"
fi

dependency_probe='import importlib, sys
modules = ("fastapi", "uvicorn", "multipart", "numpy", "PIL", "docx", "cv2")
failures = []
for name in modules:
    try:
        importlib.import_module(name)
    except Exception as exc:
        failures.append(f"{name}: {exc}")
if failures:
    print("Runtime dependency check failed:", file=sys.stderr)
    print("\n".join(f"  - {item}" for item in failures), file=sys.stderr)
    raise SystemExit(1)'

dependencies_available=true
if ! "${python_exe}" -c "${dependency_probe}"; then
    dependencies_available=false
fi
if [[ "${dependencies_available}" != true || "${saved_fingerprint}" != "${dependency_fingerprint}" ]]; then
    echo "Synchronizing runtime dependencies..."
    "${python_exe}" -m pip install --disable-pip-version-check \
        --requirement "${requirements_file}"
    "${python_exe}" -c "${dependency_probe}"
    printf '%s\n' "${dependency_fingerprint}" > "${dependency_stamp}"
fi

host="${GO2_HOST:-0.0.0.0}"
port="${GO2_PORT:-8000}"

exec "${python_exe}" -m uvicorn app.api:app --host "${host}" --port "${port}"
