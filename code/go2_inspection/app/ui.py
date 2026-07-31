"""GO2 图片传输与参数调试工作台页面。"""

from __future__ import annotations

from pathlib import Path


_UI_PATH = Path(__file__).resolve().parent / "static" / "index.html"


def inspection_ui_html() -> str:
    """返回由同源 API 驱动的工作台入口页面。"""

    return _UI_PATH.read_text(encoding="utf-8")
