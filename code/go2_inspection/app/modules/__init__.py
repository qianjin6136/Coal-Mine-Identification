"""可独立启停的巡检业务模块。"""

from .registry import ModuleRegistry, build_module_registry

__all__ = ["ModuleRegistry", "build_module_registry"]
