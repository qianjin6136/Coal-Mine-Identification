# Ubuntu 20.04 / 22.04 / 24.04 上位机部署指南

## 1. 兼容范围

正式上位机目标为 Ubuntu 22.04、CPython 3.12 和 RTX 4060；Ubuntu 20.04 仅保留
现有部署的兼容安装能力：

| 系统 | Python | 安装方式 |
| --- | --- | --- |
| Ubuntu 20.04 LTS | 3.12 | 系统已有时复用，否则从官方源码构建到项目目录 |
| Ubuntu 22.04 LTS | 3.12 | 系统已有时复用，否则从官方源码构建到项目目录 |
| Ubuntu 24.04 LTS | 3.12 | 通过 Ubuntu 官方包安装并直接创建项目虚拟环境 |

Ubuntu 22.04 的默认 Python 是 3.10，Ubuntu 24.04 的默认 Python 是 3.12，不能
对两个版本使用相同安装分支。安装脚本不会替换系统 Python：20.04/22.04 需要源码
构建时，结果位于 `.python/cpython-3.12.13/`；24.04 复用系统 Python 3.12；所有
应用依赖均隔离在 `.venv/`。

Ubuntu 20.04 已进入扩展安全维护阶段。生产机器应启用 Ubuntu Pro/ESM，
或制定升级到仍处于标准支持期的 LTS 版本的计划。

## 2. 准备目录

建议保留当前目录层级，源码、模型和运行数据都位于同一个上位机项目中：

```text
/opt/coal-mine-identification/
  go2_inspection/
    app/
    configs/
    runtime_data/
      models/
        station_number_templates.npz
        digital_meter_templates.npz
```

将项目复制到 Ubuntu 后进入源码目录：

```bash
cd /opt/coal-mine-identification/go2_inspection
```

安装脚本必须由普通登录用户运行，并要求该用户可写项目目录。若项目是通过 `sudo`
复制到 `/opt`、当前用户没有写权限，先执行一次（路径按实际情况修改）：

```bash
sudo chown -R "$(id -un):$(id -gn)" /opt/coal-mine-identification/go2_inspection
```

不要使用 `sudo bash scripts/install_ubuntu.sh`，否则生成的 `.python`、`.venv` 和运行
数据会归 root 所有。

脚本可通过 `bash` 直接执行，不依赖文件的可执行位：

```bash
bash scripts/install_ubuntu.sh --help
```

## 3. 一键安装

仅运行 API、编号牌和数字表模块时，安装精简运行环境：

```bash
bash scripts/install_ubuntu.sh --profile runtime
```

RTX 4060 正式上位机安装完整 GPU 环境：

```bash
bash scripts/install_ubuntu.sh --profile gpu-4060
```

安装脚本将执行：

1. 确认系统是 Ubuntu 20.04、22.04 或 24.04，并检查项目目录写权限；
2. 在 GPU 配置下先检查 x86_64、`nvidia-smi` 和驱动；
3. Ubuntu 24.04 从官方仓库安装并使用 Python 3.12；
4. Ubuntu 20.04/22.04 优先复用已有的完整 `python3.12`，否则下载经过 SHA-256
   校验的 CPython 3.12.13 官方源码，编译到项目 `.python/` 目录；
5. 创建 `.venv` 并安装项目依赖；
6. 对 `app/` 和 `scripts/` 执行字节码编译检查；
7. 导入 OpenCV、python-docx、FastAPI、NumPy、Pillow、Uvicorn 并创建应用实例做冒烟检查；
8. `gpu-4060` 配置额外验证 `nvidia-smi`、Ultralytics、PyTorch CUDA 和显卡名称。

安装系统包需要 `root` 或 `sudo`。已经提前装好系统依赖时可跳过 `apt-get`：

```bash
bash scripts/install_ubuntu.sh --profile runtime --skip-system-deps
```

即使系统已有 Python 3.12，也可以强制构建项目专用的 3.12.13：

```bash
bash scripts/install_ubuntu.sh --profile runtime --build-python
```

已有 `.venv` 如果来自 Windows 或不是 Python 3.12，脚本会停止并提示处理，不会自动
删除或覆盖环境。使用下面的参数可以把它移动到 `.venv.backup.时间戳` 后安全重建：

```bash
bash scripts/install_ubuntu.sh --profile runtime --recreate-venv
```

源码编译默认最多使用 4 个并行任务，内存较小时可主动降低：

```bash
GO2_BUILD_JOBS=2 bash scripts/install_ubuntu.sh --profile runtime
```

网络环境无法访问 Python 官方下载地址时，可指定同一文件的可信镜像；脚本仍使用
内置 SHA-256 强制校验内容：

```bash
GO2_PYTHON_SOURCE_URL="https://可信镜像/Python-3.12.13.tar.xz" \
  bash scripts/install_ubuntu.sh --profile runtime
```

## 4. 依赖配置

四种安装配置如下：

| 配置 | 命令 | 用途 |
| --- | --- | --- |
| `runtime` | `bash scripts/install_ubuntu.sh --profile runtime` | API、存储、编号牌、数字表 |
| `full` | `bash scripts/install_ubuntu.sh --profile full` | 增加 Ultralytics/YOLO |
| `gpu-4060` | `bash scripts/install_ubuntu.sh --profile gpu-4060` | RTX 4060、Ultralytics、PyTorch 2.12.1 CUDA 12.6，并执行 GPU 验收 |
| `dev` | `bash scripts/install_ubuntu.sh --profile dev` | 增加测试依赖并自动运行单元测试 |

默认配置中的检测器是 `noop`，所以正常运行服务不需要 PyTorch 或 NVIDIA GPU。
RTX 4060 使用项目的 `requirements-gpu-ubuntu4060.txt`，不要复用 Windows GPU
依赖。版本依据为 [PyTorch 官方 CUDA 12.6 安装矩阵](https://pytorch.org/get-started/previous-versions/)；
Ubuntu 22.04 的驱动和 CUDA 平台支持见
[NVIDIA CUDA Linux 安装指南](https://docs.nvidia.com/cuda/cuda-installation-guide-linux/index.html)。
安装前先保证 `nvidia-smi` 可以正常显示设备，再把 `configs/app.json` 中的检测器改为
`ultralytics`。

## 5. 编译和测试

安装后激活环境：

```bash
source .venv/bin/activate
python --version
```

输出必须是 `Python 3.12.x`。

执行源码编译检查和单元测试：

```bash
python -m compileall -q app scripts
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

检查 Python 包是否可以构建：

```bash
mkdir -p /tmp/go2-wheel
python -m pip wheel --no-deps --wheel-dir /tmp/go2-wheel .
```

## 6. 启动与验证

启动服务：

```bash
bash scripts/start_server.sh
```

脚本按以下顺序寻找解释器：

1. 项目 `.venv/bin/python`；
2. 项目本地 `.python/cpython-3.12.13/bin/python3.12`；
3. 系统 `python3.12`。

发现的解释器不是 Python 3.12 时，脚本会拒绝启动。默认监听
`0.0.0.0:8000`，可通过环境变量修改：

```bash
GO2_HOST=127.0.0.1 GO2_PORT=8001 bash scripts/start_server.sh
```

另开终端验证：

```bash
curl --fail http://127.0.0.1:8000/health
```

浏览器入口：

- 工作台：`http://127.0.0.1:8000/ui`
- Swagger：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

## 7. 配置 systemd

以下示例假设项目位于 `/opt/coal-mine-identification/go2_inspection`，服务以专用
`go2` 用户运行。先确保该用户可读源码，并可写
`/opt/coal-mine-identification/go2_inspection/runtime_data`。

创建 `/etc/systemd/system/go2-inspection.service`：

```ini
[Unit]
Description=GO2 visual inspection service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=go2
Group=go2
WorkingDirectory=/opt/coal-mine-identification/go2_inspection
Environment=LANG=C.UTF-8
Environment=YOLO_CONFIG_DIR=/opt/coal-mine-identification/go2_inspection/runtime_data
ExecStart=/opt/coal-mine-identification/go2_inspection/.venv/bin/python -m uvicorn app.api:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

加载并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now go2-inspection
systemctl status go2-inspection --no-pager
journalctl -u go2-inspection -n 100 --no-pager
```

如果使用不同目录或用户，必须同步修改 `User`、`Group`、`WorkingDirectory`、
`YOLO_CONFIG_DIR` 和 `ExecStart`。

## 8. 防火墙和权限

仅本机使用时，把 `GO2_HOST` 设置为 `127.0.0.1`。GO2 需要通过局域网访问时，
只允许可信网段访问 8000 端口，例如：

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp
```

运行账户至少需要：

- 读取 `app/`、`configs/` 和模型文件；
- 写入 `runtime_data/database/`、`incoming/`、`processed/`、`evidence/`；
- 写入 `runtime_data/runtime_settings.json` 和 `YOLO_CONFIG_DIR`。

## 9. 常见问题

### 先确认失败发生在哪一步

新安装脚本会打印 `[1/5]` 至 `[5/5]`，并在失败时显示脚本行号和失败命令。建议保留
完整日志：

```bash
cd /opt/coal-mine-identification/go2_inspection
mkdir -p runtime_data/logs
set -o pipefail
bash scripts/install_ubuntu.sh --profile runtime 2>&1 \
  | tee runtime_data/logs/install-runtime.log
```

先用 `runtime` 配置确认 Python 和基础依赖，再执行
`bash scripts/install_ubuntu.sh --profile gpu-4060`，可以把 Python 问题和 CUDA/模型
依赖问题分开。

### Ubuntu 24.04 被提示“不支持”

说明树莓派或上位机上仍是旧版 `install_ubuntu.sh`。新版支持 24.04，并直接安装
`python3.12`、`python3.12-dev`、`python3.12-venv`，不会编译 Python。确认版本：

```bash
. /etc/os-release
echo "$PRETTY_NAME"
grep -E '20.04|22.04|24.04' scripts/install_ubuntu.sh
```

### 在 `.python` 目录报 `Permission denied`

当前用户没有项目写权限，常见原因是用 `sudo cp` 把项目复制到了 `/opt`。不要用 sudo
运行整个安装脚本；按“准备目录”一节修正所有权后重新执行。

### 出现 `$'\r': command not found` 或 `pipefail\r`

这是 Shell 脚本被 Windows 转成 CRLF 换行造成的。仓库已通过 `.gitattributes` 固定
`*.sh` 为 LF；对已经复制到 Ubuntu 的旧文件可执行：

```bash
sed -i 's/\r$//' scripts/*.sh
bash -n scripts/install_ubuntu.sh
```

### 下载 CPython 超时或失败

Ubuntu 20.04/22.04 的源码分支需要访问 Python 官方站点。先检查时间、DNS 和 HTTPS：

```bash
timedatectl status
getent hosts www.python.org
curl -I https://www.python.org/ftp/python/3.12.13/Python-3.12.13.tar.xz
df -h /tmp .
```

源码文件的官方 SHA-256 是
`c08bc65a81971c1dd5783182826503369466c7e67374d1646519adf05207b684`；校验不一致时
脚本必须停止，不要绕过校验。版本与校验值可在
[Python 3.12.13 官方发布页](https://www.python.org/downloads/release/python-31213/)核对。

### 编译 Python 时进程被终止

通常是 `/tmp` 空间或内存不足。脚本会在编译前要求临时目录至少有约 1.5 GiB，并把
默认并行数限制为最多 4。内存较少时使用 `GO2_BUILD_JOBS=2`；`/tmp` 空间不足时可
把临时目录放到其他磁盘：

```bash
mkdir -p "$PWD/runtime_data/tmp"
TMPDIR="$PWD/runtime_data/tmp" GO2_BUILD_JOBS=2 \
  bash scripts/install_ubuntu.sh --profile runtime
```

### 提示 `.venv` 不是 Linux 环境或不是 Python 3.12

不要复用 Windows 的 `.venv`。执行：

```bash
bash scripts/install_ubuntu.sh --profile runtime --recreate-venv
```

旧目录会改名保留；确认新环境正常后再由操作人员决定是否清理备份。

### 系统显示 Python 3.8 或 3.10

不要修改 `/usr/bin/python3` 链接。使用安装脚本创建的 `.venv/bin/python`，
并通过 `bash scripts/start_server.sh` 启动。

### OpenCV 提示缺少 `libGL.so.1`

执行：

```bash
sudo apt-get update
sudo apt-get install -y libgl1 libglib2.0-0
```

### 中文路径或中文输出乱码

确认 UTF-8 locale：

```bash
export LANG=C.UTF-8
```

代码读写 JSON、CSV、配置和页面资源时均显式使用 UTF-8；OpenCV 图片读写通过
字节流接口处理 Unicode 路径。

### 端口被占用

查看占用进程，确认后再处理，或改用其他端口：

```bash
ss -ltnp | grep ':8000'
GO2_PORT=8001 bash scripts/start_server.sh
```
