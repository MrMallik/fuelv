from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any

from backend.db.repository import SnapshotRepository, get_default_db_path
from backend.storage.r2 import R2Storage, get_r2_storage

ROOT_DIR = Path(__file__).resolve().parents[2]


def get_default_images_dir() -> Path:
    env_dir = os.getenv("FUELV_IMAGES_DIR")
    return Path(env_dir) if env_dir else ROOT_DIR / "data" / "images"


def _guess_mime_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "image/jpeg"


def migrate_local_images_to_r2(
    *,
    db_path: Path | None = None,
    images_dir: Path | None = None,
    storage: R2Storage | None = None,
    delete_local: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate existing local snapshot images (e.g. `images/<file>`) to Cloudflare R2 (`fvs/<file>`),
    update SQLite rows in `fuel_snapshots`, and optionally delete local files after verification.
    """
    resolved_db = db_path or get_default_db_path()
    resolved_img_dir = (images_dir or get_default_images_dir()).resolve()
    resolved_img_parent = resolved_img_dir.parent.resolve()
    owns_storage = storage is None
    r2 = storage or get_r2_storage()

    if not dry_run and not r2.is_configured:
        raise RuntimeError(
            "R2 credentials are not configured. Set R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY."
        )

    repo = SnapshotRepository(db_path=resolved_db)
    prefix_with_slash = f"{r2.key_prefix}/"

    migrated_files = 0
    updated_rows = 0
    deleted_local_files = 0
    skipped_files = 0
    errors: list[str] = []

    try:
        with repo._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, captured_at, odo_image_path, pump_image_path
                FROM fuel_snapshots
                ORDER BY captured_at ASC
                """
            ).fetchall()

        for row in rows:
            snap_id = row["id"]
            updates: dict[str, str] = {}
            local_files_to_remove: list[Path] = []

            for col in ("odo_image_path", "pump_image_path"):
                raw_path: str | None = row[col]
                if not raw_path:
                    continue

                clean_path = raw_path.strip().replace("\\", "/").lstrip("/")
                if clean_path.startswith(prefix_with_slash) or clean_path.startswith(("http://", "https://")):
                    skipped_files += 1
                    continue

                if ".." in Path(clean_path).parts:
                    msg = f"[{snap_id}] Refusing unsafe path traversal in {col}={raw_path!r}"
                    errors.append(msg)
                    print(f"  WARN: {msg}", file=sys.stderr)
                    continue

                filename = Path(clean_path).name
                candidate_file = (resolved_img_dir / filename).resolve()
                if not candidate_file.is_relative_to(resolved_img_dir) or not candidate_file.is_file():
                    # Also check relative to root data dir if stored as `images/foo.jpg`
                    alt_candidate = (resolved_img_parent / clean_path).resolve()
                    if alt_candidate.is_relative_to(resolved_img_parent) and alt_candidate.is_file():
                        candidate_file = alt_candidate
                    else:
                        msg = f"[{snap_id}] Local file not found for {col}={raw_path} (checked {candidate_file})"
                        errors.append(msg)
                        print(f"  WARN: {msg}", file=sys.stderr)
                        continue

                target_key = r2.build_object_key(filename)
                mime_type = _guess_mime_type(filename)

                if dry_run:
                    print(f"  [DRY-RUN] Would upload {candidate_file} -> R2://{r2.bucket_name}/{target_key}")
                    updates[col] = target_key
                    migrated_files += 1
                    continue

                try:
                    file_bytes = candidate_file.read_bytes()
                    uploaded_key = r2.upload_image(
                        data=file_bytes,
                        filename=filename,
                        content_type=mime_type,
                    )
                    # Verify object exists on R2 before updating DB / deleting local file
                    if not r2.head_image(uploaded_key):
                        raise RuntimeError(f"HEAD verification failed on R2 for {uploaded_key}")

                    updates[col] = uploaded_key
                    migrated_files += 1
                    local_files_to_remove.append(candidate_file)
                    print(
                        f"  OK: Uploaded {filename} ({len(file_bytes)} bytes) -> "
                        f"{r2.build_public_url(uploaded_key)}"
                    )
                except Exception as exc:
                    msg = f"[{snap_id}] Failed to migrate {filename}: {exc}"
                    errors.append(msg)
                    print(f"  ERROR: {msg}", file=sys.stderr)

            if updates and not dry_run:
                with repo._connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    for col, new_key in updates.items():
                        conn.execute(
                            f"UPDATE fuel_snapshots SET {col} = ? WHERE id = ?",
                            (new_key, snap_id),
                        )
                    conn.commit()
                updated_rows += 1

                if delete_local:
                    for lf in local_files_to_remove:
                        try:
                            lf.unlink(missing_ok=True)
                            deleted_local_files += 1
                            print(f"  CLEANUP: Removed local file {lf}")
                        except Exception as exc:
                            print(f"  WARN: Could not delete local file {lf}: {exc}", file=sys.stderr)
            elif updates and dry_run:
                updated_rows += 1
    finally:
        if owns_storage:
            r2.close()

    return {
        "total_rows_scanned": len(rows),
        "updated_rows": updated_rows,
        "migrated_files": migrated_files,
        "deleted_local_files": deleted_local_files,
        "skipped_files": skipped_files,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate local FuelV snapshot photos to Cloudflare R2 (public-bucket/fvs/)."
    )
    parser.add_argument("--db-path", type=Path, default=None, help="Path to fuelv.db")
    parser.add_argument("--images-dir", type=Path, default=None, help="Path to local images directory")
    parser.add_argument(
        "--delete-local",
        action="store_true",
        help="Delete local image files from disk after verifying R2 upload",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview files to migrate without uploading or modifying the database",
    )
    args = parser.parse_args()

    print("Starting FuelV -> Cloudflare R2 (fvs/) image migration...")
    summary = migrate_local_images_to_r2(
        db_path=args.db_path,
        images_dir=args.images_dir,
        delete_local=args.delete_local,
        dry_run=args.dry_run,
    )
    print(
        f"\nMigration Summary:\n"
        f"  Rows scanned        : {summary['total_rows_scanned']}\n"
        f"  Rows updated        : {summary['updated_rows']}\n"
        f"  Images uploaded     : {summary['migrated_files']}\n"
        f"  Local files deleted : {summary['deleted_local_files']}\n"
        f"  Already on R2/Skip  : {summary['skipped_files']}\n"
        f"  Errors              : {len(summary['errors'])}"
    )
    if summary["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
