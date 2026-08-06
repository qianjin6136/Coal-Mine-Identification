# Coal Mine Identification

本项目用于煤矿巡检比赛中的离线数据采集与识别，分为树莓派采集端和上位机识别端。
树莓派在约 20 分钟的巡检过程中只负责采集和保存；比赛结束后通过 U 盘把完整数据复制到上位机，再进行图像识别、结果处理与报告生成。
上位机正式部署目标为 Ubuntu 22.04、Python 3.12 和 NVIDIA GeForce RTX 4060。

> **不要把整个仓库都当作树莓派程序运行。** 树莓派只运行
> `raspberry-pi-sensor-logger`；`go2_inspection` 是上位机程序。树莓派端的完整安装、
> 参数与逐项排错说明见[树莓派采集端部署与使用手册](raspberry-pi-sensor-logger/README.md)。

## 最新赛项范围

本阶段已按最新 18 项比赛清单重新对齐。仓库继续负责可见光、热像、气体采集，U 盘
离线导入、数据库、人工复核、Word 报告和工作台手工测试上传。当前可形成有效结果的
核心链路是：托辊卡死候选（最高温严格大于 65℃）、编号位置关联、LED 数字表、气体
记录和人工确认后生成报告。

皮带异物、堆煤、5 类巡检标牌、红/绿指示灯、3 个水泵仪表和损坏托辊尚缺现场样本
或标定，工作台与报告必须显示“待样本/不可用”，不能用“未检出”代替正常。现有
1～10 编号牌只用于托辊位置关联，不作为第 7 项巡检标牌计分。机器人自主运行、泥泞/
浅水/斜坡通过、夜视遥控与位姿、自动返航、粉尘喷淋和跟随能力不在本仓库实现范围。
完整逐项矩阵见[最新赛项对齐矩阵](go2_inspection/docs/最新赛项对齐矩阵.md)。

## 项目结构

```text
Coal Mine Identification/
├── README.md
├── raspberry-pi-sensor-logger/   # 树莓派气体、红外和 USB 相机采集
└── go2_inspection/               # 上位机识别、训练、结果与样本
    ├── app/
    ├── configs/
    ├── scripts/
    ├── tests/
    ├── docs/
    ├── models/
    ├── runtime_data/
    ├── dataset_inbox/            # U 盘巡检批次导入区
    └── sample/                   # 训练和验证样本
```

数据流：

```text
4MZ-HH4 ─┐
MLX90640 ├─> 树莓派本地 data/ ─> U盘导出 ─> dataset_inbox/ ─> 上位机识别与复核
USB相机 ─┘
```

## 树莓派硬件连接

断电后接线，不要带电插拔。

### MLX90640-D55 红外热像仪

| 树莓派物理引脚 | GPIO/功能 | MLX90640 |
| --- | --- | --- |
| 2 或 4 | 5V | VCC |
| 6 | GND | GND |
| 3 | BCM2 / SDA | SDA |
| 5 | BCM3 / SCL | SCL |

I2C 地址为 `0x33`。

### 4MZ-HH4 四合一气体模块

| 树莓派物理引脚 | GPIO/方向 | 4MZ-HH4 |
| --- | --- | --- |
| 2 或 4 | 5V | `+` |
| 6 | GND | `-` |
| 8 | BCM14 / TXD | `R` |
| 10 | BCM15 / RXD | `T` |

串口使用 9600、8N1，默认设备为 `/dev/serial0`。默认 Modbus 地址为 CH4=`0x01`、O2=`0x02`、CO=`0x03`、H2S=`0x04`。

### USB 可见光相机

把普通 USB UVC 相机直接插入树莓派 USB 接口。程序默认使用设备编号 `0`，在 Linux 上通常对应 `/dev/video0`，采用 V4L2、MJPG、1280×720、30 FPS 和 JPEG 质量 95。

## 树莓派安装与运行

推荐系统为 64 位 Raspberry Pi OS Bookworm，也支持树莓派 Ubuntu 22.04/24.04；
Python 要求已放宽到 3.9 及以上。把 `raspberry-pi-sensor-logger` 复制到树莓派，
不要复用 Windows 或 x86 电脑生成的 `.venv`：

```powershell
scp -r ".\raspberry-pi-sensor-logger" pi@raspberrypi.local:~/sensor-reader
```

登录树莓派后执行：

```bash
cd ~/sensor-reader
chmod +x scripts/*.sh
./scripts/setup_raspberry_pi.sh
sudo reboot
```

其中第一行应按实际用户目录修改，例如 `cd ~/sensor-reader`。安装脚本会自动识别
Raspberry Pi OS/Ubuntu 的启动配置位置、安装 ARM 版 OpenCV、创建 Linux 虚拟环境，
并配置当前用户的串口、I2C 和相机权限。重启后先运行完整诊断：

```bash
./scripts/diagnose_hardware.sh
```

单次硬件验收要求三类设备全部成功；无需提供工位编号：

```bash
.venv/bin/python -m sensor_logger --once --require-all-hardware
```

连续巡检：

```bash
.venv/bin/python -m sensor_logger \
  --interval 5 \
  --camera-interval 2
```

- 气体和红外热像每 5 秒采样一次。
- USB 相机每 2 秒拍一张彩色照片并直接写入 `data/visible`。
- 20 分钟约生成 600 张照片；程序不会自动删除数据。
- 日志写入 `logs/sensor_logger.log` 并自动轮转。
- 任一路硬件暂时不可用时，默认记录该路错误并继续采集其他设备；USB 相机会在后续周期重试。
- 加 `--require-all-hardware` 时，气体串口或热像初始化失败会立即退出，适合安装验收。

安装开机服务时会自动使用当前用户名和当前项目目录，不再绑定固定账户或路径：

```bash
./scripts/install_service.sh
sudo systemctl status sensor-logger.service
journalctl -u sensor-logger.service -f
```

旧流程也可传可选工位号，例如 `./scripts/install_service.sh 08`，但该值不会写入当前
单图数据。

## 树莓派数据格式

```text
data/
├── gas/
│   └── gas_2026-02-11.csv
├── thermal/
│   └── thermal_20260211_073025_000001.png
└── visible/
    └── color_20260211_073025_706675.jpg
```

气体 CSV 使用 `时间、编号、CH4(%LEL)、O2(%VOL)、CO(ppm)、H2S(ppm)、状态`
七列格式。可见光照片先写入临时文件，再原子发布为单张 JPG，因此上位机不会读到半张图片。

## U 盘导出

插入 U 盘并通过 `lsblk -f`、`findmnt` 确认挂载路径，然后执行：

```bash
./scripts/export_to_usb.sh /media/$USER/USB_NAME
```

U 盘根目录中会生成：

```text
U盘根目录/
├── gas/
├── thermal/
└── visible/
```

脚本会先完整复制到 U 盘内的临时目录，再自动替换根目录中已有的 `gas`、`thermal`、
`visible`。U 盘中的其他文件不会被删除，树莓派上的原始数据也会保留。脚本执行
`sync` 后才报告完成；随后使用实际设备名安全卸载：

```bash
udisksctl unmount -b /dev/sda1
```

## 上位机安装与启动

正式目标仍是 Ubuntu 22.04 / RTX 4060；安装脚本同时支持 Ubuntu 20.04 和 24.04。
Ubuntu 24.04 直接使用系统 Python 3.12，Ubuntu 20.04/22.04 没有可用的系统
Python 3.12 时才会把经过 SHA-256 校验的 CPython 3.12.13 编译到项目目录。
使用普通登录用户进入 `go2_inspection` 后执行，不要在整个命令前加 `sudo`：

```bash
bash scripts/install_ubuntu.sh --profile gpu-4060
bash scripts/start_server.sh
```

安装脚本固定使用 PyTorch 2.12.1 CUDA 12.6，并检查 NVIDIA 驱动、CUDA 可用性和
RTX 4060 设备名称。如果复制来的 `.venv` 属于 Windows、其他机器或错误 Python，
可加 `--recreate-venv`；旧环境会备份而不是删除。完整步骤和 Python 安装排错见
Ubuntu 部署指南。

Windows 可继续作为开发和现场调试环境。进入 `go2_inspection`，重新创建项目自己的
虚拟环境；整理副本不附带 `.venv`：

```powershell
cd "E:\视觉识别\Coal Mine Identification\go2_inspection"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

启动服务：

```powershell
.\scripts\start_server.ps1
```

- 工作台：`http://127.0.0.1:8000/ui`
- 健康检查：`http://127.0.0.1:8000/health`
- API 文档：`http://127.0.0.1:8000/docs`

默认数据、数据库、处理图片和识别结果均保存在项目内的 `runtime_data/`。

## U 盘数据导入与识别

先清空收件箱中上一批的三个数据目录，再把 U 盘根目录的 `gas`、`thermal`、
`visible` 直接复制到：

```text
go2_inspection/dataset_inbox/
├── gas/
├── thermal/
└── visible/
```

打开工作台 `http://127.0.0.1:8000/ui`，在顶部“U 盘离线数据”区域：

1. 点击“扫描收件箱”；
2. 确认可见光抓拍、气体记录和红外热像数量；
3. 点击“导入并预检”，此时不会自动识别；
4. 核对预检信息后点击“确认并开始检测”，服务才会在后台逐包处理；
5. 检测结束后先查看并按需人工修正结果，再点击“确认结果并开放报告”；
6. 点击“下载Word报告”；修正、重跑、删除或重试后报告会重新锁定；
7. 失败项修复后点击“重试失败项”，再重新确认检测，成功项不会重复识别。

上位机不会修改或删除 `dataset_inbox` 中的原文件。气体 CSV 会结构化入库，气体和
红外原文件会归档到 `runtime_data/imported_batches/<batch_id>/`。上位机同时兼容新的
`color_*.jpg` 单图与旧的“三张图片 + metadata.json”抓拍包；热像会读取PNG中的
机器可读温度统计，以整幅图最高温严格大于 65℃ 作为疑似托辊卡死异常，并关联前后
3 秒内最近的可信编号。同一编号的重复候选会合并，报告按位置保留最高温证据并汇总
“已识别 X/3 处”；位置未知的超温候选只进入人工复核。所有原始热像仍完整归档。
批次编号由三个目录中的文件路径自动生成；清空并换入
新一批时间戳文件后会得到新编号。导入完成前不要增删文件。无元数据单图不保存
工位号，时间按 UTC+08:00 解析。

树莓派保存带温度统计文字的伪彩色 PNG，并在 `thermal_stats_v1` PNG 文本元数据中
写入采集时间、样本编号、32×24 尺寸及最低/最高/平均温度。上位机直接读取该数值，
不对图片文字做 OCR。文件仍不包含 MLX90640 的原始 32×24 温度矩阵；若后续需要
热点区域定位、面积分析或重新计算温度场，仍需继续扩展采集格式。

## 样本、模型与结果

- `sample/`：现有编号牌和数字表样本；新增赛项样本按对齐矩阵要求补充。
- `models/`：上位机当前模型；基础模型位于 `models/base/`。
- `runtime_data/`：数据库、训练数据集、模型产物、评估报告和历史运行结果。
- `dataset_inbox/`：从 U 盘复制进来的 `gas`、`thermal`、`visible` 原始巡检数据。

更详细的部署、标注和训练说明见：

- [源码运行与工作台使用说明](go2_inspection/docs/源码运行与工作台使用说明.md)
- [部署与安装指南](go2_inspection/docs/部署与安装指南.md)
- [Ubuntu20.04与22.04部署指南](go2_inspection/docs/Ubuntu20.04与22.04部署指南.md)
- [标注规范](go2_inspection/docs/标注规范.md)
- [编号牌YOLO数据集与GPU训练](go2_inspection/docs/编号牌YOLO数据集与GPU训练.md)

## 测试

树莓派端：

```bash
cd raspberry-pi-sensor-logger
.venv/bin/python -m pip install --requirement requirements-dev.txt
.venv/bin/python -m pytest -q
```

上位机端：

```powershell
cd go2_inspection
python -m unittest discover -s tests -v
```

## 常见问题与安全

- 找不到 `0x33`：断电检查 MLX90640 的 VCC、GND、SDA 和 SCL。
- `/dev/serial0` 权限不足：确认运行过初始化脚本并已重启。
- 找不到 `/dev/video0`：运行 `v4l2-ctl --list-devices`，并关闭占用相机的程序。
- 报 `unknown encoding: utf‑8`：说明仍在运行旧版入口；新版已把错误的 Unicode 减号修正为标准 `utf-8`。
- 报 `externally-managed-environment`：不要使用系统 `pip` 或 `sudo pip`，应运行安装脚本创建 `.venv`。
- ARM 上 OpenCV 安装失败或长时间编译：使用 `setup_raspberry_pi.sh`，它通过系统包安装 `python3-opencv`。
- systemd 服务路径错误：在项目实际目录重新执行 `./scripts/install_service.sh`。
- U 盘导出失败：确认参数是已挂载文件系统，而不是普通目录。
- 上位机未识别可见光数据：新格式应为 `visible/color_YYYYMMDD_HHMMSS_ffffff.jpg`；
  旧格式应同时包含 `metadata.json` 和三张 `frame_*.jpg`。

本项目用于实验和比赛数据采集，不是经过认证的生命安全或工业联锁报警系统。在爆炸性环境使用任何传感器和计算设备时，必须遵守现场规范并使用合格的本安设备与防护措施。
