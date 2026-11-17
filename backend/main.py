from __future__ import annotations

import hmac
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db.repository import GroupByPeriod, SnapshotRepository
from .extraction import get_extractor, normalize_extracted_json

ROOT_DIR = Path(__file__).resolve().parents[1]


def _load_dotenv_if_present() -> None:
    env_file = ROOT_DIR / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv_if_present()


def get_images_dir() -> Path:
    env_dir = os.getenv("FUELV_IMAGES_DIR")
    path = Path(env_dir) if env_dir else ROOT_DIR / "data" / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


class CreateSnapshotRequest(BaseModel):
    captured_at: str | None = Field(
        default=None,
        description="ISO-8601 timestamp of snapshot capture. Defaults to current time if omitted.",
    )
    odometer_reading: float = Field(..., ge=0, description="Confirmed odometer reading")
    total_expense: float = Field(..., ge=0, description="Confirmed total fuel expense")
    fuel_dispensed: float = Field(..., ge=0, description="Confirmed volume of fuel dispensed")
    odo_image_path: str | None = None
    pump_image_path: str | None = None
    entry_mode: str | None = None
    extractor_model: str | None = None
    raw_extraction_json: dict[str, Any] | None = None
    extra_data: dict[str, Any] = Field(default_factory=dict)


def verify_write_access(
    x_fuelv_key: str | None = Header(default=None, alias="X-FuelV-Key"),
) -> None:
    expected_key = os.getenv("FUELV_WRITE_KEY", "").strip()
    if not expected_key:
        return
    if not x_fuelv_key:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: valid write access key (UUID) required.",
        )
    try:
        parsed_key = str(uuid.UUID(x_fuelv_key.strip())).lower()
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: write access key must be a valid UUID.",
        )
    try:
        expected_uuid = str(uuid.UUID(expected_key)).lower()
    except (ValueError, AttributeError):
        expected_uuid = expected_key.lower()

    if not hmac.compare_digest(parsed_key, expected_uuid):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: invalid write access key.",
        )


def create_app(
    db_path: str | Path | None = None,
    images_dir: str | Path | None = None,
) -> FastAPI:
    app = FastAPI(
        title="FuelV API",
        description="Snapshot-based fuel log backend with swappable vision extraction and SQLite3 storage.",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    repo = SnapshotRepository(db_path=db_path)
    img_dir = Path(images_dir) if images_dir else get_images_dir()
    img_dir.mkdir(parents=True, exist_ok=True)

    app.state.repo = repo
    app.state.images_dir = img_dir

    app.mount("/api/images", StaticFiles(directory=str(img_dir)), name="images")

    @app.get("/api/health")
    def health_check() -> dict[str, Any]:
        extractor = get_extractor()
        backend = os.getenv("EXTRACTOR_BACKEND", "gemini").strip().lower()
        return {
            "status": "ok",
            "extractor_backend": backend,
            "extractor_model": extractor.model_id,
            "gemini_key_configured": bool(os.getenv("GEMINI_API_KEY", "").strip()),
            "write_protection_enabled": bool(os.getenv("FUELV_WRITE_KEY", "").strip()),
        }

    @app.post("/api/extract", dependencies=[Depends(verify_write_access)])
    async def extract_from_snapshots(
        odo_image: UploadFile | None = File(default=None),
        pump_image: UploadFile | None = File(default=None),
    ) -> dict[str, Any]:
        if odo_image is None and pump_image is None:
            raise HTTPException(
                status_code=400,
                detail="At least one image (odo_image or pump_image) must be provided.",
            )

        batch_prefix = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        odo_bytes: bytes | None = None
        odo_mime: str | None = None
        odo_rel_path: str | None = None

        pump_bytes: bytes | None = None
        pump_mime: str | None = None
        pump_rel_path: str | None = None

        if odo_image is not None:
            odo_bytes = await odo_image.read()
            odo_mime = odo_image.content_type or "image/jpeg"
            ext = Path(odo_image.filename or "odo.jpg").suffix or ".jpg"
            odo_filename = f"{batch_prefix}_odo{ext}"
            (app.state.images_dir / odo_filename).write_bytes(odo_bytes)
            odo_rel_path = f"images/{odo_filename}"

        if pump_image is not None:
            pump_bytes = await pump_image.read()
            pump_mime = pump_image.content_type or "image/jpeg"
            ext = Path(pump_image.filename or "pump.jpg").suffix or ".jpg"
            pump_filename = f"{batch_prefix}_pump{ext}"
            (app.state.images_dir / pump_filename).write_bytes(pump_bytes)
            pump_rel_path = f"images/{pump_filename}"

        extractor = get_extractor()
        warning_msg: str | None = None

        try:
            extracted_json = extractor.extract(
                odo_bytes=odo_bytes,
                odo_mime=odo_mime,
                pump_bytes=pump_bytes,
                pump_mime=pump_mime,
            )
        except Exception as exc:
            # Graceful fallback: preserve uploaded images and return null extraction values
            # so the user can enter the numbers manually without losing their snapshot photos.
            extracted_json = normalize_extracted_json(None)
            warning_msg = str(exc)

        return {
            "extraction": extracted_json,
            "meta": {
                "extractor_model": extractor.model_id,
                "odo_image_path": odo_rel_path,
                "pump_image_path": pump_rel_path,
                "odo_image_url": f"/api/images/{Path(odo_rel_path).name}" if odo_rel_path else None,
                "pump_image_url": f"/api/images/{Path(pump_rel_path).name}" if pump_rel_path else None,
                "warning": warning_msg,
            },
        }

    @app.post("/api/snapshots", status_code=201, dependencies=[Depends(verify_write_access)])
    def create_snapshot(payload: CreateSnapshotRequest) -> dict[str, Any]:
        record = app.state.repo.insert_snapshot(
            captured_at=payload.captured_at,
            odometer_reading=payload.odometer_reading,
            total_expense=payload.total_expense,
            fuel_dispensed=payload.fuel_dispensed,
            odo_image_path=payload.odo_image_path,
            pump_image_path=payload.pump_image_path,
            entry_mode=payload.entry_mode,
            extractor_model=payload.extractor_model,
            raw_extraction_json=payload.raw_extraction_json,
            extra_data=payload.extra_data,
        )
        return record

    @app.get("/api/snapshots/grouped")
    def list_grouped_snapshots(
        by: GroupByPeriod = Query(default="month", description="Group snapshots by week, month, or year"),
    ) -> dict[str, Any]:
        return app.state.repo.get_grouped_snapshots(group_by=by)

    @app.delete("/api/snapshots/{snapshot_id}", dependencies=[Depends(verify_write_access)])
    def remove_snapshot(snapshot_id: str) -> dict[str, Any]:
        deleted = app.state.repo.delete_snapshot(snapshot_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        return {"deleted": True, "id": snapshot_id}

    # Serve built Vue 3 SPA (frontend/dist) when present (e.g., in production Docker container)
    web_dist = ROOT_DIR / "frontend" / "dist"
    index_html = web_dist / "index.html"
    if index_html.is_file():
        assets_dir = web_dist / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="web_assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str) -> FileResponse:
            candidate = (web_dist / full_path).resolve()
            if full_path and candidate.is_file() and str(candidate).startswith(str(web_dist.resolve())):
                return FileResponse(candidate)
            return FileResponse(index_html)

    return app


app = create_app()
