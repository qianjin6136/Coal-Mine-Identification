# 编号牌 YOLO 数据集与 GPU 训练

## 1. 当前数据集

已生成的数据集：

```text
E:\视觉识别\runtime_data\datasets\station_marker_v1
```

数据集只有一个目标检测类别：`station_marker`（区段编号牌）。数字 7、8、9、10
仍由编号模板模型负责识别，YOLO 只负责找到编号牌的位置。

当前划分：

- 训练集：42 张室内图片；
- 验证集：17 张独立桌面、地面和开放办公区场景；
- 测试集：4 张煤矿现场图片；
- 16 张分割错误、误选背景或严重模糊的原图未进入本版数据集。

训练前可查看绿色目标框预览：

```text
runtime_data\datasets\station_marker_v1\previews\train.jpg
runtime_data\datasets\station_marker_v1\previews\val.jpg
runtime_data\datasets\station_marker_v1\previews\test.jpg
```

## 2. 启动 GPU 训练

在 PowerShell 中完整复制以下命令：

```powershell
cd E:\视觉识别

.\.venv\Scripts\python.exe `
  .\code\go2_inspection\scripts\train_detector.py `
  .\runtime_data\datasets\station_marker_v1\dataset.yaml `
  --weights yolo11n.pt `
  --epochs 100 `
  --imgsz 640 `
  --batch 8 `
  --device 0 `
  --project .\runtime_data\runs\detect `
  --name station_marker_v1
```

首次运行会下载通用预训练权重 `yolo11n.pt`。RTX 5060 Ti 8GB 建议先使用
`batch=8`；如果出现显存不足，把它改成 `4`。

训练完成后的最佳权重通常位于：

```text
E:\视觉识别\runtime_data\runs\detect\station_marker_v1\weights\best.pt
```

## 3. 接入控制台

先把 `station_marker_best.pt` 复制为：

```text
E:\视觉识别\code\go2_inspection\models\station_marker_best.pt
```

再把 `configs/app.json` 的检测器配置改成：

```json
"detector": {
  "backend": "noop",
  "weights": "models/station_marker_best.pt",
  "confidence": 0.35
}
```

重启服务后，在控制台点击“GPU”。保留 `backend=noop` 可以在权重或 CUDA
异常时安全启动；需要启动时立即加载 GPU 才改为 `backend=ultralytics`。

## 4. 后续增加数据

新增图片先进入 `样本/编号/<数字>/` 并完成人工质检。不要覆盖 v1 数据集；复制
`configs/station_marker_dataset_v1.json`，把输出目录改成 `station_marker_v2`，然后运行：

```powershell
.\.venv\Scripts\python.exe `
  .\code\go2_inspection\scripts\build_station_marker_dataset.py `
  --config .\code\go2_inspection\configs\station_marker_dataset_v2.json
```

正式部署前还需要补充：没有编号牌的负样本、更多煤矿现场图片、真实低光和强反光
图片。相邻连拍必须放在同一个数据划分中。
