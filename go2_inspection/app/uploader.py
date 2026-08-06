"""用于调试与单次抓拍回放的标准库 HTTP 上传客户端。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib import error, request
import uuid

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
