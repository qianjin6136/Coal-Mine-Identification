"""应用内可预期异常，供服务层和 HTTP 层统一转换。"""


class InspectionError(Exception):
    """可预期巡检失败的基类。"""


class ValidationError(InspectionError):
    """上传的元数据或图片内容不合法。"""


class CaptureNotFoundError(InspectionError):
    """请求的抓拍记录不存在。"""


class ConfigurationError(InspectionError):
    """应用配置缺失或格式不正确。"""
