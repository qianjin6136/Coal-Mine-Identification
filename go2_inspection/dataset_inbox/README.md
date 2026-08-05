# U 盘离线批次收件箱

把树莓派导出的完整 `inspection-export-*` 目录复制到这里，然后在工作台顶部点击
“扫描收件箱”和“导入并识别”。不要拆散或重命名批次中的 `gas`、`thermal`、
`visible` 目录。

上位机不会修改或删除这里的原文件。完成导入后，气体 CSV 和红外 PNG 的归档副本
位于 `runtime_data/imported_batches/<batch_id>/`。
