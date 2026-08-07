#!/usr/bin/env bash
set -Eeuo pipefail

trap 'status=$?; printf "\n安装失败（退出码 %s，脚本第 %s 行）：%s\n" "$status" "$LINENO" "$BASH_COMMAND" >&2; printf "请保留上方最后 30 行输出用于排错。\n" >&2; exit "$status"' ERR

readonly python_version="3.12.13"
readonly python_sha256="c08bc65a81971c1dd5783182826503369466c7e67374d1646519adf05207b684"
readonly python_archive="Python-${python_version}.tar.xz"
readonly official_python_url="https://www.python.org/ftp/python/${python_version}/${python_archive}"
readonly huawei_python_url="https://mirrors.huaweicloud.com/python/${python_version}/${python_archive}"
readonly npmmirror_python_url="https://cdn.npmmirror.com/binaries/python/${python_version}/${python_archive}"
readonly python_url="${GO2_PYTHON_SOURCE_URL:-${official_python_url}}"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "${script_dir}/.." && pwd -P)"
python_prefix="${project_root}/.python/cpython-${python_version}"
venv_root="${project_root}/.venv"
profile="runtime"
install_system_deps=true
force_source_build=false
recreate_venv=false

usage() {
    cat <<'EOF'
Usage: bash scripts/install_ubuntu.sh [options]

Options:
  --profile runtime  Install API and meter runtime dependencies (default).
  --profile full     Also install Ultralytics/YOLO dependencies.
  --profile gpu-4060 Install Ultralytics with PyTorch CUDA 12.6 for RTX 4060.
  --profile dev      Install runtime and test dependencies.
  --skip-system-deps Do not run apt-get; required packages must already exist.
  --build-python     Build the project-local CPython 3.12.13 even if python3.12 exists.
  --recreate-venv    Back up the existing .venv and create a clean Linux environment.
  -h, --help         Show this help.

Environment:
  GO2_BUILD_JOBS=2             Limit parallel CPython compilation jobs.
  GO2_PYTHON_SOURCE_URL=<URL>  Use a source mirror; SHA-256 is still verified.
                               If unset, official URL is tried first, then
                               Huawei Cloud and npmmirror fallbacks.
  GO2_PIP_INDEX_URL=<URL>      Prefer a PyPI mirror (default: Tsinghua).
EOF
}

while (($# > 0)); do
    case "$1" in
        --profile)
            if (($# < 2)); then
                echo "--profile requires runtime, full, gpu-4060, or dev" >&2
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
        --recreate-venv)
            recreate_venv=true
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
    gpu-4060) requirements_file="${project_root}/requirements-gpu-ubuntu4060.txt" ;;
    dev) requirements_file="${project_root}/requirements-dev.txt" ;;
    *)
        echo "Unsupported profile: ${profile}; use runtime, full, gpu-4060, or dev" >&2
        exit 2
        ;;
esac

if ((EUID == 0)); then
    echo "请使用普通登录用户运行安装脚本，不要在命令前加 sudo。" >&2
    echo "脚本会在 apt-get 阶段自行调用 sudo。" >&2
    exit 2
fi

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
    20.04|22.04|24.04) ;;
    *)
        echo "Supported releases are Ubuntu 20.04, 22.04 and 24.04; detected: ${VERSION_ID:-unknown}" >&2
        exit 1
        ;;
esac

write_probe="${project_root}/.go2-install-write-test.$$"
if ! (umask 077 && : >"${write_probe}") 2>/dev/null; then
    echo "当前用户 $(id -un) 无法写入项目目录：${project_root}" >&2
    echo "如果项目位于 /opt，请先修正所有权，例如：" >&2
    echo "  sudo chown -R $(id -un):$(id -gn) \"${project_root}\"" >&2
    exit 1
fi
rm -f -- "${write_probe}"
for managed_path in "${project_root}/.python" "${venv_root}" "${project_root}/runtime_data"; do
    if [[ -e "${managed_path}" && ! -w "${managed_path}" ]]; then
        echo "当前用户 $(id -un) 无法写入：${managed_path}" >&2
        echo "这通常是以前用 sudo 运行安装程序造成的；请修正项目所有权后重试。" >&2
        exit 1
    fi
done

if [[ "${profile}" == "gpu-4060" ]]; then
    if [[ "$(uname -m)" != "x86_64" ]]; then
        echo "gpu-4060 配置要求 x86_64 Ubuntu，当前架构：$(uname -m)。" >&2
        exit 1
    fi
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        echo "未找到 nvidia-smi。请先安装 NVIDIA 驱动并重启，再安装 GPU 依赖。" >&2
        exit 1
    fi
    echo "NVIDIA 驱动预检："
    nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
fi

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

install_system_dependencies() {
    run_as_root apt-get update
    if [[ "${VERSION_ID}" == "24.04" && "${force_source_build}" == false ]]; then
        echo "Ubuntu 24.04：安装系统 Python 3.12，不执行源码编译。"
        run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
            ca-certificates \
            curl \
            libglib2.0-0 \
            libgl1 \
            python3.12 \
            python3.12-dev \
            python3.12-venv
    else
        echo "Ubuntu ${VERSION_ID}：准备 CPython ${python_version} 源码构建依赖。"
        run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
            build-essential \
            ca-certificates \
            curl \
            libbz2-dev \
            libexpat1-dev \
            libffi-dev \
            libgdbm-compat-dev \
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
    fi
}

build_project_python() (
    tmp_base="$(realpath -m "${TMPDIR:-/tmp}")"
    available_kb="$(df -Pk "${tmp_base}" | awk 'NR == 2 {print $4}')"
    if [[ ! "${available_kb}" =~ ^[0-9]+$ ]] || ((available_kb < 1572864)); then
        echo "编译 Python 的临时目录至少需要约 1.5 GiB 空间：${tmp_base}" >&2
        echo "当前可用：${available_kb:-unknown} KiB；可用 TMPDIR 指定其他磁盘。" >&2
        exit 1
    fi

    mkdir -p -- "$(dirname -- "${python_prefix}")"
    build_root="$(mktemp -d "${tmp_base}/go2-python-build.XXXXXX")"

    cleanup() {
        case "${build_root}" in
            "${tmp_base}"/go2-python-build.*)
                rm -rf -- "${build_root}"
                ;;
        esac
    }
    trap cleanup EXIT

    cd -- "${build_root}"

    download_urls=()
    if [[ -n "${GO2_PYTHON_SOURCE_URL:-}" ]]; then
        download_urls+=("${GO2_PYTHON_SOURCE_URL}")
    else
        download_urls+=(
            "${official_python_url}"
            "${huawei_python_url}"
            "${npmmirror_python_url}"
        )
    fi

    downloaded=false
    for candidate_url in "${download_urls[@]}"; do
        echo "下载 CPython ${python_version}：${candidate_url}"
        rm -f -- "${python_archive}"
        if curl \
            --fail \
            --location \
            --retry 5 \
            --retry-all-errors \
            --retry-delay 2 \
            --connect-timeout 20 \
            --max-time 600 \
            --output "${python_archive}" \
            "${candidate_url}"
        then
            if printf '%s  %s\n' "${python_sha256}" "${python_archive}" | sha256sum --check -; then
                downloaded=true
                break
            fi
            echo "校验失败，尝试下一个镜像：${candidate_url}" >&2
        else
            echo "下载失败，尝试下一个镜像：${candidate_url}" >&2
        fi
    done
    if [[ "${downloaded}" != true ]]; then
        echo "无法下载 CPython ${python_version} 源码包；可设置 GO2_PYTHON_SOURCE_URL 指定镜像。" >&2
        exit 1
    fi
    tar --extract --file "${python_archive}"
    cd -- "Python-${python_version}"
    ./configure \
        --prefix="${python_prefix}" \
        --with-ensurepip=install
    build_jobs="${GO2_BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '2')}"
    if [[ ! "${build_jobs}" =~ ^[1-9][0-9]*$ ]]; then
        echo "GO2_BUILD_JOBS 必须是正整数，当前值：${build_jobs}" >&2
        exit 2
    fi
    if [[ -z "${GO2_BUILD_JOBS:-}" ]] && ((build_jobs > 4)); then
        build_jobs=4
    fi
    echo "使用 ${build_jobs} 个并行任务编译；可通过 GO2_BUILD_JOBS 调整。"
    make -j"${build_jobs}"
    make install
)

is_compatible_python() {
    "$1" -c \
        'import bz2, ctypes, decimal, ensurepip, lzma, readline, sqlite3, ssl, sys, uuid, venv, zlib; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)'
}

report_python_problem() {
    "$1" - <<'PY' || true
import importlib
import sys

print(f"解释器：{sys.executable}；版本：{sys.version.split()[0]}")
for name in ("bz2", "ctypes", "decimal", "ensurepip", "lzma", "readline", "sqlite3", "ssl", "uuid", "venv", "zlib"):
    try:
        importlib.import_module(name)
    except Exception as exc:
        print(f"  模块 {name} 不可用：{exc}")
PY
}

if [[ "${install_system_deps}" == true ]]; then
    echo "[1/5] 准备 Ubuntu 系统依赖"
    install_system_dependencies
else
    echo "[1/5] 已按参数跳过 Ubuntu 系统依赖"
fi

echo "[2/5] 选择 Python 3.12 解释器"
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
    report_python_problem "${python_exe}"
    exit 1
fi
echo "使用：${python_exe}（$("${python_exe}" --version 2>&1)）"

if [[ "${recreate_venv}" == true && ( -e "${venv_root}" || -L "${venv_root}" ) ]]; then
    venv_backup="${venv_root}.backup.$(date +%Y%m%d-%H%M%S)"
    if [[ -e "${venv_backup}" || -L "${venv_backup}" ]]; then
        venv_backup="${venv_backup}.$$"
    fi
    echo "备份现有虚拟环境：${venv_backup}"
    mv -- "${venv_root}" "${venv_backup}"
fi

if [[ -d "${venv_root}" && ! -x "${venv_root}/bin/python" ]]; then
    echo "Existing ${venv_root} is not a Linux virtual environment." >&2
    echo "请重新执行并添加 --recreate-venv；旧目录会保留为带时间戳的备份。" >&2
    exit 1
fi
if [[ ( -e "${venv_root}" || -L "${venv_root}" ) && ! -d "${venv_root}" ]]; then
    echo "${venv_root} 存在，但它不是目录。请移走该文件后重试。" >&2
    exit 1
fi

echo "[3/5] 创建或复用项目虚拟环境"
if [[ ! -x "${venv_root}/bin/python" ]]; then
    "${python_exe}" -m venv "${venv_root}"
fi

venv_python="${venv_root}/bin/python"
if ! is_compatible_python "${venv_python}"; then
    echo "Existing virtual environment does not use Python 3.12: ${venv_root}" >&2
    report_python_problem "${venv_python}"
    echo "请重新执行并添加 --recreate-venv。" >&2
    exit 1
fi

echo "[4/5] 安装 ${profile} Python 依赖"
pip_index_url="${GO2_PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
pip_trusted_host="$(
    "${venv_python}" - <<'PY' "${pip_index_url}"
from urllib.parse import urlparse
import sys
print(urlparse(sys.argv[1]).hostname or "")
PY
)"
pip_args=(--upgrade pip setuptools wheel -i "${pip_index_url}")
req_args=(--requirement "${requirements_file}" -i "${pip_index_url}")
if [[ -n "${pip_trusted_host}" ]]; then
    pip_args+=(--trusted-host "${pip_trusted_host}")
    req_args+=(--trusted-host "${pip_trusted_host}")
fi
echo "使用 PyPI 索引：${pip_index_url}"
"${venv_python}" -m pip install "${pip_args[@]}"
"${venv_python}" -m pip install "${req_args[@]}"

echo "[5/5] 验证应用导入与运行环境"
cd -- "${project_root}"
"${venv_python}" -m compileall -q app scripts
"${venv_python}" -c \
    'import cv2, docx, fastapi, numpy, PIL, uvicorn; from app.api import app; assert app.title'

if [[ "${profile}" == "gpu-4060" ]]; then
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        echo "nvidia-smi was not found. Install a compatible NVIDIA driver first." >&2
        exit 1
    fi
    nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
    "${venv_python}" -c \
        'import torch, ultralytics
if not torch.cuda.is_available():
    raise SystemExit("PyTorch CUDA is unavailable; check the NVIDIA driver and virtual environment")
name = torch.cuda.get_device_name(0)
if "RTX 4060" not in name:
    raise SystemExit(f"Expected an RTX 4060 GPU, detected: {name}")
print(f"GPU ready: {name}; torch={torch.__version__}; CUDA={torch.version.cuda}")'
fi

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
