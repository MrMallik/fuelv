from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Literal

from ..storage.r2 import resolve_image_url

GroupByPeriod = Literal["day", "week", "month", "year"]

PERIOD_FORMATS: dict[GroupByPeriod, str] = {
    "day": "%Y-%m-%d",
    "week": "%Y-W%W",
    "month": "%Y-%m",
    "year": "%Y",
}

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_default_db_path() -> Path:
    env_path = os.getenv("FUELV_DB_PATH")
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parents[2] / "data" / "fuelv.db"


class SnapshotRepository:
    """SQLite3 repository for storing and grouping fuel snapshots."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else get_default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.executescript(schema_sql)

    @staticmethod
    def _determine_entry_mode(
        explicit_mode: str | None,
        raw_extraction: dict[str, Any] | None,
        odometer_reading: float,
        total_expense: float,
        fuel_dispensed: float,
    ) -> str:
        if explicit_mode in ("vision", "manual", "vision_edited"):
            return explicit_mode
        if not raw_extraction:
            return "manual"

        def _same(a: Any, b: float) -> bool:
            if a is None:
                return False
            try:
                return abs(float(a) - float(b)) < 1e-4
            except (TypeError, ValueError):
                return False

        matches_raw = (
            _same(raw_extraction.get("odometer_reading"), odometer_reading)
            and _same(raw_extraction.get("total_expense"), total_expense)
            and _same(raw_extraction.get("fuel_dispensed"), fuel_dispensed)
        )
        return "vision" if matches_raw else "vision_edited"

    def _recalculate_chronological_chain(self, conn: sqlite3.Connection) -> None:
        """Recalculate previous_odometer, distance_delta, and km_per_l across all snapshots
        in strict chronological order (captured_at ASC, created_at ASC).
        """
        rows = conn.execute(
            """
            SELECT id, odometer_reading, fuel_dispensed, extra_data
            FROM fuel_snapshots
            ORDER BY captured_at ASC, created_at ASC
            """
        ).fetchall()

        prev_odo: float | None = None
        for row in rows:
            snap_id = row["id"]
            odo = float(row["odometer_reading"])
            fuel = float(row["fuel_dispensed"])
            extra_raw = row["extra_data"]
            extra = json.loads(extra_raw) if extra_raw else {}

            changed = False
            if prev_odo is not None:
                if extra.get("previous_odometer") != prev_odo:
                    extra["previous_odometer"] = prev_odo
                    changed = True

                if odo >= prev_odo:
                    delta = round(odo - prev_odo, 1)
                    if extra.get("distance_delta") != delta:
                        extra["distance_delta"] = delta
                        changed = True

                    if fuel > 0 and delta > 0:
                        kmpl = round(delta / fuel, 2)
                        if extra.get("km_per_l") != kmpl:
                            extra["km_per_l"] = kmpl
                            changed = True
                    elif fuel > 0:
                        if extra.get("km_per_l") != 0.0:
                            extra["km_per_l"] = 0.0
                            changed = True
                    else:
                        if "km_per_l" in extra:
                            extra.pop("km_per_l", None)
                            changed = True
                else:
                    if "distance_delta" in extra:
                        extra.pop("distance_delta", None)
                        changed = True
                    if "km_per_l" in extra:
                        extra.pop("km_per_l", None)
                        changed = True
            else:
                if "previous_odometer" in extra:
                    extra.pop("previous_odometer", None)
                    changed = True
                if "distance_delta" in extra:
                    extra.pop("distance_delta", None)
                    changed = True
                if "km_per_l" in extra:
                    extra.pop("km_per_l", None)
                    changed = True

            if changed:
                conn.execute(
                    "UPDATE fuel_snapshots SET extra_data = ? WHERE id = ?",
                    (json.dumps(extra), snap_id),
                )

            prev_odo = odo

    def insert_snapshot(
        self,
        *,
        odometer_reading: float,
        total_expense: float,
        fuel_dispensed: float,
        captured_at: str | None = None,
        odo_image_path: str | None = None,
        pump_image_path: str | None = None,
        entry_mode: str | None = None,
        extractor_model: str | None = None,
        raw_extraction_json: dict[str, Any] | None = None,
        extra_data: dict[str, Any] | None = None,
        schema_version: int = 1,
    ) -> dict[str, Any]:
        snapshot_id = uuid.uuid4().hex
        timestamp = captured_at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

        resolved_mode = self._determine_entry_mode(
            entry_mode,
            raw_extraction_json,
            odometer_reading,
            total_expense,
            fuel_dispensed,
        )

        raw_json_str = json.dumps(raw_extraction_json) if raw_extraction_json is not None else None
        extra_dict = dict(extra_data or {})

        with self._connect() as conn:
            extra_json_str = json.dumps(extra_dict)
            conn.execute("BEGIN IMMEDIATE")

            conn.execute(
                """
                INSERT INTO fuel_snapshots (
                    id,
                    captured_at,
                    odo_image_path,
                    pump_image_path,
                    odometer_reading,
                    total_expense,
                    fuel_dispensed,
                    entry_mode,
                    extractor_model,
                    raw_extraction_json,
                    schema_version,
                    extra_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    timestamp,
                    odo_image_path,
                    pump_image_path,
                    float(odometer_reading),
                    float(total_expense),
                    float(fuel_dispensed),
                    resolved_mode,
                    extractor_model,
                    raw_json_str,
                    schema_version,
                    extra_json_str,
                ),
            )
            self._recalculate_chronological_chain(conn)
            conn.commit()

        return self.get_snapshot(snapshot_id)  # type: ignore[return-value]

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM fuel_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def get_latest_snapshot(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM fuel_snapshots
                ORDER BY captured_at DESC, created_at DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def update_snapshot(
        self,
        snapshot_id: str,
        *,
        odometer_reading: float | None = None,
        total_expense: float | None = None,
        fuel_dispensed: float | None = None,
        captured_at: str | None = None,
        entry_mode: str | None = None,
        extra_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM fuel_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
            if not row:
                return None

            current = self._row_to_dict(row)
            new_odo = float(odometer_reading) if odometer_reading is not None else float(current["odometer_reading"])
            new_exp = float(total_expense) if total_expense is not None else float(current["total_expense"])
            new_fuel = float(fuel_dispensed) if fuel_dispensed is not None else float(current["fuel_dispensed"])
            new_captured_at = captured_at if captured_at is not None else current["captured_at"]

            current_extra = dict(current.get("extra_data") or {})
            if extra_data:
                current_extra.update(extra_data)

            # Detect entry_mode update
            resolved_mode = entry_mode or current["entry_mode"]
            if resolved_mode == "vision" and (
                abs(new_odo - float(current["odometer_reading"])) > 1e-4
                or abs(new_exp - float(current["total_expense"])) > 1e-4
                or abs(new_fuel - float(current["fuel_dispensed"])) > 1e-4
            ):
                resolved_mode = "vision_edited"

            conn.execute(
                """
                UPDATE fuel_snapshots
                SET
                    odometer_reading = ?,
                    total_expense = ?,
                    fuel_dispensed = ?,
                    captured_at = ?,
                    entry_mode = ?,
                    extra_data = ?
                WHERE id = ?
                """,
                (
                    new_odo,
                    new_exp,
                    new_fuel,
                    new_captured_at,
                    resolved_mode,
                    json.dumps(current_extra),
                    snapshot_id,
                ),
            )
            self._recalculate_chronological_chain(conn)
            conn.commit()

        return self.get_snapshot(snapshot_id)

    def delete_snapshot(self, snapshot_id: str) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("DELETE FROM fuel_snapshots WHERE id = ?", (snapshot_id,))
            if cursor.rowcount > 0:
                self._recalculate_chronological_chain(conn)
            conn.commit()
            return cursor.rowcount > 0

    def get_grouped_snapshots(self, group_by: GroupByPeriod = "month") -> dict[str, Any]:
        if group_by not in PERIOD_FORMATS:
            raise ValueError(f"Unsupported group_by value: {group_by}")

        period_fmt = PERIOD_FORMATS[group_by]

        with self._connect() as conn:
            # Fetch all snapshots ordered newest first, paired with their computed SQLite period bucket
            rows = conn.execute(
                """
                SELECT
                    strftime(?, substr(captured_at, 1, 19)) AS period,
                    *
                FROM fuel_snapshots
                ORDER BY captured_at DESC, created_at DESC
                """,
                (period_fmt,),
            ).fetchall()

        groups_map: dict[str, dict[str, Any]] = {}
        overall_expense = 0.0
        overall_fuel = 0.0

        for row in rows:
            period_key = row["period"] or "Unknown"
            item = self._row_to_dict(row)

            if period_key not in groups_map:
                groups_map[period_key] = {
                    "period": period_key,
                    "snapshot_count": 0,
                    "total_expense": 0.0,
                    "total_fuel_dispensed": 0.0,
                    "min_odometer": item["odometer_reading"],
                    "max_odometer": item["odometer_reading"],
                    "snapshots": [],
                }

            grp = groups_map[period_key]
            grp["snapshot_count"] += 1
            grp["total_expense"] = round(grp["total_expense"] + item["total_expense"], 2)
            grp["total_fuel_dispensed"] = round(grp["total_fuel_dispensed"] + item["fuel_dispensed"], 3)
            grp["min_odometer"] = min(grp["min_odometer"], item["odometer_reading"])
            grp["max_odometer"] = max(grp["max_odometer"], item["odometer_reading"])
            grp["snapshots"].append(item)

            overall_expense = round(overall_expense + item["total_expense"], 2)
            overall_fuel = round(overall_fuel + item["fuel_dispensed"], 3)

        groups_list = sorted(groups_map.values(), key=lambda g: g["period"], reverse=True)

        overall_min_odo = min((r["odometer_reading"] for r in rows), default=0.0)
        overall_max_odo = max((r["odometer_reading"] for r in rows), default=0.0)
        overall_distance = round(max(0.0, overall_max_odo - overall_min_odo), 1) if len(rows) > 1 else 0.0

        return {
            "group_by": group_by,
            "total_snapshots": len(rows),
            "total_logs": len(rows),
            "overall_expense": overall_expense,
            "overall_fuel_dispensed": overall_fuel,
            "overall_distance": overall_distance,
            "groups": groups_list,
        }

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        odo_path = row["odo_image_path"]
        pump_path = row["pump_image_path"]
        raw_json = row["raw_extraction_json"]
        extra_json = row["extra_data"]

        return {
            "id": row["id"],
            "captured_at": row["captured_at"],
            "odo_image_path": odo_path,
            "pump_image_path": pump_path,
            "odo_image_url": resolve_image_url(odo_path),
            "pump_image_url": resolve_image_url(pump_path),
            "odometer_reading": row["odometer_reading"],
            "total_expense": row["total_expense"],
            "fuel_dispensed": row["fuel_dispensed"],
            "entry_mode": row["entry_mode"],
            "extractor_model": row["extractor_model"],
            "raw_extraction_json": json.loads(raw_json) if raw_json else None,
            "schema_version": row["schema_version"],
            "extra_data": json.loads(extra_json) if extra_json else {},
            "created_at": row["created_at"],
        }
