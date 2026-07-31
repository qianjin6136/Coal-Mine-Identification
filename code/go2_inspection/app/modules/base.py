"""巡检小模块的统一上下文和接口。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from ..domain import CaptureMetadata


@dataclass(frozen=True)
class ModuleContext:
    metadata: CaptureMetadata
    image_paths: Sequence[Path]
    objects: Sequence[dict[str, Any]]
    detector_configured: bool


class InspectionModule(Protocol):
    module_id: str

    def run(self, context: ModuleContext) -> dict[str, Any]:
        """返回该模块自己的状态和结果，不修改其他模块输出。"""
