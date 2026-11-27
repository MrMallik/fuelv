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
from backend.storage.r2 import R2Storage


class TestFeatureSnapshotCrud(unittest.TestCase):
    """Feature tests covering the full snapshot CRUD lifecycle, image uploads,
    entry mode provenance, cascade cleanup, and latest snapshot queries.
    """

    def setUp(self) -> None:
        self.env_patcher = patch.dict(
            os.environ,
            {
                "R2_ACCOUNT_ID": "test_account_id",
                "R2_ACCESS_KEY_ID": "test_access_key",
                "R2_SECRET_ACCESS_KEY": "test_secret_key",
                "R2_BUCKET_NAME": "public-bucket",
                "R2_KEY_PREFIX": "fvs",
                "R2_PUBLIC_BASE_URL": "https://r2.example.com",
                "FUELV_WRITE_KEY": "",
                "FUELV_DISABLE_AUTH": "1",
                "RATE_LIMIT_ENABLED": "false",
            },
            clear=False,
        )
        self.env_patcher.start()

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_crud.db"
        self.img_dir = Path(self.tmp_dir.name) / "images"
        self.img_dir.mkdir(parents=True, exist_ok=True)
        self.r2 = R2Storage()
        self.app = create_app(
            db_path=self.db_path,
            images_dir=self.img_dir,
            r2_storage=self.r2,
            validate_on_startup=False,
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.r2.close()
        self.env_patcher.stop()
        self.tmp_dir.cleanup()

    def test_create_manual_snapshot_json_lifecycle(self) -> None:
        """Creating a JSON snapshot without raw extraction defaults to manual mode and computes no deltas for first log."""
        res = self.client.post(
            "/api/snapshots",
            json={
                "captured_at": "2026-11-01T08:00:00+05:30",
                "odometer_reading": 10000.0,
                "total_expense": 500.0,
                "fuel_dispensed": 4.9,
            },
        )
        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertTrue(body["id"])
        self.assertEqual(body["entry_mode"], "manual")
        self.assertEqual(body["odometer_reading"], 10000.0)
        self.assertEqual(body["total_expense"], 500.0)
        self.assertEqual(body["fuel_dispensed"], 4.9)
        self.assertIsNone(body["odo_image_url"])
        self.assertIsNone(body["pump_image_url"])
        self.assertNotIn("previous_odometer", body["extra_data"])

    def test_create_multipart_snapshot_uploads_to_r2_and_resolves_urls(self) -> None:
        """Creating a multipart snapshot uploads both photos to R2 under fvs/ with zero local disk writes."""
        uploaded: dict[str, tuple[bytes, str]] = {}

        def fake_upload(data: bytes, filename: str, content_type: str = "image/jpeg", max_retries: int = 3) -> str:
            key = self.r2.build_object_key(filename)
            uploaded[key] = (data, content_type)
            return key

        with patch.object(self.r2, "upload_image", side_effect=fake_upload):
            res = self.client.post(
                "/api/snapshots",
                data={
                    "captured_at": "2026-11-05T14:30:00+05:30",
                    "odometer_reading": "14520.5",
                    "total_expense": "550.0",
                    "fuel_dispensed": "5.38",
                    "extractor_model": "gemini-3.5-flash-lite",
                    "raw_extraction_json": json.dumps(
                        {
                            "odometer_reading": 14520.5,
                            "total_expense": 550.0,
                            "fuel_dispensed": 5.38,
                        }
                    ),
                    "extra_data": json.dumps({"vehicle": "My Bike", "station": "IOCL"}),
                },
                files={
                    "odo_image": ("dashboard.jpg", b"odo-jpeg-bytes", "image/jpeg"),
                    "pump_image": ("dispenser.png", b"pump-png-bytes", "image/png"),
                },
            )

        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertEqual(body["entry_mode"], "vision")
        self.assertEqual(body["extractor_model"], "gemini-3.5-flash-lite")
        self.assertTrue(body["odo_image_path"].startswith("fvs/"))
        self.assertTrue(body["odo_image_path"].endswith("_odo.jpg"))
        self.assertTrue(body["pump_image_path"].startswith("fvs/"))
        self.assertTrue(body["pump_image_path"].endswith("_pump.png"))
        self.assertEqual(body["odo_image_url"], f"https://r2.example.com/{body['odo_image_path']}")
        self.assertEqual(body["pump_image_url"], f"https://r2.example.com/{body['pump_image_path']}")
        self.assertEqual(body["extra_data"]["vehicle"], "My Bike")
        self.assertEqual(body["extra_data"]["station"], "IOCL")
        self.assertEqual(len(uploaded), 2)
        self.assertEqual(list(self.img_dir.iterdir()), [])

    def test_entry_mode_provenance_detection_and_explicit_override(self) -> None:
        """Unchanged vision numbers stay 'vision'; edited numbers switch to 'vision_edited'; explicit mode wins."""
        repo: SnapshotRepository = self.app.state.repo

        s_vision = repo.insert_snapshot(
            captured_at="2026-11-01T09:00:00Z",
            odometer_reading=1000.0,
            total_expense=100.0,
            fuel_dispensed=2.0,
            raw_extraction_json={
                "odometer_reading": 1000.00001,
                "total_expense": 100.0,
                "fuel_dispensed": 2.0,
            },
        )
        self.assertEqual(s_vision["entry_mode"], "vision")

        s_edited = repo.insert_snapshot(
            captured_at="2026-11-02T09:00:00Z",
            odometer_reading=1205.5,
            total_expense=100.0,
            fuel_dispensed=2.0,
            raw_extraction_json={
                "odometer_reading": 1200.5,
                "total_expense": 100.0,
                "fuel_dispensed": 2.0,
            },
        )
        self.assertEqual(s_edited["entry_mode"], "vision_edited")

        s_explicit = repo.insert_snapshot(
            captured_at="2026-11-03T09:00:00Z",
            odometer_reading=1400.0,
            total_expense=100.0,
            fuel_dispensed=2.0,
            entry_mode="manual",
            raw_extraction_json={
                "odometer_reading": 1400.0,
                "total_expense": 100.0,
                "fuel_dispensed": 2.0,
            },
        )
        self.assertEqual(s_explicit["entry_mode"], "manual")

    def test_update_snapshot_transitions_vision_to_vision_edited_and_handles_missing_id(self) -> None:
        """Editing numeric fields on a vision log flips entry_mode to vision_edited; unknown IDs return 404."""
        create_res = self.client.post(
            "/api/snapshots",
            json={
                "captured_at": "2026-11-10T10:00:00Z",
                "odometer_reading": 5000.0,
                "total_expense": 300.0,
                "fuel_dispensed": 3.0,
                "raw_extraction_json": {
                    "odometer_reading": 5000.0,
                    "total_expense": 300.0,
                    "fuel_dispensed": 3.0,
                },
            },
        )
        self.assertEqual(create_res.status_code, 201)
        snap_id = create_res.json()["id"]
        self.assertEqual(create_res.json()["entry_mode"], "vision")

        # Updating only extra_data keeps entry_mode as vision
        meta_res = self.client.put(
            f"/api/snapshots/{snap_id}",
            json={"extra_data": {"notes": "verified receipt"}},
        )
        self.assertEqual(meta_res.status_code, 200)
        self.assertEqual(meta_res.json()["entry_mode"], "vision")
        self.assertEqual(meta_res.json()["extra_data"]["notes"], "verified receipt")

        # Updating odometer flips entry_mode to vision_edited
        edit_res = self.client.put(
            f"/api/snapshots/{snap_id}",
            json={"odometer_reading": 5012.5},
        )
        self.assertEqual(edit_res.status_code, 200)
        self.assertEqual(edit_res.json()["entry_mode"], "vision_edited")
        self.assertEqual(edit_res.json()["odometer_reading"], 5012.5)

        # Unknown ID returns 404
        missing_res = self.client.put("/api/snapshots/nonexistent-id", json={"total_expense": 10.0})
        self.assertEqual(missing_res.status_code, 404)

    def test_create_snapshot_r2_rollback_cascade_on_db_or_second_upload_failure(self) -> None:
        """If the second upload or DB insert fails after first R2 upload succeeds, uploaded R2 keys are deleted."""
        deleted_keys: list[str] = []

        def fake_upload_fail_on_pump(
            data: bytes, filename: str, content_type: str = "image/jpeg", max_retries: int = 3
        ) -> str:
            if "_pump" in filename:
                raise RuntimeError("Simulated R2 pump upload failure")
            return self.r2.build_object_key(filename)

        def fake_delete(object_key: str | None) -> bool:
            if object_key:
                deleted_keys.append(object_key)
            return True

        with patch("backend.main.logger"), patch.object(
            self.r2, "upload_image", side_effect=fake_upload_fail_on_pump
        ), patch.object(self.r2, "delete_image", side_effect=fake_delete):
            res = self.client.post(
                "/api/snapshots",
                data={
                    "odometer_reading": "1000",
                    "total_expense": "100",
                    "fuel_dispensed": "1.0",
                },
                files={
                    "odo_image": ("odo.jpg", b"odo-bytes", "image/jpeg"),
                    "pump_image": ("pump.jpg", b"pump-bytes", "image/jpeg"),
                },
            )

        self.assertEqual(res.status_code, 502)
        self.assertEqual(len(deleted_keys), 1)
        self.assertTrue(deleted_keys[0].endswith("_odo.jpg"))
        self.assertIsNone(self.app.state.repo.get_latest_snapshot())

        # Also verify rollback when both R2 uploads succeed but DB insert raises
        deleted_keys.clear()
        with patch("backend.main.logger"), patch.object(
            self.r2,
            "upload_image",
            side_effect=lambda data, filename, content_type="image/jpeg", max_retries=3: self.r2.build_object_key(
                filename
            ),
        ), patch.object(self.r2, "delete_image", side_effect=fake_delete), patch.object(
            self.app.state.repo, "insert_snapshot", side_effect=RuntimeError("DB write failure")
        ):
            res_db_fail = self.client.post(
                "/api/snapshots",
                data={
                    "odometer_reading": "1000",
                    "total_expense": "100",
                    "fuel_dispensed": "1.0",
                },
                files={
                    "odo_image": ("odo.jpg", b"odo-bytes", "image/jpeg"),
                    "pump_image": ("pump.jpg", b"pump-bytes", "image/jpeg"),
                },
            )

        self.assertEqual(res_db_fail.status_code, 502)
        self.assertEqual(len(deleted_keys), 2)

    def test_delete_snapshot_cascades_r2_cleanup_and_rejects_second_delete(self) -> None:
        """Deleting a snapshot removes the DB record, cleans up both R2 objects, and 404s on repeat."""
        with patch.object(
            self.r2,
            "upload_image",
            side_effect=lambda data, filename, content_type="image/jpeg", max_retries=3: self.r2.build_object_key(
                filename
            ),
        ):
            create_res = self.client.post(
                "/api/snapshots",
                data={
                    "odometer_reading": "2500",
                    "total_expense": "250",
                    "fuel_dispensed": "2.5",
                },
                files={
                    "odo_image": ("odo.jpg", b"odo", "image/jpeg"),
                    "pump_image": ("pump.jpg", b"pump", "image/jpeg"),
                },
            )
        created = create_res.json()
        snap_id = created["id"]

        deleted_keys: list[str | None] = []
        with patch.object(
            self.r2,
            "delete_image",
            side_effect=lambda key: deleted_keys.append(key) or True,
        ):
            del_res = self.client.delete(f"/api/snapshots/{snap_id}")

        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(del_res.json(), {"deleted": True, "id": snap_id})
        self.assertEqual(
            deleted_keys,
            [created["odo_image_path"], created["pump_image_path"]],
        )
        self.assertIsNone(self.app.state.repo.get_snapshot(snap_id))

        # Second delete returns 404
        second_del = self.client.delete(f"/api/snapshots/{snap_id}")
        self.assertEqual(second_del.status_code, 404)

    def test_latest_snapshot_query_orders_by_captured_at_then_created_at(self) -> None:
        """GET /api/snapshots/latest returns the most recent snapshot chronologically, or None when empty."""
        empty_res = self.client.get("/api/snapshots/latest")
        self.assertEqual(empty_res.status_code, 200)
        self.assertIsNone(empty_res.json()["latest"])

        repo: SnapshotRepository = self.app.state.repo
        older = repo.insert_snapshot(
            captured_at="2026-11-01T10:00:00Z",
            odometer_reading=1000.0,
            total_expense=100.0,
            fuel_dispensed=1.0,
        )
        newest = repo.insert_snapshot(
            captured_at="2026-11-20T10:00:00Z",
            odometer_reading=1500.0,
            total_expense=200.0,
            fuel_dispensed=2.0,
        )
        # Backdated insert must NOT displace newest
        backdated = repo.insert_snapshot(
            captured_at="2026-10-15T10:00:00Z",
            odometer_reading=800.0,
            total_expense=90.0,
            fuel_dispensed=0.9,
        )
        self.assertNotEqual(older["id"], newest["id"])
        self.assertNotEqual(backdated["id"], newest["id"])

        latest_res = self.client.get("/api/snapshots/latest")
        self.assertEqual(latest_res.status_code, 200)
        self.assertEqual(latest_res.json()["latest"]["id"], newest["id"])
        self.assertEqual(latest_res.json()["latest"]["odometer_reading"], 1500.0)

        # Tie-break on identical captured_at uses created_at DESC
        same_ts = repo.insert_snapshot(
            captured_at="2026-11-20T10:00:00Z",
            odometer_reading=1505.0,
            total_expense=50.0,
            fuel_dispensed=0.5,
        )
        with repo._connect() as conn:
            conn.execute(
                "UPDATE fuel_snapshots SET created_at = '2026-11-20T10:00:00Z' WHERE id = ?",
                (newest["id"],),
            )
            conn.execute(
                "UPDATE fuel_snapshots SET created_at = '2026-11-20T10:00:01Z' WHERE id = ?",
                (same_ts["id"],),
            )
            conn.commit()

        latest_tie = self.client.get("/api/snapshots/latest")
        self.assertEqual(latest_tie.status_code, 200)
        self.assertEqual(latest_tie.json()["latest"]["id"], same_ts["id"])

    def test_invalid_request_bodies_rejected_with_400(self) -> None:
        """Malformed JSON bodies or invalid multipart JSON metadata return 400."""
        bad_json_res = self.client.post(
            "/api/snapshots",
            content=b"[1, 2, 3]",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(bad_json_res.status_code, 400)

        bad_extra_res = self.client.post(
            "/api/snapshots",
            data={
                "odometer_reading": "100",
                "total_expense": "10",
                "fuel_dispensed": "1",
                "extra_data": "{not-valid-json",
            },
        )
        self.assertEqual(bad_extra_res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
