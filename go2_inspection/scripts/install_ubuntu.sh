#!/usr/bin/env bash
set -Eeuo pipefail

readonly python_version="3.12.13"
readonly python_sha256="c08bc65a81971c1dd5783182826503369466c7e67374d1646519adf05207b684"
readonly python_archive="Python-${python_version}.tar.xz"
readonly python_url="https://www.python.org/ftp/python/${python_version}/${python_archive}"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "${script_dir}/.." && pwd -P)"
python_prefix="${project_root}/.python/cpython-${python_version}"
venv_root="${project_root}/.venv"
profile="runtime"
install_system_deps=true
force_source_build=false

usage() {
    cat <<'EOF'
Usage: bash scripts/install_ubuntu.sh [options]

Options:
  --profile runtime  Install API and meter runtime dependencies (default).
  --profile full     Also install Ultralytics/YOLO dependencies.
  --profile dev      Install runtime and test dependencies.
  --skip-system-deps Do not run apt-get; required packages must already exist.
  --build-python     Build the project-local CPython 3.12.13 even if python3.12 exists.
  -h, --help         Show this help.
EOF
}

while (($# > 0)); do
    case "$1" in
        --profile)
            if (($# < 2)); then
                echo "--profile requires runtime, full, or dev" >&2
                exit 2
            fi
            profile="$2"
            shift 2
            ;;
        --skip-system-deps)
            install_system_deps=false
            shift
            ;;
        --build-python)
            force_source_build=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "${profile}" in
    runtime) requirements_file="${project_root}/requirements-runtime.txt" ;;
    full) requirements_file="${project_root}/requirements.txt" ;;
    dev) requirements_file="${project_root}/requirements-dev.txt" ;;
    *)
        echo "Unsupported profile: ${profile}; use runtime, full, or dev" >&2
        exit 2
        ;;
esac

if [[ ! -r /etc/os-release ]]; then
    echo "Cannot identify this Linux distribution: /etc/os-release is missing" >&2
    exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
    echo "This installer supports Ubuntu only; detected: ${ID:-unknown}" >&2
    exit 1
fi
case "${VERSION_ID:-}" in
    20.04|22.04) ;;
    *)
        echo "Supported releases are Ubuntu 20.04 and 22.04; detected: ${VERSION_ID:-unknown}" >&2
        exit 1
        ;;
esac

run_as_root() {
    if ((EUID == 0)); then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        echo "sudo is required to install Ubuntu system packages" >&2
        return 1
    fi
}

install_build_dependencies() {
    run_as_root apt-get update
    run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
        build-essential \
        ca-certificates \
        curl \
        libbz2-dev \
        libffi-dev \
        libgdbm-dev \
        libglib2.0-0 \
        libgl1 \
        liblzma-dev \
        libncurses-dev \
        libnss3-dev \
        libreadline-dev \
        libsqlite3-dev \
        libssl-dev \
        pkg-config \
        uuid-dev \
        xz-utils \
        zlib1g-dev
}

build_project_python() (
    mkdir -p -- "$(dirname -- "${python_prefix}")"
    build_root="$(mktemp -d "${TMPDIR:-/tmp}/go2-python-build.XXXXXX")"

    cleanup() {
        case "${build_root}" in
            "${TMPDIR:-/tmp}"/go2-python-build.*)
                rm -rf -- "${build_root}"
                ;;
        esac
    }
    trap cleanup EXIT

    cd -- "${build_root}"
    curl --fail --location --retry 3 --output "${python_archive}" "${python_url}"
    printf '%s  %s\n' "${python_sha256}" "${python_archive}" | sha256sum --check -
    tar --extract --file "${python_archive}"
    cd -- "Python-${python_version}"
    ./configure \
        --prefix="${python_prefix}" \
        --with-ensurepip=install
    make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '2')"
    make install
)

is_compatible_python() {
    "$1" -c \
        'import ensurepip, sqlite3, ssl, sys, venv; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)'
}

if [[ "${install_system_deps}" == true ]]; then
    install_build_dependencies
fi

python_exe=""
if [[ "${force_source_build}" == false ]] && command -v python3.12 >/dev/null 2>&1; then
    candidate="$(command -v python3.12)"
    if is_compatible_python "${candidate}"; then
        python_exe="${candidate}"
    fi
fi

if [[ -z "${python_exe}" ]]; then
    python_exe="${python_prefix}/bin/python3.12"
    if [[ "${force_source_build}" == true ]] \
        || [[ ! -x "${python_exe}" ]] \
        || ! is_compatible_python "${python_exe}"
    then
        build_project_python
    fi
fi

if ! is_compatible_python "${python_exe}"; then
    echo "Python 3.12 build is incomplete: ${python_exe}" >&2
    exit 1
fi

if [[ -d "${venv_root}" && ! -x "${venv_root}/bin/python" ]]; then
    echo "Existing ${venv_root} is not a Linux virtual environment." >&2
    echo "Move or remove it explicitly, then run this installer again." >&2
    exit 1
fi

if [[ ! -x "${venv_root}/bin/python" ]]; then
    "${python_exe}" -m venv "${venv_root}"
fi

venv_python="${venv_root}/bin/python"
if ! is_compatible_python "${venv_python}"; then
    echo "Existing virtual environment does not use Python 3.12: ${venv_root}" >&2
    echo "Move or remove it explicitly, then run this installer again." >&2
    exit 1
fi

"${venv_python}" -m pip install --upgrade pip setuptools wheel
"${venv_python}" -m pip install --requirement "${requirements_file}"

cd -- "${project_root}"
"${venv_python}" -m compileall -q app scripts
"${venv_python}" -c \
    'import cv2, fastapi, numpy, PIL, uvicorn; from app.api import app; assert app.title'

if [[ "${profile}" == "dev" ]]; then
    "${venv_python}" -m unittest discover -s tests -v
fi

cat <<EOF

Installation completed.
Ubuntu: ${VERSION_ID}
Python: $("${venv_python}" --version)
Profile: ${profile}
Start:   bash scripts/start_server.sh
Health:  http://127.0.0.1:8000/health
EOF
