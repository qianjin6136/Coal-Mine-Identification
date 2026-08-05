# 编号牌 YOLO 数据集与 GPU 训练

## 1. v2 数据集

已生成可直接训练的 Ultralytics YOLO 数据集：

```text
E:\视觉识别\Coal Mine Identification\go2_inspection\runtime_data\datasets\station_marker_v2
```

本数据集只有一个检测类别：`station_marker`（实体区段编号牌）。YOLO 负责定位
编号牌，数字 1～10 仍由后续编号识别模块读取。屏幕中显示的递归画面、蓝色门、
水瓶和蓝色箭头牌均不作为目标。

数据统计：

- 200 张图片，其中 187 张正样本、13 张确认负样本；
- 191 个目标框；6 号桌面序列中同时出现的实体 7 号牌也已标注；
- 训练集 140 张、验证集 50 张、测试集 10 张；
- 负样本标签文件为空，分配为训练集 9 张、验证集 4 张；
- 跨划分近重复检查为 0 对。

训练前查看绿色目标框和橙色 `NEGATIVE` 预览：

```text
runtime_data\datasets\station_marker_v2\previews\train.jpg
runtime_data\datasets\station_marker_v2\previews\val.jpg
runtime_data\datasets\station_marker_v2\previews\test.jpg
```

原先误放在 `sample/编号/3` 中的数字 4 图片已经移动到 `sample/编号/4`。分类器裁剪构建器
按 SHA-256 匹配已有人工框，因此移动目录不会使框标定失效，并会以纠正后的数字目录为准。

## 2. 更新 1～10 编号分类器

先从复核过的 YOLO 框生成只含编号牌的裁剪集，再训练分类器：

```powershell
.\.venv\Scripts\python.exe `
  .\scripts\build_station_number_crops.py

.\.venv\Scripts\python.exe `
  .\scripts\train_station_number.py
```

输出模型和报告分别为：

```text
runtime_data\models\station_number_templates_v2.npz
runtime_data\station_number\training_metrics_v2.json
```

运行配置已经使用 1～10、上述 V2 模型和校准后的 `0.46` 置信度阈值。新增或移动原图后，
请新建一个版本化输出目录，避免把不同批次的裁剪样本混在一起。

## 3. 使用当前最新版 YOLO26 训练

项目内已有 `models/base/yolo26n.pt`，训练脚本默认权重也已切换为 YOLO26n。RTX 4060 8GB
先用 `batch=8`；如果显存不足改为 `4`。

在 PowerShell 中运行：

```powershell
cd "E:\视觉识别\Coal Mine Identification\go2_inspection"

.\.venv\Scripts\python.exe `
  .\scripts\train_detector.py `
  .\runtime_data\datasets\station_marker_v2\dataset.yaml `
  --weights .\models\base\yolo26n.pt `
  --epochs 100 `
  --imgsz 640 `
  --batch 8 `
  --device 0 `
  --project .\runtime_data\runs\detect `
  --name station_marker_v2_yolo26n
```

最佳权重通常位于：

```text
E:\视觉识别\Coal Mine Identification\go2_inspection\runtime_data\runs\detect\station_marker_v2_yolo26n\weights\best.pt
```

若训练中断，应从同一次运行的 `last.pt` 恢复，而不是改基础模型后继续同一运行：

```powershell
.\.venv\Scripts\python.exe -c "from ultralytics import YOLO; YOLO(r'runtime_data\runs\detect\station_marker_v2_yolo26n\weights\last.pt').train(resume=True)"
```

## 4. 评估与部署

先在冻结测试集运行评估：

```powershell
.\.venv\Scripts\python.exe `
  .\scripts\evaluate_detector.py `
  .\runtime_data\runs\detect\station_marker_v2_yolo26n\weights\best.pt `
  .\runtime_data\datasets\station_marker_v2\dataset.yaml `
  --split test `
  --imgsz 640 `
  --batch 8 `
  --device 0 `
  --output .\runtime_data\runs\detect\station_marker_v2_yolo26n\test_metrics.json
```

除了 mAP50-95、Precision 和 Recall，还要逐图检查负样本是否误报。当前测试集 10 张
正样本共用同一煤矿传送带合成背景，因此该结果只能作为回归参考，不能替代真实井下
独立测试。

确认效果后，将最佳权重复制到运行模型位置：

```powershell
Copy-Item `
  .\runtime_data\runs\detect\station_marker_v2_yolo26n\weights\best.pt `
  .\models\station_marker_best.pt
```

`configs/app.json` 保持：

```json
"detector": {
  "backend": "noop",
  "weights": "models/station_marker_best.pt",
  "confidence": 0.35
}
```

重启服务后，在控制台点击“GPU”；如需服务启动时立即加载模型，才将
`backend` 改为 `ultralytics`。

## 5. 如何更换为新的基础模型

不能把旧权重文件改名来升级模型。升级时要安装支持新架构的 Ultralytics 版本，取得
新的官方预训练权重，然后开启一次新的训练运行。例如 YOLO11 升到 YOLO26：

```powershell
.\.venv\Scripts\python.exe -m pip install -U ultralytics

# 只需把 --weights 从 models/base/yolo11n.pt 改为 models/base/yolo26n.pt，并使用新的 --name
.\.venv\Scripts\python.exe `
  .\scripts\train_detector.py `
  .\runtime_data\datasets\station_marker_v2\dataset.yaml `
  --weights .\models\base\yolo26n.pt `
  --epochs 100 --imgsz 640 --batch 8 --device 0 `
  --project .\runtime_data\runs\detect `
  --name station_marker_v2_yolo26n
```

需要更高精度时可尝试 `yolo26s.pt`，但 8GB 显存应从 `batch=4` 开始。每次更换架构
都保留相同的冻结测试集，分别评估速度、显存和精度，再决定部署哪个 `best.pt`。

## 6. 后续数据迭代

新增原图后复制 v2 配置为 v3，修改 `output_root`，补充人工框/划分，再生成新目录；
不要覆盖已有数据集：

```powershell
.\.venv\Scripts\python.exe `
  .\scripts\build_station_marker_dataset.py `
  --config .\configs\station_marker_dataset_v3.json
```

优先补充真实煤矿环境中的低光、反光、运动模糊、远距离小目标以及“蓝色物体但没有
编号牌”的困难负样本。相邻连拍必须保持在同一个数据划分中。
