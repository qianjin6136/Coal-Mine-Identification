"""模拟仪表和数字仪表识别辅助函数。"""

from .analog import PointerStatus, angular_distance_deg, classify_pointer_status
from .digital import DigitalReading, decode_segments, majority_vote_readings

__all__ = [
    "DigitalReading",
    "PointerStatus",
    "angular_distance_deg",
    "classify_pointer_status",
    "decode_segments",
    "majority_vote_readings",
]
"""仪表图像处理和多帧融合组件。"""

from .processor import MeterProcessor

__all__ = ["MeterProcessor"]
