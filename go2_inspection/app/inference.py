"""推理模式名称与 GPU 可用性检查。"""

from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
from typing import Any


def runtime_mode_for_backend(backend: str) -> str:
    """把项目配置中的后端名称转换为控制台使用的运行模式。"""

    return "gpu" if backend == "ultralytics" else backend


def detector_runtime_mode(detector: object) -> str:
    """返回检测器声明的控制台模式，兼容旧检测器实现。"""

    declared = getattr(detector, "runtime_mode", None)
    if declared:
        return str(declared)
    return runtime_mode_for_backend(str(getattr(detector, "name", "noop")))


def gpu_inference_status(weights: Path | None) -> dict[str, Any]:
    """检查权重、依赖和 CUDA，并返回可直接展示的状态。"""

    weights_path = str(weights) if weights is not None else None
    weights_configured = weights is not None
    weights_available = bool(weights is not None and weights.is_file())
    ultralytics_installed = find_spec("ultralytics") is not None
    torch_installed = find_spec("torch") is not None
    cuda_available = False
    device_name: str | None = None
    torch_error: str | None = None
    if torch_installed:
        try:
            import torch

            cuda_available = bool(torch.cuda.is_available())
            if cuda_available:
                device_name = str(torch.cuda.get_device_name(0))
        except Exception as exc:  # pragma: no cover - 依赖损坏时由现场环境触发
            torch_error = str(exc)

    reason: str | None = None
    reason_code: str | None = None
    if not weights_configured:
        reason_code = "weights_not_configured"
        reason = "未在 configs/app.json 中配置 detector.weights"
    elif not weights_available:
        reason_code = "weights_not_found"
        reason = f"模型权重文件不存在：{weights_path}"
    elif not ultralytics_installed:
        reason_code = "ultralytics_not_installed"
        reason = "未安装 Ultralytics，请安装 requirements.txt"
    elif not torch_installed:
        reason_code = "torch_not_installed"
        reason = "未安装 PyTorch CUDA 运行环境"
    elif torch_error:
        reason_code = "torch_check_failed"
        reason = f"检查 PyTorch CUDA 失败：{torch_error}"
    elif not cuda_available:
        reason_code = "cuda_unavailable"
        reason = "PyTorch 未检测到可用的 NVIDIA CUDA 显卡"

    return {
        "available": reason is None,
        "reason": reason,
        "reason_code": reason_code,
        "weights_path": weights_path,
        "weights_configured": weights_configured,
        "weights_available": weights_available,
        "ultralytics_installed": ultralytics_installed,
        "torch_installed": torch_installed,
        "cuda_available": cuda_available,
        "device_name": device_name,
    }
