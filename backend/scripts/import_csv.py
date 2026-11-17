from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

from backend.db.repository import SnapshotRepository


def parse_fuelio_csv(csv_path: Path) -> tuple[str, list[dict[str, str]]]:
    """Parses a Fuelio multi-section CSV export (`## Vehicle`, `## Log`, `## Category`)."""
    raw_lines = csv_path.read_text(encoding="utf-8-sig").splitlines()

    current_section: str | None = None
    vehicle_rows: list[list[str]] = []
    log_rows: list[list[str]] = []

    reader = csv.reader(raw_lines)
    for row in reader:
        if not row:
            continue
        first_cell = row[0].strip()
        if first_cell.startswith("##"):
            current_section = first_cell.lstrip("#").strip().lower()
            continue

        if current_section == "vehicle":
            vehicle_rows.append(row)
        elif current_section == "log":
            log_rows.append(row)

    vehicle_name = "My Bike"
    if len(vehicle_rows) >= 2:
        headers = [h.strip() for h in vehicle_rows[0]]
        values = vehicle_rows[1]
        veh_dict = dict(zip(headers, values))
        if veh_dict.get("Name"):
            vehicle_name = veh_dict["Name"].strip()

    parsed_logs: list[dict[str, str]] = []
    if len(log_rows) >= 2:
        headers = [h.strip() for h in log_rows[0]]
        for values in log_rows[1:]:
            row_dict = {headers[i]: values[i].strip() for i in range(min(len(headers), len(values)))}
            parsed_logs.append(row_dict)

    return vehicle_name, parsed_logs


def import_csv_to_db(csv_path: Path, db_path: Path | None = None) -> dict[str, int]:
    vehicle_name, logs = parse_fuelio_csv(csv_path)
    repo = SnapshotRepository(db_path=db_path)

    # Check existing legacy_guid / captured_at to keep imports idempotent
    existing = repo.get_grouped_snapshots("year")
    existing_guids: set[str] = set()
    for group in existing["groups"]:
        for snap in group["snapshots"]:
            guid = snap.get("extra_data", {}).get("legacy_guid")
            if guid:
                existing_guids.add(str(guid))

    imported_count = 0
    skipped_count = 0

    for row in logs:
        guid = row.get("guid", "")
        if guid and guid in existing_guids:
            skipped_count += 1
            continue

        raw_date = row.get("Data", "")
        if not raw_date:
            skipped_count += 1
            continue

        # Convert "2026-11-11 18:45" -> "2026-11-11T18:45:00+05:30"
        if "T" not in raw_date:
            date_part = raw_date.replace(" ", "T")
            if len(date_part) == 16:  # YYYY-MM-DDTHH:MM
                date_part += ":00"
            captured_at = f"{date_part}+05:30"
        else:
            captured_at = raw_date

        odo = float(row.get("Odo (km)") or 0.0)
        fuel = float(row.get("Fuel (litres)") or 0.0)
        price = float(row.get("Price (optional)") or 0.0)

        extra_data: dict[str, Any] = {
            "vehicle": vehicle_name,
            "source": "csv_import",
        }
        if row.get("VolumePrice"):
            extra_data["unit_price"] = float(row["VolumePrice"])
        if row.get("km/l (optional)"):
            extra_data["km_per_l"] = float(row["km/l (optional)"])
        if row.get("Full"):
            extra_data["is_full_tank"] = row["Full"] == "1"
        if row.get("Notes (optional)"):
            extra_data["notes"] = row["Notes (optional)"]
        if row.get("UniqueId"):
            extra_data["legacy_unique_id"] = int(row["UniqueId"])
        if guid:
            extra_data["legacy_guid"] = guid

        repo.insert_snapshot(
            captured_at=captured_at,
            odometer_reading=odo,
            total_expense=round(price, 2),
            fuel_dispensed=round(fuel, 3),
            entry_mode="manual",
            extra_data=extra_data,
        )
        imported_count += 1

    return {
        "imported": imported_count,
        "skipped": skipped_count,
        "total_in_csv": len(logs),
    }


if __name__ == "__main__":
    default_csv = Path(__file__).resolve().parents[2] / "data" / "my_bike_import.csv"
    target_csv = Path(sys.argv[1]) if len(sys.argv) > 1 else default_csv
    result = import_csv_to_db(target_csv)
    print(json.dumps(result, indent=2))
