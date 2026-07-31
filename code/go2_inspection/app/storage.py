"""使用文件系统保存图片，并用 SQLite 持久化抓拍任务与检测结果。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any, Iterable, Iterator, Sequence

from .domain import CaptureMetadata, InspectionResult
from .errors import CaptureNotFoundError, ValidationError


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_ALLOWED_IMAGE_SIGNATURES = (
    b"\xff\xd8\xff",  # JPEG 文件头
    b"\x89PNG\r\n\x1a\n",
    b"BM",  # BMP 文件头
)


def portable_file_name(name: str) -> str:
    """以相同方式提取 Windows 或 POSIX 路径中的文件名。"""

    # Linux 的 Path 不把反斜杠视为分隔符；先统一分隔符，确保来自
    # Windows 和 Linux 客户端的文件名在两个服务端系统上得到相同结果。
    return str(name).replace("\\", "/").rsplit("/", 1)[-1].strip()


def safe_file_name(name: str, fallback: str) -> str:
    """移除目录信息和不安全字符，得到可控的本地文件名。"""

    base = portable_file_name(name)
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("._")
    return cleaned[:120] or fallback


def validate_image_bytes(payload: bytes, max_bytes: int) -> None:
    """通过大小和文件头做轻量校验，阻止明显无效的上传内容落盘。"""

    if not payload:
        raise ValidationError("uploaded image is empty")
    if len(payload) > max_bytes:
        raise ValidationError(f"uploaded image exceeds {max_bytes} bytes")
    if not any(payload.startswith(signature) for signature in _ALLOWED_IMAGE_SIGNATURES):
        raise ValidationError("uploaded file is not a supported JPEG, PNG or BMP image")


class CaptureRepository:
    """封装图片目录和 SQLite 表，向服务层提供一致的持久化接口。"""

    def __init__(self, database_path: Path, storage_root: Path) -> None:
        self.database_path = Path(database_path)
        self.storage_root = Path(storage_root)
        self.incoming_root = self.storage_root / "incoming"
        self.processed_root = self.storage_root / "processed"
        self.evidence_root = self.storage_root / "evidence"
        for path in (
            self.database_path.parent,
            self.incoming_root,
            self.processed_root,
            self.evidence_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """提供带外键约束的短连接，并统一管理提交、回滚和关闭。"""

        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        """以可重复执行的方式创建数据库表。"""

        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS captures (
                    capture_id TEXT PRIMARY KEY,
                    capture_time TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    station_id TEXT NOT NULL,
                    camera_id TEXT NOT NULL,
                    pose_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    capture_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    original_name TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    UNIQUE(capture_id, position),
                    FOREIGN KEY(capture_id) REFERENCES captures(capture_id)
                );

                CREATE TABLE IF NOT EXISTS objects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    capture_id TEXT NOT NULL,
                    object_index INTEGER NOT NULL,
                    object_type TEXT NOT NULL,
                    class_id TEXT,
                    confidence REAL,
                    bbox_json TEXT,
                    data_json TEXT NOT NULL,
                    UNIQUE(capture_id, object_index),
                    FOREIGN KEY(capture_id) REFERENCES captures(capture_id)
                );

                CREATE TABLE IF NOT EXISTS corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    capture_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    reason TEXT,
                    corrected_result_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(capture_id) REFERENCES captures(capture_id)
                );

                CREATE INDEX IF NOT EXISTS idx_captures_status
                ON captures(status);
                CREATE INDEX IF NOT EXISTS idx_captures_station
                ON captures(station_id);
                CREATE INDEX IF NOT EXISTS idx_corrections_capture
                ON corrections(capture_id, created_at);
                """
            )

    def capture_exists(self, capture_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM captures WHERE capture_id = ?", (capture_id,)
            ).fetchone()
        return row is not None

    def save_capture(
        self,
        metadata: CaptureMetadata,
        images: Sequence[tuple[str, bytes]],
        max_image_bytes: int,
    ) -> list[Path]:
        """校验并保存原图，然后在同一事务中登记任务和图片索引。"""

        if len(images) != len(metadata.image_names):
            raise ValidationError(
                f"metadata declares {len(metadata.image_names)} images but {len(images)} were uploaded"
            )
        for index, (expected_name, (uploaded_name, _)) in enumerate(
            zip(metadata.image_names, images)
        ):
            if portable_file_name(expected_name) != portable_file_name(uploaded_name):
                raise ValidationError(
                    f"uploaded image name at position {index} does not match metadata"
                )
        if self.capture_exists(metadata.capture_id):
            return self.image_paths(metadata.capture_id)

        # 先完成全部校验，避免第二或第三张坏图导致目录中残留半组数据。
        for _, payload in images:
            validate_image_bytes(payload, max_image_bytes)

        capture_dir = self.incoming_root / metadata.capture_id
        capture_dir.mkdir(parents=True, exist_ok=True)
        stored: list[tuple[int, str, Path, str, int]] = []
        used_names: set[str] = set()
        for index, (original_name, payload) in enumerate(images):
            file_name = safe_file_name(original_name, f"frame_{index + 1:02d}.jpg")
            if file_name in used_names:
                # 清洗后可能出现同名文件，添加位置前缀以保持每帧路径唯一。
                file_name = f"{index + 1:02d}_{file_name}"
            used_names.add(file_name)
            destination = capture_dir / file_name
            destination.write_bytes(payload)
            stored.append(
                (
                    index,
                    original_name,
                    destination,
                    hashlib.sha256(payload).hexdigest(),
                    len(payload),
                )
            )
        (capture_dir / "metadata.json").write_text(
            json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata.to_dict(), ensure_ascii=False)
        pose_json = json.dumps(metadata.robot_pose.to_dict(), ensure_ascii=False)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO captures (
                    capture_id, capture_time, received_at, station_id, camera_id,
                    pose_json, metadata_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'received')
                """,
                (
                    metadata.capture_id,
                    metadata.capture_time,
                    now,
                    metadata.station_id,
                    metadata.camera_id,
                    pose_json,
                    metadata_json,
                ),
            )
            connection.executemany(
                """
                INSERT INTO images (
                    capture_id, position, original_name, stored_path, sha256, size_bytes
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        metadata.capture_id,
                        position,
                        original_name,
                        str(path),
                        digest,
                        size,
                    )
                    for position, original_name, path, digest, size in stored
                ],
            )
        return [item[2] for item in stored]

    def image_paths(self, capture_id: str) -> list[Path]:
        """按上传顺序返回抓拍图片路径。"""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT stored_path FROM images WHERE capture_id = ? ORDER BY position",
                (capture_id,),
            ).fetchall()
        if not rows and not self.capture_exists(capture_id):
            raise CaptureNotFoundError(capture_id)
        return [Path(row["stored_path"]) for row in rows]

    def delete_capture(self, capture_id: str) -> dict[str, Any]:
        """删除任务及其关联数据库记录，并清理该任务占用的文件目录。"""

        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM captures WHERE capture_id = ?", (capture_id,)
            ).fetchone()
            if exists is None:
                raise CaptureNotFoundError(capture_id)
            for table in ("corrections", "objects", "images"):
                connection.execute(
                    f"DELETE FROM {table} WHERE capture_id = ?", (capture_id,)
                )
            connection.execute(
                "DELETE FROM captures WHERE capture_id = ?", (capture_id,)
            )

        cleanup_warnings: list[str] = []
        for root in (self.incoming_root, self.processed_root, self.evidence_root):
            root_resolved = root.resolve()
            target = (root / capture_id).resolve()
            if target.parent != root_resolved:
                cleanup_warnings.append(f"refused unsafe cleanup path: {target}")
                continue
            if not target.exists():
                continue
            try:
                shutil.rmtree(target)
            except OSError as exc:
                cleanup_warnings.append(f"{target}: {exc}")
        return {
            "capture_id": capture_id,
            "deleted": True,
            "cleanup_warnings": cleanup_warnings,
        }

    def save_result(
        self,
        result: InspectionResult,
        *,
        deactivate_corrections: bool = False,
    ) -> None:
        """原子更新任务结果，并重建便于检索的目标明细记录。"""

        payload = result.to_dict()
        with self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE captures
                SET status = 'processed', result_json = ?, error = NULL
                WHERE capture_id = ?
                """,
                (json.dumps(payload, ensure_ascii=False), result.capture_id),
            )
            if updated.rowcount == 0:
                raise CaptureNotFoundError(result.capture_id)
            connection.execute(
                "DELETE FROM objects WHERE capture_id = ?", (result.capture_id,)
            )
            rows = []
            for index, item in enumerate(result.objects):
                rows.append(
                    (
                        result.capture_id,
                        index,
                        str(item.get("type", "unknown")),
                        item.get("class"),
                        item.get("confidence"),
                        json.dumps(item.get("bbox_xyxy"), ensure_ascii=False),
                        json.dumps(item, ensure_ascii=False),
                    )
                )
            connection.executemany(
                """
                INSERT INTO objects (
                    capture_id, object_index, object_type, class_id,
                    confidence, bbox_json, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            if deactivate_corrections:
                connection.execute(
                    "UPDATE corrections SET active = 0 WHERE capture_id = ?",
                    (result.capture_id,),
                )

    def save_error(self, capture_id: str, error: str) -> None:
        """把已入库任务标记为失败，并限制错误文本长度。"""

        with self._connection() as connection:
            updated = connection.execute(
                "UPDATE captures SET status = 'failed', error = ? WHERE capture_id = ?",
                (error[:2000], capture_id),
            )
            if updated.rowcount == 0:
                raise CaptureNotFoundError(capture_id)

    def get_capture(self, capture_id: str) -> dict[str, Any]:
        """读取任务、汇总结果及其图片索引。"""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM captures WHERE capture_id = ?", (capture_id,)
            ).fetchone()
            image_rows = connection.execute(
                """
                SELECT position, original_name, stored_path, sha256, size_bytes
                FROM images WHERE capture_id = ? ORDER BY position
                """,
                (capture_id,),
            ).fetchall()
            correction_rows = connection.execute(
                """
                SELECT id, created_at, operator, reason, corrected_result_json, active
                FROM corrections
                WHERE capture_id = ?
                ORDER BY id
                """,
                (capture_id,),
            ).fetchall()
        if row is None:
            raise CaptureNotFoundError(capture_id)
        original_result = (
            json.loads(row["result_json"]) if row["result_json"] else None
        )
        active_correction = next(
            (item for item in reversed(correction_rows) if item["active"]), None
        )
        effective_result = (
            json.loads(active_correction["corrected_result_json"])
            if active_correction is not None
            else original_result
        )
        return {
            "capture_id": row["capture_id"],
            "capture_time": row["capture_time"],
            "received_at": row["received_at"],
            "station_id": row["station_id"],
            "camera_id": row["camera_id"],
            "robot_pose": json.loads(row["pose_json"]),
            "status": row["status"],
            "result": effective_result,
            "original_result": (
                original_result if active_correction is not None else None
            ),
            "manually_corrected": active_correction is not None,
            "error": row["error"],
            "images": [dict(image_row) for image_row in image_rows],
            "corrections": [
                {
                    "id": correction["id"],
                    "created_at": correction["created_at"],
                    "operator": correction["operator"],
                    "reason": correction["reason"],
                    "active": bool(correction["active"]),
                }
                for correction in correction_rows
            ],
        }

    def get_metadata(self, capture_id: str) -> CaptureMetadata:
        """从原始入库快照重建抓拍元数据，供离线重处理使用。"""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM captures WHERE capture_id = ?",
                (capture_id,),
            ).fetchone()
        if row is None:
            raise CaptureNotFoundError(capture_id)
        return CaptureMetadata.from_mapping(json.loads(row["metadata_json"]))

    def assert_replay_matches(
        self,
        metadata: CaptureMetadata,
        images: Sequence[tuple[str, bytes]],
    ) -> None:
        """拒绝同一 capture_id 携带不同元数据或图片内容的冲突重传。"""

        existing_metadata = self.get_metadata(metadata.capture_id)
        if existing_metadata.to_dict() != metadata.to_dict():
            raise ValidationError(
                "capture_id already exists with different metadata"
            )
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT position, sha256 FROM images
                WHERE capture_id = ?
                ORDER BY position
                """,
                (metadata.capture_id,),
            ).fetchall()
        if len(rows) != len(images):
            raise ValidationError(
                "capture_id already exists with a different number of images"
            )
        for row, (_, payload) in zip(rows, images):
            digest = hashlib.sha256(payload).hexdigest()
            if digest != row["sha256"]:
                raise ValidationError(
                    f"capture_id already exists with different image content at position {row['position']}"
                )

    def list_captures(
        self,
        *,
        status: str | None = None,
        station_id: str | None = None,
        capture_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """分页列出抓拍摘要，供页面、导出和问题排查使用。"""

        clauses: list[str] = []
        parameters: list[Any] = []
        if status:
            clauses.append("c.status = ?")
            parameters.append(status)
        if station_id:
            clauses.append("c.station_id = ?")
            parameters.append(station_id)
        if capture_id:
            clauses.append("instr(lower(c.capture_id), lower(?)) > 0")
            parameters.append(capture_id)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) AS count FROM captures c {where_sql}",
                parameters,
            ).fetchone()["count"]
            rows = connection.execute(
                f"""
                SELECT
                    c.capture_id, c.capture_time, c.received_at, c.station_id,
                    c.camera_id, c.status, c.error,
                    COUNT(DISTINCT i.id) AS image_count,
                    COUNT(DISTINCT o.id) AS object_count,
                    MAX(CASE WHEN x.active = 1 THEN 1 ELSE 0 END) AS manually_corrected
                FROM captures c
                LEFT JOIN images i ON i.capture_id = c.capture_id
                LEFT JOIN objects o ON o.capture_id = c.capture_id
                LEFT JOIN corrections x ON x.capture_id = c.capture_id
                {where_sql}
                GROUP BY c.capture_id
                ORDER BY c.capture_time DESC, c.received_at DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
        return {
            "items": [
                {
                    **dict(row),
                    "manually_corrected": bool(row["manually_corrected"]),
                }
                for row in rows
            ],
            "total": int(total),
            "limit": limit,
            "offset": offset,
        }

    def save_correction(
        self,
        capture_id: str,
        corrected_result: dict[str, Any],
        *,
        operator: str,
        reason: str | None,
    ) -> None:
        """追加人工修正审计记录，并仅激活最新的一条修正。"""

        if not self.capture_exists(capture_id):
            raise CaptureNotFoundError(capture_id)
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                "UPDATE corrections SET active = 0 WHERE capture_id = ?",
                (capture_id,),
            )
            connection.execute(
                """
                INSERT INTO corrections (
                    capture_id, created_at, operator, reason,
                    corrected_result_json, active
                ) VALUES (?, ?, ?, ?, ?, 1)
                """,
                (
                    capture_id,
                    now,
                    operator,
                    reason,
                    json.dumps(corrected_result, ensure_ascii=False),
                ),
            )

    def registered_image_path(self, capture_id: str, position: int) -> Path:
        """返回数据库登记的原图路径，不接受任意文件系统路径。"""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT stored_path FROM images
                WHERE capture_id = ? AND position = ?
                """,
                (capture_id, position),
            ).fetchone()
        if row is None:
            raise CaptureNotFoundError(f"{capture_id}:{position}")
        return Path(row["stored_path"])

    def annotated_image_path(self, capture_id: str) -> Path:
        """返回结果中登记的标注图路径。"""

        capture = self.get_capture(capture_id)
        result = capture.get("result") or {}
        value = result.get("annotated_image")
        if not value:
            raise CaptureNotFoundError(f"{capture_id}:annotated")
        return Path(str(value))

    def health(self) -> dict[str, Any]:
        """返回数据库位置及各处理状态的任务计数。"""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'processed' THEN 1 ELSE 0 END) AS processed,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
                FROM captures
                """
            ).fetchone()
        return {
            "database": str(self.database_path),
            "captures_total": int(row["total"] or 0),
            "captures_processed": int(row["processed"] or 0),
            "captures_failed": int(row["failed"] or 0),
        }
