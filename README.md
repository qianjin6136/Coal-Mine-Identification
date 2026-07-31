# GO2 煤矿实验室视觉巡检上位机

本教程说明如何从 GitHub 下载项目，在 Windows、PyCharm 或 Ubuntu 上完成安装，并启动
GO2 视觉巡检服务。

项目默认使用 `noop` 检测后端，因此即使没有 YOLO 权重，也可以先完成安装、启动、
图片上传、数据库写入和结果页面验证。需要真实目标检测时，再安装完整视觉依赖并配置
模型权重。

## 1. 项目目录

下载后的主要目录如下：

```text
Coal-Mine-Identification/
├── code/
│   └── go2_inspection/       # Python 源码、配置、脚本和文档
├── runtime_data/             # 数据库、上传图片、结果和运行参数
└── README.md
```

后续安装和启动命令都需要在 `code/go2_inspection` 目录中执行。

## 2. 安装前准备

### Windows

- Windows 10 或 Windows 11；
- Git；
- 64 位 Python 3.12；
- 可选：PyCharm；
- 使用 NVIDIA GPU 推理时，需要兼容的 NVIDIA 驱动。

项目要求 Python `3.12.x`，不支持直接使用 Python 3.11、3.13 或其他版本。安装后执行：

```powershell
py -3.12 --version
```

若能看到 `Python 3.12.x`，即可继续。

### Ubuntu

自动安装脚本支持：

- Ubuntu 20.04；
- Ubuntu 22.04。

如果系统没有可用的 Python 3.12，脚本会下载并编译项目专用的 CPython 3.12.13。

## 3. 从 GitHub 下载

### 方法一：使用 Git 克隆（推荐）

打开 PowerShell、Windows Terminal 或 Linux 终端：

```bash
git clone https://github.com/qianjin6136/Coal-Mine-Identification.git
cd Coal-Mine-Identification
```

以后更新代码时，可在仓库根目录运行：

```bash
git pull
```

### 方法二：下载 ZIP

1. 打开 [项目 GitHub 页面](https://github.com/qianjin6136/Coal-Mine-Identification)；
2. 点击 `Code`；
3. 点击 `Download ZIP`；
4. 下载完成后解压；
5. 进入解压后的 `Coal-Mine-Identification` 目录。

ZIP 方式适合只运行项目、不需要使用 Git 更新代码的用户。

## 4. Windows 安装

打开 PowerShell，进入源码目录：

```powershell
cd "Coal-Mine-Identification\code\go2_inspection"
```

### 4.1 创建虚拟环境

```powershell
py -3.12 -m venv .venv
```

激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 提示禁止运行脚本，先在当前窗口临时放行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

升级安装工具：

```powershell
python -m pip install --upgrade pip setuptools wheel
```

### 4.2 安装依赖

首次运行建议安装精简运行依赖：

```powershell
python -m pip install -r requirements-runtime.txt
```

不同依赖文件的用途：

| 使用场景 | 安装命令 |
| --- | --- |
| API、工作台、编号牌和仪表模块 | `python -m pip install -r requirements-runtime.txt` |
| Ultralytics/YOLO 完整视觉环境 | `python -m pip install -r requirements.txt` |
| Windows NVIDIA GPU 环境 | `python -m pip install -r requirements-gpu-windows.txt` |
| 开发和运行测试 | `python -m pip install -r requirements-dev.txt` |

不使用 YOLO 时不需要安装 GPU 依赖。

### 4.3 验证安装

```powershell
python -c "import cv2, fastapi, numpy, PIL, uvicorn; from app.api import app; print(app.title)"
```

命令正常输出应用名称且没有异常，即表示基础依赖安装成功。

## 5. 在 PyCharm 中安装

1. 在 PyCharm 中打开整个 `Coal-Mine-Identification` 文件夹；
2. 打开 `File → Settings → Project → Python Interpreter`；
3. 添加现有解释器；
4. 选择：

   ```text
   code\go2_inspection\.venv\Scripts\python.exe
   ```

5. 打开 PyCharm 底部的 `Terminal`；
6. 进入项目源码目录并执行安装：

   ```powershell
   cd code\go2_inspection
   python -m pip install -r requirements-runtime.txt
   ```

如果 PyCharm 已经自动进入 `code/go2_inspection`，不需要重复执行 `cd`。

## 6. Ubuntu 20.04/22.04 安装

安装 Git 并克隆项目：

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/qianjin6136/Coal-Mine-Identification.git
cd Coal-Mine-Identification/code/go2_inspection
```

执行自动安装：

```bash
bash scripts/install_ubuntu.sh --profile runtime
```

可用安装配置：

| 配置 | 说明 |
| --- | --- |
| `runtime` | 安装 API 和仪表运行依赖，推荐首次安装使用 |
| `full` | 额外安装 Ultralytics/YOLO 依赖 |
| `dev` | 安装运行依赖和测试工具，并在安装后运行测试 |

例如安装完整视觉环境：

```bash
bash scripts/install_ubuntu.sh --profile full
```

安装器会创建 `.venv`。如果系统没有 Python 3.12，还会在项目内创建 `.python` 并编译
CPython，因此首次安装可能需要较长时间。

## 7. 启动服务

### Windows

在 `code/go2_inspection` 目录中运行：

```powershell
.\scripts\start_server.ps1
```

### Ubuntu

```bash
bash scripts/start_server.sh
```

启动后打开：

- 健康检查：<http://127.0.0.1:8000/health>
- 图片上传与结果工作台：<http://127.0.0.1:8000/ui>
- Swagger API 文档：<http://127.0.0.1:8000/docs>

按 `Ctrl+C` 停止服务。

如需修改端口，Windows PowerShell 使用：

```powershell
$env:GO2_PORT = "8001"
.\scripts\start_server.ps1
```

Ubuntu 使用：

```bash
GO2_PORT=8001 bash scripts/start_server.sh
```

## 8. 运行数据位置

默认运行数据位于仓库根目录的 `runtime_data`：

```text
runtime_data/
├── database/inspection.db
├── original/
├── annotated/
└── runtime_settings.json
```

上传图片、数据库和运行参数与源码分开保存。升级源码前建议备份该目录。

## 9. 配置识别模型

默认配置文件为：

```text
code/go2_inspection/configs/app.json
```

默认 `noop` 后端不会输出目标，但可以验证服务、上传、存储和页面流程：

```json
{
  "detector": {
    "backend": "noop",
    "weights": null,
    "confidence": 0.35
  }
}
```

使用训练好的 Ultralytics/YOLO 权重时：

1. 安装 `requirements.txt` 或 `requirements-gpu-windows.txt`；
2. 将权重放入 `code/go2_inspection/models/best.pt`；
3. 修改 `configs/app.json`：

   ```json
   {
     "detector": {
       "backend": "ultralytics",
       "weights": "models/best.pt",
       "confidence": 0.35
     }
   }
   ```

4. 重新启动服务。

## 10. 运行测试

在 `code/go2_inspection` 目录执行：

```powershell
python -m unittest discover -s tests -v
```

Ubuntu 使用相同命令：

```bash
python -m unittest discover -s tests -v
```

如果当前终端没有激活虚拟环境，可分别使用 `.venv\Scripts\python.exe` 或
`.venv/bin/python`。

## 11. 常见问题

### GitHub 下载提示 443 连接失败

如果使用 Clash Verge，先确认系统代理已开启，并查看软件显示的混合代理端口。然后在
终端中配置 Git，下面的 `7897` 需要替换为你的实际端口：

```powershell
git config --global http.proxy http://127.0.0.1:7897
```

如果同时出现 `SEC_E_NO_CREDENTIALS`，可让 Git for Windows 使用其自带 OpenSSL：

```powershell
git config --global http.sslBackend openssl
```

不再使用代理时取消配置：

```powershell
git config --global --unset http.proxy
```

### 提示 Python 版本不正确

确认使用的是 Python 3.12：

```powershell
py -3.12 --version
```

删除错误版本创建的 `.venv` 后，再用 Python 3.12 重新创建。删除前确认虚拟环境中没有
需要保留的个人文件。

### 端口 8000 已被占用

改用其他端口：

```powershell
$env:GO2_PORT = "8001"
.\scripts\start_server.ps1
```

### 上传成功但没有识别目标

这是默认 `noop` 检测器的预期行为。需要安装完整视觉依赖、放置模型权重，并把
`configs/app.json` 的检测后端修改为 `ultralytics`。

### 局域网中的其他设备无法访问

启动脚本默认监听 `0.0.0.0`。请确认 Windows 防火墙或 Ubuntu 防火墙允许所用端口，
然后使用运行服务电脑的局域网 IP 访问，例如：

```text
http://192.168.1.100:8000/ui
```

## 12. 更多文档

- [源码运行与工作台使用说明](code/go2_inspection/docs/源码运行与工作台使用说明.md)
- [完整部署与安装指南](code/go2_inspection/docs/部署与安装指南.md)
- [Ubuntu 20.04/22.04 部署指南](code/go2_inspection/docs/Ubuntu20.04与22.04部署指南.md)
- [标注规范](code/go2_inspection/docs/标注规范.md)
