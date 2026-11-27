from __future__ import annotations

import hmac
import json
import logging
import math
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import load_dotenv_if_present, validate_environment
from .db.repository import GroupByPeriod, SnapshotRepository
from .extraction import get_extractor, normalize_extracted_json
from .storage.r2 import R2Storage, get_r2_storage

logger = logging.getLogger("fuelv.api")

ROOT_DIR = Path(__file__).resolve().parents[1]
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"}
EXT_TO_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".bmp": "image/bmp",
}
ALLOWED_IMAGE_MIMES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
    "image/bmp",
    "image/x-ms-bmp",
}
MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15 MB per image guard
_UPLOAD_CHUNK_BYTES = 64 * 1024

load_dotenv_if_present()


async def _read_upload_bounded(upload: UploadFile, max_bytes: int, label: str) -> bytes:
    """Read UploadFile in bounded chunks to prevent OOM from oversized uploads."""
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = await upload.read(_UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail=f"{label} exceeds 15 MB limit.")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        await upload.close()


def _sanitize_image_mime(content_type: str | None, ext: str) -> str:
    """Validate and normalize uploaded image MIME type to safe raster image types only."""
    base_mime = (content_type or "").split(";", 1)[0].strip().lower()
    if not base_mime or base_mime == "application/octet-stream":
        return EXT_TO_MIME.get(ext, "image/jpeg")
    if base_mime == "image/jpg":
        return "image/jpeg"
    if base_mime in ALLOWED_IMAGE_MIMES:
        return base_mime
    raise HTTPException(
        status_code=400,
        detail=f"Unsupported image content type: {base_mime}",
    )


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
    odometer_reading: float = Field(..., ge=0, allow_inf_nan=False, description="Confirmed odometer reading")
    total_expense: float = Field(..., ge=0, allow_inf_nan=False, description="Confirmed total fuel expense")
    fuel_dispensed: float = Field(..., ge=0, allow_inf_nan=False, description="Confirmed volume of fuel dispensed")
    odo_image_path: str | None = None
    pump_image_path: str | None = None
    entry_mode: str | None = None
    extractor_model: str | None = None
    raw_extraction_json: dict[str, Any] | None = None
    extra_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("odo_image_path", "pump_image_path")
    @classmethod
    def _validate_image_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if "\\" in cleaned or "\x00" in cleaned or ".." in PurePosixPath(cleaned).parts:
            raise ValueError("Invalid image path")
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", cleaned) and not cleaned.startswith(("http://", "https://")):
            raise ValueError("Unsupported URL scheme in image path")
        return cleaned


class UpdateSnapshotRequest(BaseModel):
    captured_at: str | None = None
    odometer_reading: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    total_expense: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    fuel_dispensed: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    entry_mode: str | None = None
    extra_data: dict[str, Any] | None = None


def get_client_ip(request: Request) -> str:
    """Extract client IP supporting Cloudflare (CF-Connecting-IP), reverse proxies (X-Forwarded-For),
    and direct socket connections.
    """
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "detail": f"Rate limit exceeded: {exc.detail}. Please slow down.",
        },
        headers={"Retry-After": "60"},
    )


def verify_write_access(
    x_fuelv_key: str | None = Header(default=None, alias="X-FuelV-Key"),
) -> None:
    if os.getenv("FUELV_DISABLE_AUTH", "").strip().lower() in ("1", "true", "yes"):
        return
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        if getattr(app.state, "validate_on_startup", False):
            res = validate_environment(exit_on_failure=False)
            if not res.is_valid:
                raise RuntimeError(
                    f"FuelV startup halted: missing required configuration: {', '.join(res.missing_vars)}"
                )
        yield
    finally:
        r2 = getattr(app.state, "r2", None)
        if r2 is not None and hasattr(r2, "close"):
            r2.close()


def create_app(
    db_path: str | Path | None = None,
    images_dir: str | Path | None = None,
    r2_storage: R2Storage | None = None,
    validate_on_startup: bool | None = None,
) -> FastAPI:
    app = FastAPI(
        title="FuelV — The Visual Fuel Logger API",
        description="Snapshot-based visual fuel log backend with swappable vision extraction, Cloudflare R2 storage, and SQLite3.",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.validate_on_startup = (
        validate_on_startup
        if validate_on_startup is not None
        else (db_path is None and os.getenv("FUELV_SKIP_STARTUP_VALIDATION", "").strip().lower() not in ("1", "true", "yes"))
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    rate_limit_enabled = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in ("true", "1", "yes")
    limiter = Limiter(
        key_func=get_client_ip,
        enabled=rate_limit_enabled,
        default_limits=["120/minute"],
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    def _sanitize_validation_errors(val: Any) -> Any:
        if isinstance(val, float) and not math.isfinite(val):
            return str(val)
        if isinstance(val, dict):
            return {k: _sanitize_validation_errors(v) for k, v in val.items()}
        if isinstance(val, (list, tuple)):
            return [_sanitize_validation_errors(v) for v in val]
        return val

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": _sanitize_validation_errors(jsonable_encoder(exc.errors()))},
        )

    repo = SnapshotRepository(db_path=db_path)
    img_dir = Path(images_dir) if images_dir else get_images_dir()
    img_dir.mkdir(parents=True, exist_ok=True)
    r2 = r2_storage if r2_storage is not None else get_r2_storage()

    app.state.repo = repo
    app.state.images_dir = img_dir
    app.state.r2 = r2

    app.mount("/api/images", StaticFiles(directory=str(img_dir)), name="images")

    @app.get("/api/health")
    @limiter.exempt
    def health_check(request: Request) -> dict[str, Any]:
        extractor = get_extractor()
        backend = os.getenv("EXTRACTOR_BACKEND", "gemini").strip().lower()
        return {
            "status": "ok",
            "extractor_backend": backend,
            "extractor_model": extractor.model_id,
            "gemini_key_configured": bool(os.getenv("GEMINI_API_KEY", "").strip()),
            "write_protection_enabled": bool(os.getenv("FUELV_WRITE_KEY", "").strip())
            and os.getenv("FUELV_DISABLE_AUTH", "").strip().lower() not in ("1", "true", "yes"),
            "storage_backend": "cloudflare_r2",
            "r2_configured": app.state.r2.is_configured,
            "r2_bucket": app.state.r2.bucket_name,
            "r2_prefix": app.state.r2.key_prefix,
            "r2_public_base_url": app.state.r2.public_base_url,
        }

    @app.post("/api/extract", dependencies=[Depends(verify_write_access)])
    @limiter.limit("15/minute")
    async def extract_from_snapshots(
        request: Request,
        odo_image: UploadFile | None = File(default=None),
        pump_image: UploadFile | None = File(default=None),
    ) -> dict[str, Any]:
        """Stateless VLM extraction: reads image bytes in memory and extracts structured numbers
        without writing images to disk or uploading to R2 prior to user confirmation.
        """
        if odo_image is None and pump_image is None:
            raise HTTPException(
                status_code=400,
                detail="At least one image (odo_image or pump_image) must be provided.",
            )

        odo_bytes: bytes | None = None
        odo_mime: str | None = None
        pump_bytes: bytes | None = None
        pump_mime: str | None = None

        if odo_image is not None:
            odo_bytes = await _read_upload_bounded(odo_image, MAX_IMAGE_BYTES, "Odometer image")
            odo_ext = Path((odo_image.filename or "odo.jpg").replace("\\", "/")).suffix.lower()
            odo_mime = _sanitize_image_mime(
                odo_image.content_type,
                odo_ext if odo_ext in ALLOWED_IMAGE_EXTENSIONS else ".jpg",
            )

        if pump_image is not None:
            pump_bytes = await _read_upload_bounded(pump_image, MAX_IMAGE_BYTES, "Fuel pump image")
            pump_ext = Path((pump_image.filename or "pump.jpg").replace("\\", "/")).suffix.lower()
            pump_mime = _sanitize_image_mime(
                pump_image.content_type,
                pump_ext if pump_ext in ALLOWED_IMAGE_EXTENSIONS else ".jpg",
            )

        extractor = get_extractor()
        warning_msg: str | None = None

        try:
            extracted_json = await run_in_threadpool(
                extractor.extract,
                odo_bytes=odo_bytes,
                odo_mime=odo_mime,
                pump_bytes=pump_bytes,
                pump_mime=pump_mime,
            )
        except Exception as exc:
            # Graceful fallback: return null extraction values so the user can enter numbers
            # manually while retaining their captured photos in client IndexedDB.
            logger.warning("VLM extraction failed; switching to manual fallback: %s", exc)
            extracted_json = normalize_extracted_json(None)
            warning_msg = str(exc)

        latest = await run_in_threadpool(app.state.repo.get_latest_snapshot)
        efficiency_info: dict[str, Any] | None = None
        if latest and latest.get("odometer_reading") is not None:
            prev_odo = float(latest["odometer_reading"])
            extracted_odo = extracted_json.get("odometer_reading")
            extracted_fuel = extracted_json.get("fuel_dispensed")

            dist_delta = None
            kmpl = None
            if extracted_odo is not None and float(extracted_odo) >= prev_odo:
                dist_delta = round(float(extracted_odo) - prev_odo, 1)
                if extracted_fuel is not None and float(extracted_fuel) > 0 and dist_delta > 0:
                    kmpl = round(dist_delta / float(extracted_fuel), 2)
                elif extracted_fuel is not None and float(extracted_fuel) > 0:
                    kmpl = 0.0

            efficiency_info = {
                "previous_odometer": prev_odo,
                "distance_delta": dist_delta,
                "km_per_l": kmpl,
            }

        return {
            "extraction": extracted_json,
            "efficiency": efficiency_info,
            "meta": {
                "extractor_model": extractor.model_id,
                "odo_image_path": None,
                "pump_image_path": None,
                "odo_image_url": None,
                "pump_image_url": None,
                "warning": warning_msg,
            },
        }

    @app.get("/api/snapshots/latest")
    @limiter.limit("60/minute")
    def get_latest_snapshot(request: Request) -> dict[str, Any]:
        latest = app.state.repo.get_latest_snapshot()
        return {"latest": latest}

    @app.put("/api/snapshots/{snapshot_id}", dependencies=[Depends(verify_write_access)])
    @limiter.limit("30/minute")
    def edit_snapshot(
        request: Request,
        snapshot_id: str,
        payload: UpdateSnapshotRequest,
    ) -> dict[str, Any]:
        updated = app.state.repo.update_snapshot(
            snapshot_id,
            odometer_reading=payload.odometer_reading,
            total_expense=payload.total_expense,
            fuel_dispensed=payload.fuel_dispensed,
            captured_at=payload.captured_at,
            entry_mode=payload.entry_mode,
            extra_data=payload.extra_data,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        return updated

    @app.post("/api/snapshots", status_code=201, dependencies=[Depends(verify_write_access)])
    @limiter.limit("30/minute")
    async def create_snapshot(request: Request) -> dict[str, Any]:
        """Create a confirmed fuel log and upload any attached photos directly to Cloudflare R2 (`fvs/`).
        Supports both `multipart/form-data` (with `odo_image` / `pump_image` files) and `application/json`.
        """
        content_type = request.headers.get("content-type", "").lower()
        odo_upload: UploadFile | None = None
        pump_upload: UploadFile | None = None

        if "multipart/form-data" in content_type:
            form = await request.form()
            raw_data: dict[str, Any] = {}
            for key in (
                "captured_at",
                "odometer_reading",
                "total_expense",
                "fuel_dispensed",
                "odo_image_path",
                "pump_image_path",
                "entry_mode",
                "extractor_model",
            ):
                val = form.get(key)
                if isinstance(val, str) and val != "":
                    raw_data[key] = val

            for json_key in ("raw_extraction_json", "extra_data"):
                raw_json_val = form.get(json_key)
                if isinstance(raw_json_val, str) and raw_json_val.strip():
                    try:
                        raw_data[json_key] = json.loads(raw_json_val)
                    except json.JSONDecodeError as exc:
                        raise HTTPException(status_code=400, detail=f"Invalid JSON in {json_key}") from exc

            odo_candidate = form.get("odo_image")
            if hasattr(odo_candidate, "read"):
                odo_upload = odo_candidate  # type: ignore[assignment]

            pump_candidate = form.get("pump_image")
            if hasattr(pump_candidate, "read"):
                pump_upload = pump_candidate  # type: ignore[assignment]
        else:
            def _reject_non_finite_constant(token: str) -> Any:
                raise ValueError(f"Non-standard JSON constant not allowed: {token}")

            try:
                body_bytes = await request.body()
                raw_data = json.loads(body_bytes, parse_constant=_reject_non_finite_constant)
                if not isinstance(raw_data, dict):
                    raise ValueError("JSON body must be an object")
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Invalid JSON request body") from exc

        try:
            payload = CreateSnapshotRequest.model_validate(raw_data)
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc

        batch_prefix = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        uploaded_r2_keys: list[str] = []
        odo_image_path = payload.odo_image_path
        pump_image_path = payload.pump_image_path

        try:
            if odo_upload is not None:
                odo_bytes = await _read_upload_bounded(odo_upload, MAX_IMAGE_BYTES, "Odometer image")
                if odo_bytes:
                    raw_ext = Path((odo_upload.filename or "odo.jpg").replace("\\", "/")).suffix.lower()
                    ext = raw_ext if raw_ext in ALLOWED_IMAGE_EXTENSIONS else ".jpg"
                    odo_filename = f"{batch_prefix}_odo{ext}"
                    odo_mime = _sanitize_image_mime(odo_upload.content_type, ext)
                    odo_image_path = await run_in_threadpool(
                        app.state.r2.upload_image,
                        data=odo_bytes,
                        filename=odo_filename,
                        content_type=odo_mime,
                    )
                    uploaded_r2_keys.append(odo_image_path)

            if pump_upload is not None:
                pump_bytes = await _read_upload_bounded(pump_upload, MAX_IMAGE_BYTES, "Fuel pump image")
                if pump_bytes:
                    raw_ext = Path((pump_upload.filename or "pump.jpg").replace("\\", "/")).suffix.lower()
                    ext = raw_ext if raw_ext in ALLOWED_IMAGE_EXTENSIONS else ".jpg"
                    pump_filename = f"{batch_prefix}_pump{ext}"
                    pump_mime = _sanitize_image_mime(pump_upload.content_type, ext)
                    pump_image_path = await run_in_threadpool(
                        app.state.r2.upload_image,
                        data=pump_bytes,
                        filename=pump_filename,
                        content_type=pump_mime,
                    )
                    uploaded_r2_keys.append(pump_image_path)

            record = await run_in_threadpool(
                app.state.repo.insert_snapshot,
                captured_at=payload.captured_at,
                odometer_reading=payload.odometer_reading,
                total_expense=payload.total_expense,
                fuel_dispensed=payload.fuel_dispensed,
                odo_image_path=odo_image_path,
                pump_image_path=pump_image_path,
                entry_mode=payload.entry_mode,
                extractor_model=payload.extractor_model,
                raw_extraction_json=payload.raw_extraction_json,
                extra_data=payload.extra_data,
            )
            return record
        except HTTPException:
            for key in uploaded_r2_keys:
                await run_in_threadpool(app.state.r2.delete_image, key)
            raise
        except Exception as exc:
            logger.exception("Failed to store snapshot: %s", exc)
            for key in uploaded_r2_keys:
                await run_in_threadpool(app.state.r2.delete_image, key)
            raise HTTPException(
                status_code=502,
                detail="Failed to store snapshot in cloud storage or database. Please retry.",
            ) from exc

    @app.get("/api/snapshots/grouped")
    @limiter.limit("60/minute")
    def list_grouped_snapshots(
        request: Request,
        by: GroupByPeriod = Query(default="month", description="Group snapshots by week, month, or year"),
    ) -> dict[str, Any]:
        return app.state.repo.get_grouped_snapshots(group_by=by)

    @app.delete("/api/snapshots/{snapshot_id}", dependencies=[Depends(verify_write_access)])
    @limiter.limit("30/minute")
    def remove_snapshot(
        request: Request,
        snapshot_id: str,
    ) -> dict[str, Any]:
        existing = app.state.repo.get_snapshot(snapshot_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        deleted = app.state.repo.delete_snapshot(snapshot_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        # Best-effort cleanup of R2 objects
        app.state.r2.delete_image(existing.get("odo_image_path"))
        app.state.r2.delete_image(existing.get("pump_image_path"))
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
            try:
                candidate = (web_dist / full_path).resolve()
                if full_path and candidate.is_file() and candidate.is_relative_to(web_dist.resolve()):
                    return FileResponse(candidate)
            except (ValueError, RuntimeError):
                pass
            return FileResponse(index_html)

    return app


app = create_app()
