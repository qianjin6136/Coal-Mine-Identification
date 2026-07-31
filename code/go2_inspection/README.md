# GO2 煤矿实验室视觉巡检上位机

当前版本完成工程骨架，并把识别任务拆成五个独立模块：

- GO2 图片、拍照时间、区段和机器人位姿上传；
- 图片本地存储与 SHA-256；
- SQLite 任务、图片、结果、目标和人工修正审计记录；
- 三连拍目标融合；
- 结果画框和证据图；
- 蓝底白字工位编号牌定位、1～10 分类和多帧投票；
- 指针表 ROI 角度提取及正常/异常/不确定融合；
- 数字表 ROI 七段解码和三帧多数投票；
- 可替换的无模型、JSON 重放和 Ultralytics 检测后端；
- 冲突检测的 capture ID 幂等处理和断网补传队列；
- 数据质量检查、清单、分组切分及 YOLO 标注转换；
- 训练、冻结集评估和准备状态检查脚本；
- Web 结果列表、原图/标注图查看、人工修正、重处理和 CSV/JSON 导出；
- 单元测试。

五个模块及其当前状态：

- 工具/安全标识牌：清单未冻结，禁用；
- 煤堆：独立二分类接口，检测模型未训练；
- 工位编号：已用 10 张编号牌样本训练 1～10 图像模板分类器；
- 数字表：已按 22 张新文件名标签重新训练逐位模板模型；
- 指针表：缺正常/异常图片，禁用。

源码启动和工作台操作见
[`docs/源码运行与工作台使用说明.md`](docs/源码运行与工作台使用说明.md)。
完整安装、训练、上线和故障处理见
[`docs/部署与安装指南.md`](docs/部署与安装指南.md)。
Ubuntu 安装、编译和服务化运行见
[`docs/Ubuntu20.04与22.04部署指南.md`](docs/Ubuntu20.04与22.04部署指南.md)。

默认 `noop` 检测器不会输出目标，但会完整保存图片、位置和结果记录。数据集及模型权重到位后，把 `configs/app.json` 中的检测器改成 `ultralytics` 即可。

## 1. 环境

Windows、Ubuntu 20.04 和 Ubuntu 22.04 统一使用 Python 3.12。

Windows：

```powershell
cd E:\视觉识别\code\go2_inspection
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Ubuntu 20.04/22.04：

```bash
cd /path/to/视觉识别/code/go2_inspection
bash scripts/install_ubuntu.sh --profile runtime
```

Ubuntu 安装器会检查发行版，安装编译依赖，并在系统没有 Python 3.12 时从官方
CPython 3.12.13 源码构建项目专用解释器，再创建 `.venv`。需要 YOLO 时把
`runtime` 改为 `full`，开发测试环境改为 `dev`。

本工作区已在 `E:\视觉识别\.venv` 安装完整视觉依赖，并验证 RTX 5060 Ti 可通过
PyTorch CUDA 13.0 执行张量运算。`start_server.ps1` 会优先寻找项目内 `.venv`，
其次使用该工作区环境。

在相同 Windows GPU 环境复现 CUDA 依赖时：

```powershell
python -m pip install -r requirements-gpu-windows.txt
```

当前只运行 API 和数字表模块时，安装精简运行依赖：

```powershell
python -m pip install -r requirements-runtime.txt
```

## 2. 启动

Windows：

```powershell
.\scripts\start_server.ps1
```

Ubuntu：

```bash
bash scripts/start_server.sh
```

两个启动脚本都会校验 Python 必须为 3.12。监听地址和端口可分别通过
`GO2_HOST`、`GO2_PORT` 环境变量覆盖。

打开：

- 健康检查：`http://127.0.0.1:8000/health`
- 图片传输与参数调试工作台：`http://127.0.0.1:8000/ui`
- Swagger：`http://127.0.0.1:8000/docs`

默认数据写入工作区的 `E:\视觉识别\runtime_data\`，数据库为
`E:\视觉识别\runtime_data\database\inspection.db`。运行数据与源码分离，方便整体备份和清理。

## 3. 用现有图片重放

服务启动后：

```powershell
python .\scripts\replay_capture.py `
  ..\..\fig\微信图片_20260728145300_104_5.jpg `
  ..\..\fig\微信图片_20260728145300_104_5.jpg `
  ..\..\fig\微信图片_20260728145300_104_5.jpg `
  --station 10 --x 12.4 --y 3.1 --yaw 90
```

重复使用同一个 `--capture-id` 不会创建重复记录，便于 GO2 断网后安全补传。
若相同 ID 携带不同元数据或图片，服务会拒绝请求，避免错误数据静默覆盖。

对于断网缓存目录，每个抓拍包使用以下结构：

```text
queue/
  capture_id/
    metadata.json
    frame_01.jpg
    frame_02.jpg
    frame_03.jpg
```

网络恢复后执行一次补传或持续监听：

```powershell
python .\scripts\upload_queue.py .\queue
python .\scripts\upload_queue.py .\queue --watch --interval 5
```

成功后只新增 `upload_receipt.json`，不会删除原图；失败包留在原地等待下一次重试。

## 4. 在没有模型时测试整条流程

把 `configs/app.json` 的检测后端临时改成：

```json
{
  "detector": {
    "backend": "json_replay",
    "weights": null,
    "confidence": 0.35
  }
}
```

对于 `frame_01.jpg`，在相同目录放置：

```text
frame_01.jpg.detections.json
```

内容示例：

```json
[
  {
    "type": "tool",
    "class": "wrench",
    "class_cn": "扳手",
    "bbox_xyxy": [510, 330, 750, 610],
    "confidence": 0.96
  }
]
```

这样无需模型权重也能验证数据库、三帧融合、画框和结果导出链路。

## 5. 接入训练后的模型

1. 在 `configs/classes.json` 中冻结类别映射；
2. 把权重放入 `models/`；
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

4. 重启服务并用冻结测试集回归。

## 6. 数据质检、标注转换和训练

收到原始数据后先生成质量报告和防泄漏切分：

```powershell
python .\scripts\prepare_dataset.py D:\go2_raw `
  --output ..\..\runtime_data\dataset_reports\batch_001
```

输出包括：

- `manifest.jsonl`：逐图来源、哈希、质量指标和 train/val/test；
- `quality_summary.json`：数量、切分、问题统计和非三连拍清单；
- `quality_issues.csv`：需要人工复核的坏图、重复、曝光和模糊候选。

详细画框规则见 `docs/标注规范.md`。确认标注后转换：

```powershell
python .\scripts\build_yolo_dataset.py `
  ..\..\runtime_data\dataset_reports\batch_001\manifest.jsonl `
  --output D:\go2_dataset_v1 --mode hardlink
```

先检查训练参数，再启动首个基线：

```powershell
python .\scripts\train_detector.py D:\go2_dataset_v1\dataset.yaml --dry-run
python .\scripts\train_detector.py D:\go2_dataset_v1\dataset.yaml `
  --weights yolo11n.pt --epochs 50 --device 0
```

在冻结测试集评估：

```powershell
python .\scripts\evaluate_detector.py `
  .\runs\detect\go2_baseline\weights\best.pt `
  D:\go2_dataset_v1\dataset.yaml --split test --device 0
```

随时可检查准备状态：

```powershell
python .\scripts\check_readiness.py
```

## 7. 图片传输、参数调试和结果复核

`/ui` 页面可拖入 1～5 张图片并附带可选 `metadata.json`，监控自动或手动上传任务，
调整检测置信度、三帧融合 IoU、数字表阈值和模块开关。工位与机器人位姿只读展示，
不在页面中修改。

参数保存在 `runtime_data/runtime_settings.json`，对后续任务立即生效；历史任务需要人工
点击重新处理。每次结果都会保存实际使用的参数快照。结果面板可查看原图与标注图、
结构化编辑识别对象、保存带操作人和原因的人工修正。重处理会停用旧修正，但历史审计
记录不会删除。

主要接口：

- `GET /api/v1/captures`：分页列表；
- `GET /api/v1/runtime-settings`：当前参数、默认值、上传限制和模块状态；
- `PATCH /api/v1/runtime-settings`：保存受控运行参数并热更新；
- `POST /api/v1/runtime-settings/reset`：恢复项目默认参数；
- `GET /api/v1/results/{capture_id}`：模型结果、有效修正和审计摘要；
- `PATCH /api/v1/results/{capture_id}/correction`：人工修正；
- `POST /api/v1/results/{capture_id}/reprocess`：重处理；
- `GET /api/v1/export?format=csv|json`：结果导出。

## 8. 测试

不安装 pytest 也可以使用标准库执行：

```powershell
python -m unittest discover -s tests -v
```

当前测试覆盖元数据校验、路径安全、三帧融合、编号牌与仪表判断、SQLite 入库、
重复上传冲突、人工修正、导出、数据切分、标注转换和断网补传回执。

## 9. 待现场数据确认

1. 用正式工具、标牌类别替换 `configs/classes.json` 的工程占位项；
2. 为独立煤堆有/无模块补充并标注现场正负样本；
3. 为 1～10 工位编号补充现场角度和距离变化图；
4. 使用指针表正常/异常照片标定 ROI 圆心、参考角度、容差和颜色阈值；
5. 为数字表补充非零小数、负号、缺段、反光和透视变化测试；
6. 与 GO2 端确认相机取图和机器人位姿来源，并完成实际 HTTP 联调。
