"""标准库 HTTP 上传客户端和断网可恢复的本地抓拍队列。"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence
from urllib import error, request
import uuid

from .domain import CaptureMetadata
from .errors import ValidationError
from .storage import safe_file_name


def encode_multipart(
    metadata: Mapping[str, Any],
    image_paths: Sequence[Path],
) -> tuple[bytes, str]:
    """编码服务端 ``metadata + images`` multipart 请求。"""

    boundary = f"go2-{uuid.uuid4().hex}"
    parts: list[bytes] = []

    def add(value: bytes) -> None:
        parts.append(value)

    add(f"--{boundary}\r\n".encode())
    add(b'Content-Disposition: form-data; name="metadata"\r\n')
    add(b"Content-Type: application/json; charset=utf-8\r\n\r\n")
    add(json.dumps(dict(metadata), ensure_ascii=False).encode("utf-8"))
    add(b"\r\n")
    declared_names = metadata.get("images")
    upload_names = (
        [str(name) for name in declared_names]
        if isinstance(declared_names, Sequence)
        and not isinstance(declared_names, (str, bytes))
        and len(declared_names) == len(image_paths)
        else [image_path.name for image_path in image_paths]
    )
    for image_path, upload_name in zip(image_paths, upload_names):
        portable_name = safe_file_name(upload_name, image_path.name)
        add(f"--{boundary}\r\n".encode())
        add(
            (
                'Content-Disposition: form-data; name="images"; '
                f'filename="{portable_name}"\r\n'
            ).encode("utf-8")
        )
        content_type = (
            "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        )
        add(f"Content-Type: {content_type}\r\n\r\n".encode())
        add(image_path.read_bytes())
        add(b"\r\n")
    add(f"--{boundary}--\r\n".encode())
    return b"".join(parts), boundary


def upload_capture(
    server: str,
    metadata: Mapping[str, Any],
    image_paths: Sequence[Path],
    *,
    timeout: float = 60.0,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """上传一组抓拍并把 HTTP 错误转换为可记录的异常文本。"""

    body, boundary = encode_multipart(metadata, image_paths)
    http_request = request.Request(
        f"{server.rstrip('/')}/api/v1/captures",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    open_request = opener or request.urlopen
    try:
        with open_request(http_request, timeout=timeout) as response:
            result = json.load(response)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"upload failed with HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("upload response must be a JSON object")
    return result


def process_upload_queue(
    queue_root: Path,
    server: str,
    *,
    timeout: float = 60.0,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """上传队列中尚无成功回执的抓拍包；失败包留在原地等待下次补传。"""

    queue_root = Path(queue_root).resolve()
    if not queue_root.is_dir():
        raise ValidationError(f"queue directory not found: {queue_root}")
    uploaded: list[str] = []
    skipped: list[str] = []
    failed: list[dict[str, str]] = []
    for metadata_path in sorted(queue_root.rglob("metadata.json")):
        package_root = metadata_path.parent
        receipt_path = package_root / "upload_receipt.json"
        if receipt_path.is_file():
            skipped.append(str(package_root))
            continue
        try:
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping):
                raise ValidationError("metadata.json must be a JSON object")
            metadata = CaptureMetadata.from_mapping(raw)
            image_paths = [
                _safe_package_file(package_root, name)
                for name in metadata.image_names
            ]
            for image_path in image_paths:
                if not image_path.is_file():
                    raise ValidationError(f"queued image not found: {image_path.name}")
            response = upload_capture(
                server,
                metadata.to_dict(),
                image_paths,
                timeout=timeout,
                opener=opener,
            )
            receipt = {
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "server": server,
                "capture_id": metadata.capture_id,
                "response": response,
            }
            temporary = receipt_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(receipt_path)
            uploaded.append(metadata.capture_id)
        except Exception as exc:
            failed.append(
                {
                    "package": str(package_root),
                    "error": str(exc),
                }
            )
    return {
        "uploaded": uploaded,
        "skipped_with_receipt": skipped,
        "failed": failed,
    }


def watch_upload_queue(
    queue_root: Path,
    server: str,
    *,
    interval_seconds: float = 5.0,
    timeout: float = 60.0,
) -> None:
    """持续补传；进程停止不会删除或丢失任何待传抓拍包。"""

    while True:
        summary = process_upload_queue(queue_root, server, timeout=timeout)
        if summary["uploaded"] or summary["failed"]:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        time.sleep(max(1.0, interval_seconds))


def _safe_package_file(package_root: Path, name: str) -> Path:
    # 队列可能由 Windows 采集端生成后复制到 Ubuntu，统一处理两种分隔符。
    relative_name = str(name).replace("\\", "/")
    if relative_name.startswith("/") or (
        len(relative_name) >= 2
        and relative_name[0].isalpha()
        and relative_name[1] == ":"
    ):
        raise ValidationError(f"image path must be relative: {name}")
    path = (package_root / relative_name).resolve()
    try:
        path.relative_to(package_root.resolve())
    except ValueError as exc:
        raise ValidationError(f"image path escapes capture package: {name}") from exc
    return path
