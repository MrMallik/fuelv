from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.db.repository import SnapshotRepository
from backend.extraction.base import get_extractor, normalize_extracted_json


class TestFuelVBackend(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_fuelv.db"
        self.repo = SnapshotRepository(db_path=self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_strict_extraction_json_normalization(self) -> None:
        raw = {
            "odometer_reading": "14520.5",
            "total_expense": 550,
            "fuel_dispensed": "5.3819",
            "unexpected_extra_field": "ignored",
        }
        normalized = normalize_extracted_json(raw)
        self.assertEqual(
            normalized,
            {
                "odometer_reading": 14520.5,
                "total_expense": 550.0,
                "fuel_dispensed": 5.382,
            },
        )
        self.assertEqual(
            set(normalized.keys()),
            {"odometer_reading", "total_expense", "fuel_dispensed"},
        )

    def test_swappable_extractor_factory(self) -> None:
        with patch.dict("os.environ", {"EXTRACTOR_BACKEND": "gemini"}, clear=False):
            ext = get_extractor()
            self.assertEqual(ext.model_id, "gemini-3.5-flash-lite")

        with patch.dict(
            "os.environ",
            {
                "EXTRACTOR_BACKEND": "custom",
                "CUSTOM_EXTRACTOR_MODEL_ID": "my-finetuned-yolo-ocr-v1",
            },
        ):
            ext_custom = get_extractor()
            self.assertEqual(ext_custom.model_id, "my-finetuned-yolo-ocr-v1")

    def test_insert_and_entry_mode_detection(self) -> None:
        # 1. Pure manual entry
        s1 = self.repo.insert_snapshot(
            captured_at="2026-10-05T09:15:00+05:30",
            odometer_reading=12000.0,
            total_expense=500.0,
            fuel_dispensed=4.9,
            extra_data={"vehicle": "bike", "station": "Shell"},
        )
        self.assertEqual(s1["entry_mode"], "manual")
        self.assertEqual(s1["extra_data"]["vehicle"], "bike")

        # 2. Unedited vision entry
        raw_json = {
            "odometer_reading": 12250.0,
            "total_expense": 600.0,
            "fuel_dispensed": 5.88,
        }
        s2 = self.repo.insert_snapshot(
            captured_at="2026-11-02T18:30:00+05:30",
            odometer_reading=12250.0,
            total_expense=600.0,
            fuel_dispensed=5.88,
            odo_image_path="images/odo1.jpg",
            pump_image_path="images/pump1.jpg",
            extractor_model="gemini-2.5-flash",
            raw_extraction_json=raw_json,
        )
        self.assertEqual(s2["entry_mode"], "vision")
        self.assertEqual(s2["odo_image_url"], "/api/images/odo1.jpg")

        # 3. Vision entry edited by user (ground-truth diff for future fine-tuning)
        s3 = self.repo.insert_snapshot(
            captured_at="2026-11-15T10:00:00+05:30",
            odometer_reading=12510.5,  # User corrected from 12510.0
            total_expense=550.0,
            fuel_dispensed=5.38,
            extractor_model="gemini-2.5-flash",
            raw_extraction_json={
                "odometer_reading": 12510.0,
                "total_expense": 550.0,
                "fuel_dispensed": 5.38,
            },
        )
        self.assertEqual(s3["entry_mode"], "vision_edited")

        # Verify automatic km/L and distance delta calculation
        # s2 odometer was 12250.0; s3 odometer is 12510.5 -> delta = 260.5 km; fuel = 5.38 L -> 260.5 / 5.38 = 48.42 km/L
        self.assertEqual(s3["extra_data"]["previous_odometer"], 12250.0)
        self.assertEqual(s3["extra_data"]["distance_delta"], 260.5)
        self.assertEqual(s3["extra_data"]["km_per_l"], 48.42)

        # Verify get_latest_snapshot
        latest = self.repo.get_latest_snapshot()
        self.assertIsNotNone(latest)
        self.assertEqual(latest["id"], s3["id"])
        self.assertEqual(latest["odometer_reading"], 12510.5)

        # Verify update_snapshot: edit odometer to 12520.0 (delta = 270.0, 270.0 / 5.38 = 50.19 km/L)
        updated_s3 = self.repo.update_snapshot(
            s3["id"],
            odometer_reading=12520.0,
        )
        self.assertIsNotNone(updated_s3)
        self.assertEqual(updated_s3["odometer_reading"], 12520.0)
        self.assertEqual(updated_s3["extra_data"]["distance_delta"], 270.0)
        self.assertEqual(updated_s3["extra_data"]["km_per_l"], 50.19)

        # Verify grouping by month
        by_month = self.repo.get_grouped_snapshots("month")
        self.assertEqual(by_month["total_snapshots"], 3)
        self.assertEqual(by_month["overall_expense"], 1650.0)
        self.assertEqual(by_month["overall_distance"], 520.0)
        self.assertEqual(len(by_month["groups"]), 2)
        self.assertEqual(by_month["groups"][0]["period"], "2026-11")
        self.assertEqual(by_month["groups"][0]["snapshot_count"], 2)
        self.assertEqual(by_month["groups"][0]["total_expense"], 1150.0)
        self.assertEqual(by_month["groups"][1]["period"], "2026-10")
        self.assertEqual(by_month["groups"][1]["snapshot_count"], 1)

        # Verify grouping by year
        by_year = self.repo.get_grouped_snapshots("year")
        self.assertEqual(len(by_year["groups"]), 1)
        self.assertEqual(by_year["groups"][0]["period"], "2026")
        self.assertEqual(by_year["groups"][0]["total_expense"], 1650.0)

        # Verify grouping by week
        by_week = self.repo.get_grouped_snapshots("week")
        self.assertEqual(len(by_week["groups"]), 3)

        # Verify grouping by day
        by_day = self.repo.get_grouped_snapshots("day")
        self.assertEqual(len(by_day["groups"]), 3)
        self.assertEqual(by_day["total_snapshots"], 3)

    def test_verify_write_access_dependency(self) -> None:
        try:
            from backend.main import verify_write_access
            from fastapi import HTTPException
        except ImportError:
            return

        # 1. No key configured -> open access
        with patch.dict("os.environ", {"FUELV_WRITE_KEY": ""}, clear=False):
            verify_write_access(None)
            verify_write_access("anything")

        # 2. Key configured -> requires matching UUID
        secret_uuid = "550e8400-e29b-41d4-a716-446655440000"
        with patch.dict("os.environ", {"FUELV_WRITE_KEY": secret_uuid}, clear=False):
            # Missing key raises 401
            with self.assertRaises(HTTPException) as ctx:
                verify_write_access(None)
            self.assertEqual(ctx.exception.status_code, 401)

            # Invalid format raises 401
            with self.assertRaises(HTTPException) as ctx:
                verify_write_access("not-a-valid-uuid")
            self.assertEqual(ctx.exception.status_code, 401)

            # Different valid UUID raises 401
            with self.assertRaises(HTTPException) as ctx:
                verify_write_access("00000000-0000-0000-0000-000000000000")
            self.assertEqual(ctx.exception.status_code, 401)

            # Valid matching key passes (case-insensitive)
            verify_write_access(secret_uuid)
            verify_write_access(secret_uuid.upper())


if __name__ == "__main__":
    unittest.main()
