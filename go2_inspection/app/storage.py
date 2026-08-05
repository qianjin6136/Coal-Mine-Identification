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
from .errors import BatchStateConflictError, CaptureNotFoundError, ValidationError


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
        self.imported_batches_root = self.storage_root / "imported_batches"
        for path in (
            self.database_path.parent,
            self.incoming_root,
            self.processed_root,
            self.evidence_root,
            self.imported_batches_root,
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

                CREATE TABLE IF NOT EXISTS offline_batches (
                    batch_id TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    queued_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    sensor_status TEXT NOT NULL DEFAULT 'pending',
                    capture_total INTEGER NOT NULL DEFAULT 0,
                    capture_succeeded INTEGER NOT NULL DEFAULT 0,
                    capture_failed INTEGER NOT NULL DEFAULT 0,
                    gas_row_count INTEGER NOT NULL DEFAULT 0,
                    thermal_frame_count INTEGER NOT NULL DEFAULT 0,
                    warning_count INTEGER NOT NULL DEFAULT 0,
                    diagnostics_json TEXT NOT NULL DEFAULT '[]',
                    detection_confirmed_at TEXT,
                    report_confirmed_at TEXT,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS batch_capture_items (
                    batch_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    capture_id TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    PRIMARY KEY(batch_id, relative_path),
                    UNIQUE(batch_id, capture_id),
                    FOREIGN KEY(batch_id) REFERENCES offline_batches(batch_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sensor_samples (
                    batch_id TEXT NOT NULL,
                    sample_key TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    sample_id TEXT NOT NULL,
                    ch4_value REAL,
                    ch4_unit TEXT,
                    ch4_status TEXT,
                    o2_value REAL,
                    o2_unit TEXT,
                    o2_status TEXT,
                    co_value REAL,
                    co_unit TEXT,
                    co_status TEXT,
                    h2s_value REAL,
                    h2s_unit TEXT,
                    h2s_status TEXT,
                    gas_error TEXT,
                    raw_row_json TEXT,
                    thermal_stored_path TEXT,
                    thermal_sha256 TEXT,
                    warning TEXT,
                    PRIMARY KEY(batch_id, sample_key),
                    FOREIGN KEY(batch_id) REFERENCES offline_batches(batch_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_captures_status
                ON captures(status);
                CREATE INDEX IF NOT EXISTS idx_captures_station
                ON captures(station_id);
                CREATE INDEX IF NOT EXISTS idx_corrections_capture
                ON corrections(capture_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_offline_batches_status
                ON offline_batches(status, queued_at);
                CREATE INDEX IF NOT EXISTS idx_batch_capture_id
                ON batch_capture_items(capture_id);
                CREATE INDEX IF NOT EXISTS idx_batch_capture_status
                ON batch_capture_items(batch_id, status);
                CREATE INDEX IF NOT EXISTS idx_sensor_samples_time
                ON sensor_samples(batch_id, captured_at);
                """
            )
            self._ensure_column(
                connection,
                "offline_batches",
                "detection_confirmed_at",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "offline_batches",
                "report_confirmed_at",
                "TEXT",
            )
            # 旧版本中的 queued/running 表示已自动进入识别。升级到确认制后，
            # 未留下确认时间的任务必须退回待确认，不能在服务启动时继续运行。
            connection.execute(
                """
                UPDATE batch_capture_items SET status = 'pending'
                WHERE status = 'running'
                  AND batch_id IN (
                      SELECT batch_id FROM offline_batches
                      WHERE status = 'running' AND detection_confirmed_at IS NULL
                  )
                """
            )
            connection.execute(
                """
                UPDATE offline_batches
                SET status = 'awaiting_detection_confirmation',
                    started_at = NULL, finished_at = NULL, error = NULL
                WHERE status IN ('queued', 'running')
                  AND detection_confirmed_at IS NULL
                """
            )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        """为既有 SQLite 库幂等补列。表名和字段声明只由代码常量提供。"""

        columns = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
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
            batch_rows = connection.execute(
                """
                SELECT DISTINCT batch_id FROM batch_capture_items
                WHERE capture_id = ?
                """,
                (capture_id,),
            ).fetchall()
            batch_ids = [str(row["batch_id"]) for row in batch_rows]
            connection.execute(
                """
                UPDATE batch_capture_items
                SET status = 'failed', error = 'capture deleted by user'
                WHERE capture_id = ?
                """,
                (capture_id,),
            )
            for batch_id in batch_ids:
                connection.execute(
                    """
                    UPDATE offline_batches
                    SET capture_succeeded = (
                            SELECT COUNT(*) FROM batch_capture_items
                            WHERE batch_id = ? AND status = 'succeeded'
                        ),
                        capture_failed = (
                            SELECT COUNT(*) FROM batch_capture_items
                            WHERE batch_id = ? AND status = 'failed'
                        ),
                        status = CASE
                            WHEN EXISTS (
                                SELECT 1 FROM batch_capture_items
                                WHERE batch_id = ? AND status = 'succeeded'
                            ) THEN 'completed_with_errors'
                            ELSE 'failed'
                        END,
                        report_confirmed_at = NULL
                    WHERE batch_id = ?
                    """,
                    (batch_id, batch_id, batch_id, batch_id),
                )
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
            "affected_batch_ids": batch_ids,
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
            batch_rows = connection.execute(
                """
                SELECT batch_id FROM batch_capture_items
                WHERE capture_id = ? AND status = 'succeeded'
                ORDER BY batch_id
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
            "source_batch_id": (
                batch_rows[0]["batch_id"] if batch_rows else None
            ),
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
        source_batch_id: str | None = None,
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
        if source_batch_id:
            clauses.append(
                "EXISTS (SELECT 1 FROM batch_capture_items b "
                "WHERE b.capture_id = c.capture_id AND b.batch_id = ? "
                "AND b.status = 'succeeded')"
            )
            parameters.append(source_batch_id)
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
                    MIN(b.batch_id) AS source_batch_id,
                    COUNT(DISTINCT i.id) AS image_count,
                    COUNT(DISTINCT o.id) AS object_count,
                    MAX(CASE WHEN x.active = 1 THEN 1 ELSE 0 END) AS manually_corrected
                FROM captures c
                LEFT JOIN images i ON i.capture_id = c.capture_id
                LEFT JOIN objects o ON o.capture_id = c.capture_id
                LEFT JOIN corrections x ON x.capture_id = c.capture_id
                LEFT JOIN batch_capture_items b
                    ON b.capture_id = c.capture_id AND b.status = 'succeeded'
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

    def effective_results(
        self, capture_ids: Sequence[str]
    ) -> dict[str, dict[str, Any] | None]:
        """批量读取列表页所需的有效结果，避免逐条查询数据库。"""

        unique_ids = list(dict.fromkeys(capture_ids))
        if not unique_ids:
            return {}
        placeholders = ", ".join("?" for _ in unique_ids)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    c.capture_id,
                    c.result_json,
                    (
                        SELECT x.corrected_result_json
                        FROM corrections x
                        WHERE x.capture_id = c.capture_id AND x.active = 1
                        ORDER BY x.id DESC
                        LIMIT 1
                    ) AS corrected_result_json
                FROM captures c
                WHERE c.capture_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
        results: dict[str, dict[str, Any] | None] = {}
        for row in rows:
            payload = row["corrected_result_json"] or row["result_json"]
            results[row["capture_id"]] = json.loads(payload) if payload else None
        return results

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

    def offline_batch_exists(self, batch_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM offline_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
        return row is not None

    def create_offline_batch(
        self,
        batch_id: str,
        source_path: Path,
        items: Sequence[dict[str, Any]],
        *,
        gas_row_count: int,
        thermal_frame_count: int,
        diagnostics: Sequence[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        """原子登记一个经过预检的离线批次及其抓拍处理清单。"""

        now = datetime.now(timezone.utc).isoformat()
        failed_count = sum(1 for item in items if item.get("error"))
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO offline_batches (
                        batch_id, source_path, discovered_at, queued_at, status,
                        capture_total, capture_failed, gas_row_count,
                        thermal_frame_count, warning_count, diagnostics_json
                    ) VALUES (?, ?, ?, ?, 'awaiting_detection_confirmation', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        str(Path(source_path).resolve()),
                        now,
                        now,
                        len(items),
                        failed_count,
                        gas_row_count,
                        thermal_frame_count,
                        len(diagnostics),
                        json.dumps(list(diagnostics), ensure_ascii=False),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValidationError(f"offline batch already exists: {batch_id}") from exc
            connection.executemany(
                """
                INSERT INTO batch_capture_items (
                    batch_id, relative_path, capture_id, status, error
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        batch_id,
                        str(item["relative_path"]),
                        item.get("capture_id"),
                        "failed" if item.get("error") else "pending",
                        item.get("error"),
                    )
                    for item in items
                ],
            )
        return self.get_offline_batch(batch_id)

    def list_offline_batches(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT o.*,
                    (
                        SELECT i.error FROM batch_capture_items i
                        WHERE i.batch_id = o.batch_id
                          AND i.status = 'failed' AND i.error IS NOT NULL
                        ORDER BY i.relative_path LIMIT 1
                    ) AS first_item_error
                FROM offline_batches o
                ORDER BY o.discovered_at DESC
                """
            ).fetchall()
        return [self._offline_batch_dict(row) for row in rows]

    def get_offline_batch(
        self, batch_id: str, *, include_items: bool = True
    ) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM offline_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            items = (
                connection.execute(
                    """
                    SELECT relative_path, capture_id, status, error
                    FROM batch_capture_items
                    WHERE batch_id = ?
                    ORDER BY relative_path
                    """,
                    (batch_id,),
                ).fetchall()
                if include_items and row is not None
                else []
            )
        if row is None:
            raise CaptureNotFoundError(batch_id)
        result = self._offline_batch_dict(row)
        if include_items:
            result["items"] = [dict(item) for item in items]
        return result

    @staticmethod
    def _offline_batch_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        try:
            result["diagnostics"] = json.loads(result.pop("diagnostics_json"))
        except (json.JSONDecodeError, TypeError):
            result["diagnostics"] = []
            result.pop("diagnostics_json", None)
        total = int(result.get("capture_total") or 0)
        succeeded = int(result.get("capture_succeeded") or 0)
        failed = int(result.get("capture_failed") or 0)
        result["capture_pending"] = max(0, total - succeeded - failed)
        result["progress_percent"] = (
            round((succeeded + failed) * 100 / total, 1) if total else 0.0
        )
        terminal = result.get("status") in {
            "completed",
            "completed_with_errors",
            "failed",
        }
        result["can_start_detection"] = (
            result.get("status") == "awaiting_detection_confirmation"
        )
        result["can_confirm_report"] = (
            terminal and not result.get("report_confirmed_at")
        )
        result["report_available"] = (
            terminal and bool(result.get("report_confirmed_at"))
        )
        return result

    def confirm_offline_batch_detection(self, batch_id: str) -> dict[str, Any]:
        """确认并原子地把预检批次放入后台队列；重复请求保持幂等。"""

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, detection_confirmed_at
                FROM offline_batches WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
            if row is None:
                raise CaptureNotFoundError(batch_id)
            status = str(row["status"])
            if status == "awaiting_detection_confirmation":
                now = datetime.now(timezone.utc).isoformat()
                connection.execute(
                    """
                    UPDATE offline_batches
                    SET status = 'queued', queued_at = ?,
                        detection_confirmed_at = ?, report_confirmed_at = NULL,
                        started_at = NULL, finished_at = NULL, error = NULL
                    WHERE batch_id = ?
                    """,
                    (now, now, batch_id),
                )
            elif row["detection_confirmed_at"] is None:
                raise BatchStateConflictError(
                    f"offline batch cannot start detection from status: {status}"
                )
            # 已确认且已排队、运行或完成时直接返回，避免双击造成重复任务。
        return self.get_offline_batch(batch_id)

    def confirm_offline_batch_report(self, batch_id: str) -> dict[str, Any]:
        """确认当前终态结果可以生成报告；重复请求保持幂等。"""

        terminal = {"completed", "completed_with_errors", "failed"}
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, report_confirmed_at
                FROM offline_batches WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
            if row is None:
                raise CaptureNotFoundError(batch_id)
            status = str(row["status"])
            if status not in terminal:
                raise BatchStateConflictError(
                    f"offline batch results cannot be confirmed from status: {status}"
                )
            if row["report_confirmed_at"] is None:
                connection.execute(
                    """
                    UPDATE offline_batches SET report_confirmed_at = ?
                    WHERE batch_id = ?
                    """,
                    (datetime.now(timezone.utc).isoformat(), batch_id),
                )
        return self.get_offline_batch(batch_id)

    def invalidate_report_confirmation_for_capture(self, capture_id: str) -> list[str]:
        """抓拍结果变化时撤销其所属批次的报告确认。"""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT batch_id FROM batch_capture_items
                WHERE capture_id = ?
                """,
                (capture_id,),
            ).fetchall()
            batch_ids = [str(row["batch_id"]) for row in rows]
            connection.execute(
                """
                UPDATE offline_batches SET report_confirmed_at = NULL
                WHERE batch_id IN (
                    SELECT batch_id FROM batch_capture_items WHERE capture_id = ?
                )
                """,
                (capture_id,),
            )
        return batch_ids

    def reset_interrupted_offline_batches(self) -> int:
        """服务重启后把处理中批次和单项恢复到可领取状态。"""

        with self._connection() as connection:
            connection.execute(
                """
                UPDATE batch_capture_items SET status = 'pending'
                WHERE status = 'running'
                  AND batch_id IN (
                      SELECT batch_id FROM offline_batches WHERE status = 'running'
                  )
                """
            )
            cursor = connection.execute(
                """
                UPDATE offline_batches
                SET status = 'queued', started_at = NULL, finished_at = NULL,
                    error = NULL,
                    sensor_status = CASE
                        WHEN sensor_status = 'running' THEN 'pending'
                        ELSE sensor_status
                    END
                WHERE status = 'running'
                """
            )
        return int(cursor.rowcount)

    def claim_next_offline_batch(self) -> dict[str, Any] | None:
        """以 SQLite 写锁原子领取最早排队的批次。"""

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT batch_id FROM offline_batches
                WHERE status = 'queued'
                ORDER BY queued_at, batch_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                UPDATE offline_batches
                SET status = 'running', started_at = ?, finished_at = NULL, error = NULL
                WHERE batch_id = ?
                """,
                (now, row["batch_id"]),
            )
        return self.get_offline_batch(row["batch_id"], include_items=False)

    def pending_offline_items(self, batch_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT relative_path, capture_id, status, error
                FROM batch_capture_items
                WHERE batch_id = ? AND status = 'pending'
                ORDER BY relative_path
                """,
                (batch_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_offline_item(
        self,
        batch_id: str,
        relative_path: str,
        *,
        status: str,
        capture_id: str | None = None,
        error: str | None = None,
    ) -> None:
        if status not in {"pending", "running", "succeeded", "failed"}:
            raise ValidationError(f"invalid offline item status: {status}")
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE batch_capture_items
                SET status = ?, capture_id = COALESCE(?, capture_id), error = ?
                WHERE batch_id = ? AND relative_path = ?
                """,
                (status, capture_id, error, batch_id, relative_path),
            )
        self.refresh_offline_batch_counts(batch_id)

    def refresh_offline_batch_counts(self, batch_id: str) -> None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
                FROM batch_capture_items WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
            connection.execute(
                """
                UPDATE offline_batches
                SET capture_succeeded = ?, capture_failed = ?
                WHERE batch_id = ?
                """,
                (int(row["succeeded"] or 0), int(row["failed"] or 0), batch_id),
            )

    def replace_sensor_samples(
        self,
        batch_id: str,
        samples: Sequence[dict[str, Any]],
        *,
        gas_row_count: int,
        thermal_frame_count: int,
        diagnostics: Sequence[dict[str, Any]],
    ) -> None:
        columns = (
            "batch_id", "sample_key", "captured_at", "sample_id",
            "ch4_value", "ch4_unit", "ch4_status",
            "o2_value", "o2_unit", "o2_status",
            "co_value", "co_unit", "co_status",
            "h2s_value", "h2s_unit", "h2s_status",
            "gas_error", "raw_row_json", "thermal_stored_path",
            "thermal_sha256", "warning",
        )
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM sensor_samples WHERE batch_id = ?", (batch_id,)
            )
            connection.executemany(
                f"INSERT INTO sensor_samples ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                [
                    tuple(
                        batch_id if column == "batch_id" else sample.get(column)
                        for column in columns
                    )
                    for sample in samples
                ],
            )
            existing = connection.execute(
                "SELECT diagnostics_json FROM offline_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            prior = json.loads(existing["diagnostics_json"]) if existing else []
            combined = [*prior, *diagnostics]
            connection.execute(
                """
                UPDATE offline_batches
                SET sensor_status = 'completed', gas_row_count = ?,
                    thermal_frame_count = ?, warning_count = ?,
                    diagnostics_json = ?
                WHERE batch_id = ?
                """,
                (
                    gas_row_count,
                    thermal_frame_count,
                    len(combined),
                    json.dumps(combined, ensure_ascii=False),
                    batch_id,
                ),
            )

    def mark_sensor_import_running(self, batch_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE offline_batches SET sensor_status = 'running' WHERE batch_id = ?",
                (batch_id,),
            )

    def sensor_samples_for_batch(self, batch_id: str) -> list[dict[str, Any]]:
        """按时间返回结构化传感器样本，供后续气体/热像模块直接消费。"""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sensor_samples
                WHERE batch_id = ?
                ORDER BY captured_at, sample_key
                """,
                (batch_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            raw = item.pop("raw_row_json", None)
            item["raw_gas_row"] = json.loads(raw) if raw else None
            result.append(item)
        return result

    def mark_sensor_import_failed(self, batch_id: str, error: str) -> None:
        diagnostic = {"scope": "sensors", "level": "error", "message": error[:2000]}
        with self._connection() as connection:
            row = connection.execute(
                "SELECT diagnostics_json FROM offline_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            diagnostics = json.loads(row["diagnostics_json"]) if row else []
            diagnostics.append(diagnostic)
            connection.execute(
                """
                UPDATE offline_batches
                SET sensor_status = 'failed', warning_count = ?, diagnostics_json = ?
                WHERE batch_id = ?
                """,
                (len(diagnostics), json.dumps(diagnostics, ensure_ascii=False), batch_id),
            )

    def finish_offline_batch(self, batch_id: str, error: str | None = None) -> dict[str, Any]:
        self.refresh_offline_batch_counts(batch_id)
        batch = self.get_offline_batch(batch_id, include_items=False)
        succeeded = int(batch["capture_succeeded"])
        failed = int(batch["capture_failed"])
        if error or (succeeded == 0 and failed > 0):
            status = "failed"
        elif failed or batch["sensor_status"] == "failed" or batch["warning_count"]:
            status = "completed_with_errors"
        else:
            status = "completed"
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE offline_batches
                SET status = ?, finished_at = ?, error = ?
                WHERE batch_id = ?
                """,
                (status, now, error[:2000] if error else None, batch_id),
            )
        return self.get_offline_batch(batch_id)

    def retry_offline_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self.get_offline_batch(batch_id, include_items=False)
        if batch["status"] in {"awaiting_detection_confirmation", "queued", "running"}:
            return self.get_offline_batch(batch_id)
        diagnostics = list(batch.get("diagnostics") or [])
        if batch["sensor_status"] == "failed":
            diagnostics = [
                item for item in diagnostics
                if item.get("scope") not in {"sensors", "gas", "thermal"}
            ]
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE batch_capture_items
                SET status = 'pending', error = NULL
                WHERE batch_id = ? AND status = 'failed'
                """,
                (batch_id,),
            )
            connection.execute(
                """
                UPDATE offline_batches
                SET status = 'awaiting_detection_confirmation', started_at = NULL,
                    finished_at = NULL, error = NULL,
                    detection_confirmed_at = NULL, report_confirmed_at = NULL,
                    warning_count = ?, diagnostics_json = ?,
                    sensor_status = CASE
                        WHEN sensor_status = 'failed' THEN 'pending'
                        ELSE sensor_status
                    END
                WHERE batch_id = ?
                """,
                (
                    len(diagnostics),
                    json.dumps(diagnostics, ensure_ascii=False),
                    batch_id,
                ),
            )
        self.refresh_offline_batch_counts(batch_id)
        return self.get_offline_batch(batch_id)

    def requeue_running_offline_batch(self, batch_id: str) -> None:
        """在服务正常关闭时保留已完成项，并把当前批次放回队列。"""

        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE batch_capture_items SET status = 'pending'
                WHERE batch_id = ? AND status = 'running'
                """,
                (batch_id,),
            )
            connection.execute(
                """
                UPDATE offline_batches
                SET status = 'queued', queued_at = ?, started_at = NULL,
                    finished_at = NULL, error = NULL,
                    sensor_status = CASE
                        WHEN sensor_status = 'running' THEN 'pending'
                        ELSE sensor_status
                    END
                WHERE batch_id = ? AND status = 'running'
                """,
                (now, batch_id),
            )

    def offline_queue_counts(self) -> dict[str, int]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued,
                    SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running
                FROM offline_batches
                """
            ).fetchone()
        return {
            "offline_batches_queued": int(row["queued"] or 0),
            "offline_batches_running": int(row["running"] or 0),
        }

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
            **self.offline_queue_counts(),
        }
