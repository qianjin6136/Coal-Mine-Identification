# Coal Mine Identification

本项目用于煤矿巡检比赛中的离线数据采集与识别，分为树莓派采集端和上位机识别端。
树莓派在约 20 分钟的巡检过程中只负责采集和保存；比赛结束后通过 U 盘把完整数据复制到上位机，再进行图像识别、结果处理与报告生成。

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

目标系统为树莓派 Ubuntu 24.04、Python 3.12。把 `raspberry-pi-sensor-logger` 复制到树莓派后执行：

```powershell
scp -r ".\raspberry-pi-sensor-logger" aabb942218@192.168.137.30:/home/aabb942218/sensor-reader
```

登录树莓派后执行：

```bash
cd /home/aabb942218/sensor-reader
chmod +x scripts/*.sh
./scripts/setup_ubuntu.sh
sudo reboot
```

重启后检查硬件：

```bash
i2cdetect -y 1
ls -l /dev/serial0
v4l2-ctl --list-devices
```

单次硬件测试无需提供工位编号；旧命令中的 `--station-id` 仍可使用，但单图模式不会把它写入数据：

```bash
.venv/bin/python -m sensor_logger --once
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
- 相机断开或写盘失败会写入 `logs/sensor_logger.log`，不会停止气体和红外采集。

安装脚本暂时保留工位参数以兼容已有部署命令，但该值不会写入单图数据：

```bash
./scripts/install_service.sh 08
sudo systemctl status sensor-logger.service
journalctl -u sensor-logger.service -f
```

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

进入 `go2_inspection`，重新创建项目自己的虚拟环境；整理副本不附带 `.venv`：

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
3. 点击“导入并识别”；
4. 页面可以关闭，服务会在后台逐包处理；服务重启后会从未完成项自动续跑；
5. 失败项修复后点击“重试失败项”，成功项不会重复识别。

上位机不会修改或删除 `dataset_inbox` 中的原文件。气体 CSV 会结构化入库，气体和
红外原文件会归档到 `runtime_data/imported_batches/<batch_id>/`。上位机同时兼容新的
`color_*.jpg` 单图与旧的“三张图片 + metadata.json”抓拍包；气体和热像显示归档
状态，留给后续独立模块分析。批次编号由三个目录中的文件路径自动生成；清空并换入
新一批时间戳文件后会得到新的编号。导入完成前不要增删文件。无元数据单图不保存
工位号，时间按 UTC+08:00 解析。

原命令行上传工具仍可作为兼容方案，只处理可见光抓拍包：

```powershell
python .\scripts\upload_queue.py `
  ".\dataset_inbox\visible" `
  --server http://127.0.0.1:8000
```

当前树莓派保存的是带温度统计文字的伪彩色 PNG，没有保存 MLX90640 的原始
32×24 温度矩阵；后续若需要精确温度计算，必须再扩展树莓派采集格式。

## 样本、模型与结果

- `sample/`：编号牌、数字表、煤堆和传送带样本；所有配置均以该英文目录名为准。
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
python -m pytest -q
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
- U 盘导出失败：确认参数是已挂载文件系统，而不是普通目录。
- 上位机未识别可见光数据：新格式应为 `visible/color_YYYYMMDD_HHMMSS_ffffff.jpg`；
  旧格式应同时包含 `metadata.json` 和三张 `frame_*.jpg`。

本项目用于实验和比赛数据采集，不是经过认证的生命安全或工业联锁报警系统。在爆炸性环境使用任何传感器和计算设备时，必须遵守现场规范并使用合格的本安设备与防护措施。
