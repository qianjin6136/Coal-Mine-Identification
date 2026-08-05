"""GO2 抓拍上传与结果查询的 FastAPI 适配层。"""

from __future__ import annotations

import os
import csv
import io
import json
from pathlib import Path
from urllib.parse import quote

try:
    from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
    from fastapi.responses import FileResponse, HTMLResponse, Response
    from fastapi.staticfiles import StaticFiles
except ImportError as exc:  # pragma: no cover - 仅在尚未安装依赖时触发
    raise RuntimeError(
        "FastAPI dependencies are missing. Run: pip install -r requirements.txt"
    ) from exc

from .domain import CaptureMetadata
from .errors import (
    BatchStateConflictError,
    CaptureNotFoundError,
    InspectionError,
    ReportNotReadyError,
)
from .factory import build_service
from .ui import inspection_ui_html


def create_app() -> FastAPI:
    """创建应用；可通过环境变量切换测试或现场配置文件。"""

    settings_path = os.environ.get("GO2_INSPECTION_SETTINGS")
    service = build_service(settings_path)
    app = FastAPI(
        title="GO2 Inspection API",
        version="0.1.0",
        description="Receives GO2 images and capture poses for laptop-side inspection.",
    )
    static_root = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_root), name="static")
    app.router.add_event_handler("startup", service.start_background_services)
    app.router.add_event_handler("shutdown", service.stop_background_services)

    @app.get("/health")
    def health() -> dict[str, object]:
        return service.health()

    @app.get("/", response_class=HTMLResponse)
    @app.get("/ui", response_class=HTMLResponse)
    def ui() -> str:
        return inspection_ui_html()

    @app.get("/api/v1/runtime-settings")
    def get_runtime_settings() -> dict[str, object]:
        try:
            return service.get_runtime_settings()
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/v1/runtime-settings")
    def update_runtime_settings(
        patch: dict[str, object] = Body(...),
    ) -> dict[str, object]:
        try:
            return service.update_runtime_settings(patch)
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/runtime-settings/reset")
    def reset_runtime_settings() -> dict[str, object]:
        try:
            return service.reset_runtime_settings()
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/offline-batches")
    def list_offline_batches() -> dict[str, object]:
        try:
            return service.list_offline_batches()
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/offline-batches/{batch_id}/import")
    def import_offline_batch(batch_id: str) -> dict[str, object]:
        try:
            return service.queue_offline_batch(batch_id)
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/offline-batches/{batch_id}/confirm-detection")
    def confirm_offline_batch_detection(batch_id: str) -> dict[str, object]:
        try:
            return service.confirm_offline_batch_detection(batch_id)
        except CaptureNotFoundError as exc:
            raise HTTPException(status_code=404, detail="offline batch not found") from exc
        except BatchStateConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/offline-batches/{batch_id}/confirm-report")
    def confirm_offline_batch_report(batch_id: str) -> dict[str, object]:
        try:
            return service.confirm_offline_batch_report(batch_id)
        except CaptureNotFoundError as exc:
            raise HTTPException(status_code=404, detail="offline batch not found") from exc
        except BatchStateConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/offline-batches/{batch_id}")
    def get_offline_batch(batch_id: str) -> dict[str, object]:
        try:
            return service.get_offline_batch(batch_id)
        except CaptureNotFoundError as exc:
            raise HTTPException(status_code=404, detail="offline batch not found") from exc
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/offline-batches/{batch_id}/report.docx")
    def download_offline_batch_report(batch_id: str) -> Response:
        try:
            payload = service.generate_offline_batch_report(batch_id)
        except CaptureNotFoundError as exc:
            raise HTTPException(status_code=404, detail="offline batch not found") from exc
        except ReportNotReadyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        filename = f"GO2_巡检报告_{batch_id}.docx"
        encoded = quote(filename)
        return Response(
            content=payload,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            headers={
                "Content-Disposition": (
                    f"attachment; filename=GO2_report_{batch_id}.docx; "
                    f"filename*=UTF-8''{encoded}"
                )
            },
        )

    @app.post("/api/v1/offline-batches/{batch_id}/retry")
    def retry_offline_batch(batch_id: str) -> dict[str, object]:
        try:
            return service.retry_offline_batch(batch_id)
        except CaptureNotFoundError as exc:
            raise HTTPException(status_code=404, detail="offline batch not found") from exc
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/captures")
    async def create_capture(
        metadata: str = Form(...),
        images: list[UploadFile] = File(...),
    ) -> dict[str, object]:
        try:
            parsed = CaptureMetadata.from_json(metadata)
            # UploadFile 的读取是异步 I/O；进入服务层后统一使用不可变的字节数据。
            payloads = [
                (upload.filename or f"frame_{index + 1:02d}.jpg", await upload.read())
                for index, upload in enumerate(images)
            ]
            return service.ingest_capture(parsed, payloads)
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/v1/captures")
    def list_captures(
        status: str | None = None,
        station_id: str | None = None,
        capture_id: str | None = None,
        batch_id: str | None = None,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, object]:
        try:
            return service.list_captures(
                status=status,
                station_id=station_id,
                capture_id=capture_id,
                source_batch_id=batch_id,
                limit=limit,
                offset=offset,
            )
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/export")
    def export_results(
        format: str = Query("json", pattern="^(json|csv)$"),
        status: str | None = None,
        station_id: str | None = None,
        batch_id: str | None = None,
    ) -> Response:
        try:
            rows = service.export_captures(
                status=status,
                station_id=station_id,
                source_batch_id=batch_id,
            )
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if format == "json":
            payload = json.dumps(rows, ensure_ascii=False, indent=2)
            return Response(
                content=payload,
                media_type="application/json",
                headers={
                    "Content-Disposition": 'attachment; filename="go2_results.json"'
                },
            )
        stream = io.StringIO(newline="")
        fieldnames = list(rows[0]) if rows else [
            "capture_id",
            "capture_time",
            "station_id",
            "source_batch_id",
            "status",
        ]
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (
                        json.dumps(value, ensure_ascii=False)
                        if isinstance(value, (dict, list))
                        else value
                    )
                    for key, value in row.items()
                }
            )
        return Response(
            content="\ufeff" + stream.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="go2_results.csv"'
            },
        )

    @app.get("/api/v1/results/{capture_id}")
    def get_result(capture_id: str) -> dict[str, object]:
        try:
            return service.get_capture(capture_id)
        except CaptureNotFoundError as exc:
            raise HTTPException(status_code=404, detail="capture not found") from exc

    @app.delete("/api/v1/captures/{capture_id}")
    def delete_capture(capture_id: str) -> dict[str, object]:
        try:
            return service.delete_capture(capture_id)
        except CaptureNotFoundError as exc:
            raise HTTPException(status_code=404, detail="capture not found") from exc

    @app.patch("/api/v1/results/{capture_id}/correction")
    def correct_result(
        capture_id: str,
        correction: dict[str, object] = Body(...),
    ) -> dict[str, object]:
        try:
            return service.correct_capture(capture_id, correction)
        except CaptureNotFoundError as exc:
            raise HTTPException(status_code=404, detail="capture not found") from exc
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/results/{capture_id}/reprocess")
    def reprocess_result(capture_id: str) -> dict[str, object]:
        try:
            return service.reprocess_capture(capture_id)
        except CaptureNotFoundError as exc:
            raise HTTPException(status_code=404, detail="capture not found") from exc
        except InspectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/v1/captures/{capture_id}/images/{position}")
    def get_original_image(capture_id: str, position: int) -> FileResponse:
        try:
            path = service.repository.registered_image_path(capture_id, position)
        except CaptureNotFoundError as exc:
            raise HTTPException(status_code=404, detail="image not found") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="image file missing")
        return FileResponse(path)

    @app.get("/api/v1/captures/{capture_id}/annotated")
    def get_annotated_image(capture_id: str) -> FileResponse:
        try:
            path = service.repository.annotated_image_path(capture_id)
        except CaptureNotFoundError as exc:
            raise HTTPException(status_code=404, detail="annotated image not found") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="annotated image file missing")
        return FileResponse(path)

    return app


# 保留模块级实例，供 ``uvicorn app.api:app`` 直接启动。
app = create_app()
