# 树莓派传感器采集端部署与使用手册

本目录是运行在树莓派上的采集程序，负责读取：

- MLX90640-D55 32×24 红外热像仪（I2C，默认地址 `0x33`）；
- 4MZ-HH4 四合一气体模组（UART/Modbus RTU，9600 8N1）；
- 普通 USB UVC 彩色相机（V4L2，默认 `/dev/video0`）。

程序把数据写入本机 `data/`，巡检完成后再通过 U 盘交给 `go2_inspection` 上位机处理。树莓派端不运行 YOLO 训练、CUDA 推理或上位机 Web 服务。

## 1. 推荐环境

推荐组合：树莓派 4B/5、64 位 Raspberry Pi OS Bookworm 或 Ubuntu 22.04/24.04、Python 3.9 及以上、至少 4 GiB 可用磁盘空间。

| 项目 | 支持情况 |
| --- | --- |
| Raspberry Pi OS Bookworm 64 位 | 推荐，系统 Python 通常为 3.11 |
| Ubuntu Server 22.04/24.04 ARM64 | 支持 |
| 32 位 Raspberry Pi OS | 代码支持，但 OpenCV 和图像处理速度较慢 |
| Python 3.8 及以下 | 不支持 |
| Windows 虚拟环境 `.venv` | 不能在 Linux 复用；安装脚本会创建 Linux 环境 |

ARM 树莓派通过 `apt` 安装 `python3-opencv`，避免 `pip` 在设备上长时间编译 OpenCV。其余传感器驱动装入项目虚拟环境。

## 2. 硬件接线

接线前关闭树莓派电源。不要带电插拔传感器；下表的“引脚”均为树莓派 40 针排针物理编号。

### 2.1 MLX90640

| 树莓派物理引脚 | BCM/功能 | MLX90640 |
| --- | --- | --- |
| 2 或 4 | 5V | VCC |
| 6 | GND | GND |
| 3 | BCM2 / SDA | SDA |
| 5 | BCM3 / SCL | SCL |

正常情况下，`i2cdetect -y 1` 应显示地址 `33`。

### 2.2 4MZ-HH4

| 树莓派物理引脚 | BCM/功能 | 4MZ-HH4 |
| --- | --- | --- |
| 2 或 4 | 5V | `+` |
| 6 | GND | `-` |
| 8 | BCM14 / TXD | `R`（模块接收） |
| 10 | BCM15 / RXD | `T`（模块发送） |

树莓派 TX 接模块 RX，树莓派 RX 接模块 TX，二者必须共地。默认串口为 `/dev/serial0`；默认 Modbus 地址为 CH4=`0x01`、O2=`0x02`、CO=`0x03`、H2S=`0x04`。

### 2.3 USB 相机

相机直接连接树莓派 USB 接口。建议先用有供电能力的 USB 口；多设备同时使用时要保证电源稳定。默认设备是 `/dev/video0`。

## 3. 复制代码

在 Windows PowerShell 中，只复制树莓派采集端即可：

```powershell
scp -r ".\raspberry-pi-sensor-logger" pi@raspberrypi.local:~/sensor-reader
```

也可以使用树莓派 IP：

```powershell
scp -r ".\raspberry-pi-sensor-logger" pi@192.168.1.50:~/sensor-reader
```

不要把 `go2_inspection` 当作树莓派采集程序。它面向带 NVIDIA GPU 的上位机。也不应复制 Windows 的 `.venv` 作为树莓派运行环境；不同操作系统和 CPU 架构的虚拟环境不能通用。

## 4. 一键安装

SSH 登录树莓派后执行：

```bash
cd ~/sensor-reader
chmod +x scripts/*.sh
./scripts/setup_raspberry_pi.sh
sudo reboot
```

旧命令 `./scripts/setup_ubuntu.sh` 仍可使用，它会转到同一个安装程序。

安装程序会完成以下工作：

1. 确认当前设备和 Linux 发行版；
2. 安装 Python、I2C、V4L2、Pillow 和 ARM 版 OpenCV；
3. 在树莓派启动配置中启用 I2C 400 kHz 与 UART；
4. 删除内核命令行中对 GPIO 串口的登录控制台占用；
5. 把当前用户加入 `dialout`、`video` 和存在时的 `i2c` 组；
6. 在项目内创建 `.venv` 并安装采集程序；
7. 验证所有 Python 模块能够导入。

脚本不会自动重启。必须手动重启一次，用户组和启动配置才会生效。

## 5. 重启后的诊断

先运行无写入的诊断脚本：

```bash
cd ~/sensor-reader
./scripts/diagnose_hardware.sh
```

它会检查系统与 CPU 架构、Python 模块、用户组、`/dev/i2c-1`、I2C 地址 `0x33`、`/dev/serial0`、`/dev/video0` 和剩余磁盘空间。全部显示“通过”后，再做一次真实采集：

```bash
.venv/bin/python -m sensor_logger --once --require-all-hardware
```

命令应在 `data/gas`、`data/thermal`、`data/visible` 各生成数据，并以状态码 0 退出。若任一设备失败，它会以状态码 1 退出，终端和 `logs/sensor_logger.log` 会给出原因。

如果相机不是 `/dev/video0`：

```bash
v4l2-ctl --list-devices
SENSOR_LOGGER_CAMERA_DEVICE=/dev/video2 ./scripts/diagnose_hardware.sh
.venv/bin/python -m sensor_logger --once --camera-device /dev/video2 --require-all-hardware
```

## 6. 连续采集

前台运行（Ctrl+C 安全停止）：

```bash
cd ~/sensor-reader
.venv/bin/python -m sensor_logger --interval 5 --camera-interval 2
```

气体或热像初始化失败时，默认只把对应通道记为错误，其他可用设备会继续采集；USB 相机断开后也会在后续周期重试。现场验收时可加 `--require-all-hardware`，要求三类硬件在启动时全部可用。

常用参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--interval` | `2` 秒 | 气体与热像采样周期；正式服务设为 5 秒 |
| `--camera-interval` | `2` 秒 | USB 相机拍照周期 |
| `--serial-port` | `/dev/serial0` | 气体模组串口 |
| `--camera-device` | `0` | OpenCV 编号，也可写 `/dev/video0` |
| `--camera-width` | `1280` | 请求宽度 |
| `--camera-height` | `720` | 请求高度 |
| `--camera-fps` | `30` | 请求帧率 |
| `--camera-quality` | `95` | JPEG 质量，1～100 |
| `--data-dir` | `data` | 数据目录，可使用绝对路径 |
| `--once` | 关闭 | 采集一次后退出 |
| `--require-all-hardware` | 关闭 | 任一串口/I2C 初始化失败即退出 |
| `--log-level` | `INFO` | `DEBUG`、`INFO`、`WARNING` 或 `ERROR` |

查看完整帮助：

```bash
.venv/bin/python -m sensor_logger --help
```

## 7. 开机自动运行

安装为 systemd 服务，不再要求固定用户名或固定项目路径：

```bash
cd ~/sensor-reader
./scripts/install_service.sh
```

如旧流程仍需传工位号，可使用 `./scripts/install_service.sh 08`；当前单图文件格式不依赖工位号。

常用管理命令：

```bash
sudo systemctl status sensor-logger.service
journalctl -u sensor-logger.service -f
sudo systemctl restart sensor-logger.service
sudo systemctl stop sensor-logger.service
sudo systemctl disable sensor-logger.service
```

服务使用安装脚本的当前用户与当前项目绝对路径，默认气体/热像每 5 秒、可见光每 2 秒采集。移动项目目录后应重新运行 `./scripts/install_service.sh`。

## 8. 数据与日志

```text
sensor-reader/
├── data/
│   ├── gas/
│   │   └── gas_2026-08-06.csv
│   ├── thermal/
│   │   └── thermal_20260806_153005_000001.png
│   └── visible/
│       └── color_20260806_153005_123456.jpg
└── logs/
    ├── sensor_logger.log
    ├── sensor_logger.log.1
    └── ...
```

气体 CSV 固定为 `时间、编号、CH4(%LEL)、O2(%VOL)、CO(ppm)、H2S(ppm)、状态` 七列。热像 PNG 含 `thermal_stats_v1` 文本元数据（时间、编号、尺寸和最低/最高/平均温度）。照片和热像都先写临时文件，再原子发布，避免上位机读取半个文件。

20 分钟内，可见光按 2 秒一次约产生 600 张图片。程序不会自动删除历史数据，因此每次巡检前应检查磁盘容量。

## 9. 导出到 U 盘

插入 U 盘并确认挂载点：

```bash
lsblk -f
findmnt
```

桌面系统常见挂载点为 `/media/$USER/USB_NAME`，执行：

```bash
./scripts/export_to_usb.sh "/media/$USER/USB_NAME"
```

脚本只替换 U 盘根目录下的 `gas`、`thermal`、`visible` 三个目录，不删除 U 盘其他文件，也不删除树莓派原始数据。出现“导出完成”后再安全卸载，设备名以 `lsblk` 为准：

```bash
udisksctl unmount -b /dev/sda1
```

## 10. 常见故障

### `unknown encoding: utf‑8`

旧版本入口把编码名称中的减号误写成 Unicode 字符。本版本已改为标准 `utf-8`。确认复制的是更新后的 `src/sensor_logger/cli.py`，再运行安装命令更新可编辑安装。

### `externally-managed-environment`

说明命令错误地调用了系统 `pip`。不要执行 `sudo pip install`，使用安装脚本，或明确运行 `.venv/bin/python -m pip ...`。

### `No matching distribution found` 或 OpenCV 编译很久

确认使用了 `./scripts/setup_raspberry_pi.sh`。它在 ARM 上安装系统 `python3-opencv`，不会从 pip 编译 OpenCV。用 `uname -m` 检查架构，并用 `.venv/bin/python -c "import cv2; print(cv2.__version__)"` 验证。

### 找不到 I2C 地址 `0x33`

执行 `i2cdetect -y 1`。若没有 `33`，关机后检查 VCC、GND、SDA、SCL，确认没有把 SDA/SCL 接反。若 `/dev/i2c-1` 不存在，确认安装后已重启。

### `/dev/serial0` 不存在或权限不足

执行：

```bash
ls -l /dev/serial0
groups
grep -E 'enable_uart|i2c_arm' /boot/firmware/config.txt /boot/config.txt 2>/dev/null
```

应能看到 `dialout` 用户组。若刚运行安装脚本，重启或重新登录。确认模块 TX/RX 交叉连接并共地。

### 气体全部显示 `read_error` 或超时

先确认串口为 9600、8N1；再确认四个 Modbus 地址与程序一致。关闭占用串口的其他程序，可用 `sudo lsof /dev/serial0` 检查。树莓派 TX 必须接模块 `R`，RX 必须接模块 `T`。

### 找不到或打不开 `/dev/video0`

执行 `v4l2-ctl --list-devices` 查真实编号，并检查 `groups` 是否包含 `video`。用 `sudo lsof /dev/video0` 排查占用。供电不足时 USB 相机也可能反复掉线，可运行 `dmesg -T | tail -n 100` 查看 USB 错误。

### 服务反复重启

执行：

```bash
sudo systemctl status sensor-logger.service --no-pager -l
journalctl -u sensor-logger.service -n 100 --no-pager
cat /etc/systemd/system/sensor-logger.service
```

若移动过项目或更换过登录用户，在新目录下重新运行 `./scripts/install_service.sh`。

### 磁盘写满

执行 `df -h` 和 `du -sh data/*`。先停止服务并把完整数据导出、核验后，再由操作人员决定是否清理旧批次。采集程序不会自动删数据。

## 11. 更新代码后的操作

覆盖代码后不必重新配置 I2C/UART，但应同步 Python 依赖并重启服务：

```bash
cd ~/sensor-reader
.venv/bin/python -m pip install --editable .
.venv/bin/python -m pip install --requirement requirements-dev.txt
.venv/bin/python -m pytest -q
sudo systemctl restart sensor-logger.service
```

## 12. 安全说明

本程序用于实验和比赛采集，不是经过认证的瓦斯报警、生命安全或工业联锁装置。进入爆炸性或煤矿作业环境时，树莓派、相机、U 盘、传感器、电源和外壳必须满足现场防爆、本安、供电与作业规范；不得用本程序替代法定检测和保护设备。
