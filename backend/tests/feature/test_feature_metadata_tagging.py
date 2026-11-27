from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.db.repository import SnapshotRepository
from backend.main import create_app
from backend.scripts.import_csv import import_csv_to_db, parse_fuelio_csv


class TestFeatureMetadataTagging(unittest.TestCase):
    """Feature tests for tagging logs with vehicle, station, notes, and custom metadata
    in extra_data across inserts, reads, partial updates, recalculations, and CSV imports.
    """

    def setUp(self) -> None:
        self.env_patcher = patch.dict(
            os.environ,
            {
                "FUELV_WRITE_KEY": "",
                "FUELV_DISABLE_AUTH": "1",
                "RATE_LIMIT_ENABLED": "false",
            },
            clear=False,
        )
        self.env_patcher.start()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_metadata.db"
        self.img_dir = Path(self.tmp_dir.name) / "images"
        self.img_dir.mkdir(parents=True, exist_ok=True)
        self.app = create_app(
            db_path=self.db_path,
            images_dir=self.img_dir,
            validate_on_startup=False,
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.tmp_dir.cleanup()

    def test_vehicle_station_and_notes_preserved_across_reads_and_updates(self) -> None:
        """extra_data tags (vehicle, station, notes) persist across reads, grouped views, and partial updates."""
        res1 = self.client.post(
            "/api/snapshots",
            json={
                "captured_at": "2026-11-01T08:30:00+05:30",
                "odometer_reading": 12000.0,
                "total_expense": 500.0,
                "fuel_dispensed": 5.0,
                "extra_data": {
                    "vehicle": "My Bike",
                    "station": "Shell Whitefield",
                    "notes": "Morning commute fill",
                    "unit_price": 100.0,
                },
            },
        )
        self.assertEqual(res1.status_code, 201)
        snap1 = res1.json()

        # Multipart submission with JSON extra_data for a second vehicle
        with patch.object(
            self.app.state.r2,
            "upload_image",
            side_effect=lambda data, filename, content_type="image/jpeg", max_retries=3: self.app.state.r2.build_object_key(
                filename
            ),
        ):
            res2 = self.client.post(
                "/api/snapshots",
                data={
                    "captured_at": "2026-11-05T18:00:00+05:30",
                    "odometer_reading": "12250.0",
                    "total_expense": "515.0",
                    "fuel_dispensed": "5.0",
                    "extra_data": json.dumps(
                        {
                            "vehicle": "Car",
                            "station": "HPCL Indiranagar",
                            "notes": "Highway prep",
                        }
                    ),
                },
                files={"odo_image": ("car_odo.jpg", b"odo-bytes", "image/jpeg")},
            )
        self.assertEqual(res2.status_code, 201)
        snap2 = res2.json()

        # Latest endpoint retains all tags
        latest = self.client.get("/api/snapshots/latest").json()["latest"]
        self.assertEqual(latest["id"], snap2["id"])
        self.assertEqual(latest["extra_data"]["vehicle"], "Car")
        self.assertEqual(latest["extra_data"]["station"], "HPCL Indiranagar")
        self.assertEqual(latest["extra_data"]["notes"], "Highway prep")

        # Partial extra_data update merges rather than wiping existing tags
        upd = self.client.put(
            f"/api/snapshots/{snap1['id']}",
            json={"extra_data": {"notes": "Updated morning note", "fuel_grade": "XP95"}},
        )
        self.assertEqual(upd.status_code, 200)
        upd_extra = upd.json()["extra_data"]
        self.assertEqual(upd_extra["vehicle"], "My Bike")
        self.assertEqual(upd_extra["station"], "Shell Whitefield")
        self.assertEqual(upd_extra["unit_price"], 100.0)
        self.assertEqual(upd_extra["notes"], "Updated morning note")
        self.assertEqual(upd_extra["fuel_grade"], "XP95")

        # Grouped snapshots preserve all user metadata alongside computed metrics
        grouped = self.client.get("/api/snapshots/grouped?by=month").json()
        snaps_by_id = {s["id"]: s for s in grouped["groups"][0]["snapshots"]}
        self.assertEqual(snaps_by_id[snap1["id"]]["extra_data"]["vehicle"], "My Bike")
        self.assertEqual(snaps_by_id[snap2["id"]]["extra_data"]["vehicle"], "Car")
        self.assertEqual(snaps_by_id[snap2["id"]]["extra_data"]["distance_delta"], 250.0)
        self.assertEqual(snaps_by_id[snap2["id"]]["extra_data"]["km_per_l"], 50.0)

    def test_metadata_survives_chronological_recalculation_on_out_of_order_insert_and_delete(self) -> None:
        """Inserting or deleting neighbors triggers _recalculate_chronological_chain without losing metadata."""
        repo: SnapshotRepository = self.app.state.repo
        s1 = repo.insert_snapshot(
            captured_at="2026-11-01T10:00:00Z",
            odometer_reading=1000.0,
            total_expense=200.0,
            fuel_dispensed=2.0,
            extra_data={"vehicle": "My Bike", "station": "BPCL"},
        )
        s3 = repo.insert_snapshot(
            captured_at="2026-11-10T10:00:00Z",
            odometer_reading=1300.0,
            total_expense=300.0,
            fuel_dispensed=3.0,
            extra_data={"vehicle": "My Bike", "station": "IndianOil", "notes": "Long ride"},
        )
        # Backdated middle insert
        s2 = repo.insert_snapshot(
            captured_at="2026-11-05T10:00:00Z",
            odometer_reading=1150.0,
            total_expense=150.0,
            fuel_dispensed=1.5,
            extra_data={"vehicle": "My Bike", "station": "Shell", "notes": "Mid fill"},
        )

        s3_after = repo.get_snapshot(s3["id"])
        self.assertIsNotNone(s3_after)
        self.assertEqual(s3_after["extra_data"]["vehicle"], "My Bike")
        self.assertEqual(s3_after["extra_data"]["station"], "IndianOil")
        self.assertEqual(s3_after["extra_data"]["notes"], "Long ride")
        self.assertEqual(s3_after["extra_data"]["distance_delta"], 150.0)

        # Delete first snapshot -> s2 becomes first, keeps metadata while efficiency keys clear
        self.assertTrue(repo.delete_snapshot(s1["id"]))
        s2_after = repo.get_snapshot(s2["id"])
        self.assertIsNotNone(s2_after)
        self.assertEqual(s2_after["extra_data"]["vehicle"], "My Bike")
        self.assertEqual(s2_after["extra_data"]["station"], "Shell")
        self.assertEqual(s2_after["extra_data"]["notes"], "Mid fill")
        self.assertNotIn("previous_odometer", s2_after["extra_data"])

    def test_fuelio_csv_import_populates_metadata_and_is_idempotent(self) -> None:
        """import_csv_to_db parses vehicle/log metadata, normalizes timestamps, and skips duplicates."""
        csv_content = "\n".join(
            [
                "## Vehicle",
                "Name,Description,DistUnit,FuelUnit",
                '"Interceptor 650","Weekend Twin",0,0',
                "## Log",
                "Data,Odo (km),Fuel (litres),Full,Price (optional),km/l (optional),VolumePrice,Notes (optional),UniqueId,guid",
                '"2026-10-01 09:30",5000.0,5.0,1,510.0,,102.0,"First tank",101,guid-001',
                '"2026-10-10 18:45",5200.0,5.0,0,515.0,40.0,103.0,"Partial fill",102,guid-002',
                '"",5300.0,5.0,1,500.0,,,Missing date row,103,guid-003',
            ]
        )
        csv_path = Path(self.tmp_dir.name) / "fuelio_export.csv"
        csv_path.write_text(csv_content, encoding="utf-8-sig")

        vehicle_name, parsed_logs = parse_fuelio_csv(csv_path)
        self.assertEqual(vehicle_name, "Interceptor 650")
        self.assertEqual(len(parsed_logs), 3)

        summary_1 = import_csv_to_db(csv_path, db_path=self.db_path)
        self.assertEqual(summary_1, {"imported": 2, "skipped": 1, "total_in_csv": 3})

        repo = SnapshotRepository(db_path=self.db_path)
        grouped = repo.get_grouped_snapshots("month")
        self.assertEqual(grouped["total_snapshots"], 2)
        snaps = grouped["groups"][0]["snapshots"]
        # Ordered newest first: guid-002 then guid-001
        latest_snap, first_snap = snaps[0], snaps[1]

        self.assertEqual(first_snap["captured_at"], "2026-10-01T09:30:00+05:30")
        self.assertEqual(first_snap["extra_data"]["vehicle"], "Interceptor 650")
        self.assertEqual(first_snap["extra_data"]["source"], "csv_import")
        self.assertEqual(first_snap["extra_data"]["unit_price"], 102.0)
        self.assertTrue(first_snap["extra_data"]["is_full_tank"])
        self.assertEqual(first_snap["extra_data"]["notes"], "First tank")
        self.assertEqual(first_snap["extra_data"]["legacy_unique_id"], 101)
        self.assertEqual(first_snap["extra_data"]["legacy_guid"], "guid-001")

        self.assertEqual(latest_snap["captured_at"], "2026-10-10T18:45:00+05:30")
        self.assertFalse(latest_snap["extra_data"]["is_full_tank"])
        self.assertEqual(latest_snap["extra_data"]["distance_delta"], 200.0)
        self.assertEqual(latest_snap["extra_data"]["km_per_l"], 40.0)

        # Second import must skip existing GUIDs cleanly
        summary_2 = import_csv_to_db(csv_path, db_path=self.db_path)
        self.assertEqual(summary_2, {"imported": 0, "skipped": 3, "total_in_csv": 3})


if __name__ == "__main__":
    unittest.main()
