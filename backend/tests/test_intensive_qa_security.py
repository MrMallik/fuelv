from __future__ import annotations

import hmac
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.db.repository import SnapshotRepository
from backend.main import create_app, verify_write_access


class TestSecurityAndAccessControl(unittest.TestCase):
    """Rigorous Offensive and Defensive Security Tests for FuelV."""

    def setUp(self) -> None:
        self.test_env_patcher = patch.dict(
            os.environ,
            {
                "FUELV_DISABLE_AUTH": "0",
                "R2_ACCOUNT_ID": "test_account_12345",
                "R2_ACCESS_KEY_ID": "test_access_key",
                "R2_SECRET_ACCESS_KEY": "test_secret_key",
                "R2_BUCKET_NAME": "public-bucket",
                "R2_PUBLIC_BASE_URL": "https://r2.pritabrata.com",
            },
        )
        self.test_env_patcher.start()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_security.db"
        self.img_dir = Path(self.tmp_dir.name) / "images"
        self.img_dir.mkdir(parents=True, exist_ok=True)
        self.test_write_key = str(uuid.uuid4())

        self.app = create_app(db_path=self.db_path, images_dir=self.img_dir)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.test_env_patcher.stop()
        self.tmp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Write-Key Enforcement Across All Mutating Endpoints
    # -------------------------------------------------------------------------
    def test_mutating_endpoints_require_write_key_when_configured(self) -> None:
        """Verify that all mutating endpoints reject unauthorized requests when write protection is active."""
        with patch.dict(os.environ, {"FUELV_WRITE_KEY": self.test_write_key}):
            # Test 1: POST /api/extract
            res = self.client.post("/api/extract", files={"odo_image": ("odo.jpg", b"fake_bytes", "image/jpeg")})
            self.assertEqual(res.status_code, 401)
            self.assertIn("write access key", res.json()["detail"].lower())

            # Test 2: POST /api/snapshots
            res = self.client.post(
                "/api/snapshots",
                json={"odometer_reading": 10000.0, "total_expense": 500.0, "fuel_dispensed": 5.0},
            )
            self.assertEqual(res.status_code, 401)

            # Test 3: PUT /api/snapshots/{id}
            res = self.client.put(
                "/api/snapshots/some-id",
                json={"odometer_reading": 10500.0},
            )
            self.assertEqual(res.status_code, 401)

            # Test 4: DELETE /api/snapshots/{id}
            res = self.client.delete("/api/snapshots/some-id")
            self.assertEqual(res.status_code, 401)

    def test_non_mutating_endpoints_open_when_write_key_configured(self) -> None:
        """Verify read-only endpoints remain accessible without X-FuelV-Key header."""
        with patch.dict(os.environ, {"FUELV_WRITE_KEY": self.test_write_key}):
            # GET /api/health
            res = self.client.get("/api/health")
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.json()["write_protection_enabled"])

            # GET /api/snapshots/latest
            res = self.client.get("/api/snapshots/latest")
            self.assertEqual(res.status_code, 200)

            # GET /api/snapshots/grouped
            res = self.client.get("/api/snapshots/grouped")
            self.assertEqual(res.status_code, 200)

    def test_all_mutating_endpoints_open_when_no_key_configured(self) -> None:
        """When FUELV_WRITE_KEY is empty/unset, mutating endpoints allow open access."""
        with patch.dict(os.environ, {"FUELV_WRITE_KEY": ""}):
            # Insert snapshot without header
            res = self.client.post(
                "/api/snapshots",
                json={"odometer_reading": 12000.0, "total_expense": 400.0, "fuel_dispensed": 4.0},
            )
            self.assertEqual(res.status_code, 201)
            snap_id = res.json()["id"]

            # Edit snapshot without header
            res = self.client.put(f"/api/snapshots/{snap_id}", json={"total_expense": 450.0})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["total_expense"], 450.0)

            # Delete snapshot without header
            res = self.client.delete(f"/api/snapshots/{snap_id}")
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.json()["deleted"])

    def test_write_key_malformed_and_spoofed_variants(self) -> None:
        """Verify timing-attack resistance and rejection of malformed, empty, partial, and spoofed keys."""
        with patch.dict(os.environ, {"FUELV_WRITE_KEY": self.test_write_key}):
            invalid_headers = [
                "",                                        # Empty string
                "    ",                                    # Pure whitespace
                "admin",                                   # Generic string
                "550e8400-e29b-41d4",                      # Truncated UUID
                "550e8400-e29b-41d4-a716-446655440000",   # Valid UUID, wrong secret
                "12345678-1234-5678-1234-56781234567g",   # Hex error ('g')
                "' OR '1'='1",                             # SQLi attempt in header
                f"{self.test_write_key}extra",             # Appended payload
                f"../{self.test_write_key}",               # Path traversal attempt in key
            ]

            payload = {"odometer_reading": 1000.0, "total_expense": 50.0, "fuel_dispensed": 2.0}
            for bad_key in invalid_headers:
                res = self.client.post(
                    "/api/snapshots",
                    headers={"X-FuelV-Key": bad_key},
                    json=payload,
                )
                self.assertEqual(
                    res.status_code,
                    401,
                    f"Expected 401 for invalid header: {bad_key!r}, got {res.status_code}",
                )

            # Valid key with different case & surrounding whitespace & curly braces
            valid_headers = [
                self.test_write_key,
                self.test_write_key.upper(),
                f"  {self.test_write_key}  ",
                f"{{{self.test_write_key}}}",
            ]
            for good_key in valid_headers:
                res = self.client.post(
                    "/api/snapshots",
                    headers={"X-FuelV-Key": good_key},
                    json=payload,
                )
                self.assertEqual(
                    res.status_code,
                    201,
                    f"Expected 201 for valid header variant: {good_key!r}, got {res.status_code}",
                )

    def test_timing_attack_resistance_via_constant_time_comparison(self) -> None:
        """Verify that verify_write_access uses constant-time comparison (hmac.compare_digest)."""
        with patch.object(hmac, "compare_digest", wraps=hmac.compare_digest) as mock_compare:
            with patch.dict(os.environ, {"FUELV_WRITE_KEY": self.test_write_key}):
                res = self.client.post(
                    "/api/snapshots",
                    headers={"X-FuelV-Key": str(uuid.uuid4())},
                    json={"odometer_reading": 100.0, "total_expense": 10.0, "fuel_dispensed": 1.0},
                )
                self.assertEqual(res.status_code, 401)
                self.assertTrue(mock_compare.called, "hmac.compare_digest must be invoked for timing safety")

    # -------------------------------------------------------------------------
    # 2. Path Traversal, Stateless Extract & R2 Upload Sanitization
    # -------------------------------------------------------------------------
    def test_image_upload_path_traversal_and_extension_sanitization(self) -> None:
        """1) /api/extract must be purely in-memory and NEVER write images to local disk or R2.
        2) POST /api/snapshots with multipart files must sanitize malicious filenames, store in R2 `fvs/`,
           resolve to https://r2.pritabrata.com/fvs/..., and NEVER write to local disk.
        """
        headers = {"X-FuelV-Key": self.test_write_key}
        fake_ext = MagicMock()
        fake_ext.model_id = "gemini-3.5-flash-lite"
        fake_ext.extract.return_value = {
            "odometer_reading": 14520.5,
            "total_expense": 550.0,
            "fuel_dispensed": 5.38,
        }
        with patch.dict(os.environ, {"FUELV_WRITE_KEY": self.test_write_key}):
            with patch("backend.main.get_extractor", return_value=fake_ext), patch.object(
                self.app.state.r2,
                "upload_image",
                side_effect=lambda data, filename, content_type="image/jpeg", max_retries=3: self.app.state.r2.build_object_key(filename),
            ):
                malicious_filenames = [
                    "../../../../etc/passwd.exe",
                    "../../main.py",
                    "..\\..\\windows\\system32\\cmd.exe",
                    "shell.php",
                    "exploit.py",
                    "test.jpg/../../evil.sh",
                ]

                for idx, fname in enumerate(malicious_filenames):
                    # 1. Stateless /api/extract must not write any files anywhere
                    files = {"odo_image": (fname, b"fake_content", "image/jpeg")}
                    res = self.client.post("/api/extract", headers=headers, files=files)
                    self.assertEqual(res.status_code, 200)
                    self.assertIsNone(res.json()["meta"]["odo_image_path"])
                    self.assertEqual(list(self.img_dir.iterdir()), [])

                    # 2. Final confirmed save (POST /api/snapshots) sanitizes extension & stores under fvs/
                    snap_res = self.client.post(
                        "/api/snapshots",
                        headers=headers,
                        data={
                            "odometer_reading": str(10000 + idx * 100),
                            "total_expense": "500.0",
                            "fuel_dispensed": "5.0",
                        },
                        files={"odo_image": (fname, b"fake_image_bytes", "image/jpeg")},
                    )
                    self.assertEqual(snap_res.status_code, 201)
                    body = snap_res.json()
                    saved_r2_key = body["odo_image_path"]
                    self.assertIsNotNone(saved_r2_key)
                    self.assertTrue(saved_r2_key.startswith("fvs/"))
                    self.assertTrue(saved_r2_key.endswith("_odo.jpg"))
                    self.assertNotIn("..", saved_r2_key)
                    self.assertTrue(
                        body["odo_image_url"].startswith("https://r2.pritabrata.com/fvs/")
                    )

                    # Confirm zero files were written to the local device/container disk
                    self.assertEqual(list(self.img_dir.iterdir()), [])

    def test_r2_storage_sigv4_and_prod_migration_script(self) -> None:
        """Verify Cloudflare R2 SigV4 signing and production migration of 2 logs (4 captures) to fvs/."""
        from backend.scripts.migrate_images_to_r2 import migrate_local_images_to_r2
        from backend.storage.r2 import R2Storage

        # 1. Verify SigV4 header construction for public-bucket/fvs/
        r2 = R2Storage(
            account_id="d2fe1099fc2a477abb4fd83b85d0cfe6",
            access_key_id="test_access_key",
            secret_access_key="test_secret_key",
            bucket_name="public-bucket",
            key_prefix="fvs",
            public_base_url="https://r2.pritabrata.com",
        )
        self.assertTrue(r2.is_configured)
        url, signed_headers = r2._build_signed_headers(
            method="PUT",
            object_key="fvs/sample_odo.jpg",
            payload=b"jpeg_bytes",
            extra_headers={"Content-Type": "image/jpeg"},
        )
        self.assertEqual(
            url,
            "https://d2fe1099fc2a477abb4fd83b85d0cfe6.r2.cloudflarestorage.com/public-bucket/fvs/sample_odo.jpg",
        )
        self.assertIn("AWS4-HMAC-SHA256 Credential=test_access_key/", signed_headers["Authorization"])
        self.assertIn("auto/s3/aws4_request", signed_headers["Authorization"])

        # 2. Simulate the 2 production logs (4 local captures) and migrate them to R2 fvs/
        files_created: list[Path] = []
        for i in (1, 2):
            odo_file = self.img_dir / f"prod_log{i}_odo.jpg"
            pump_file = self.img_dir / f"prod_log{i}_pump.jpg"
            odo_file.write_bytes(f"odo_bytes_{i}".encode())
            pump_file.write_bytes(f"pump_bytes_{i}".encode())
            files_created.extend([odo_file, pump_file])

            self.app.state.repo.insert_snapshot(
                captured_at=f"2026-11-1{i}T10:00:00+05:30",
                odometer_reading=10000.0 + i * 250.0,
                total_expense=500.0,
                fuel_dispensed=5.0,
                odo_image_path=f"images/prod_log{i}_odo.jpg",
                pump_image_path=f"images/prod_log{i}_pump.jpg",
            )

        uploaded_store: dict[str, bytes] = {}

        def fake_upload(data: bytes, filename: str, content_type: str = "image/jpeg", max_retries: int = 3) -> str:
            key = r2.build_object_key(filename)
            uploaded_store[key] = data
            return key

        def fake_head(object_key: str) -> bool:
            return object_key in uploaded_store

        with patch.object(r2, "upload_image", side_effect=fake_upload), patch.object(
            r2, "head_image", side_effect=fake_head
        ):
            summary = migrate_local_images_to_r2(
                db_path=self.db_path,
                images_dir=self.img_dir,
                storage=r2,
                delete_local=True,
                dry_run=False,
            )

        self.assertEqual(summary["updated_rows"], 2)
        self.assertEqual(summary["migrated_files"], 4)
        self.assertEqual(summary["deleted_local_files"], 4)
        self.assertEqual(summary["errors"], [])

        # Confirm all 4 local files were deleted after R2 verification
        for f in files_created:
            self.assertFalse(f.exists())

        # Confirm DB rows now point to fvs/... and resolve to https://r2.pritabrata.com/fvs/...
        grouped = self.app.state.repo.get_grouped_snapshots("month")
        all_snaps = [s for g in grouped["groups"] for s in g["snapshots"]]
        self.assertEqual(len(all_snaps), 2)
        for snap in all_snaps:
            self.assertTrue(snap["odo_image_path"].startswith("fvs/prod_log"))
            self.assertTrue(snap["pump_image_path"].startswith("fvs/prod_log"))
            self.assertTrue(snap["odo_image_url"].startswith("https://r2.pritabrata.com/fvs/prod_log"))
            self.assertTrue(snap["pump_image_url"].startswith("https://r2.pritabrata.com/fvs/prod_log"))

    def test_api_images_path_traversal_rejection(self) -> None:
        """Static file mount for images must block directory escape attempts."""
        traversal_attempts = [
            "/api/images/../.env",
            "/api/images/..%2f.env",
            "/api/images/%2e%2e%2ffuelv.db",
            "/api/images/../../backend/main.py",
        ]
        for path in traversal_attempts:
            res = self.client.get(path)
            # Must either return 404 Not Found or default SPA index.html, never leaking backend files
            self.assertNotIn(b"FUELV_WRITE_KEY", res.content)
            self.assertNotIn(b"FASTAPI", res.content)
            self.assertNotIn(b"import sqlite3", res.content)

    def test_spa_catch_all_path_traversal_defense(self) -> None:
        """SPA catch-all /{full_path:path} must never leak .env, database, or backend source code."""
        traversal_attempts = [
            "/.env",
            "/..%2f.env",
            "/backend/main.py",
            "/backend/db/schema.sql",
            "/data/fuelv.db",
            "/etc/passwd",
            "/frontend/../backend/main.py",
        ]
        for path in traversal_attempts:
            res = self.client.get(path)
            # Ensure internal secrets and code are not exposed
            self.assertNotIn(b"FUELV_WRITE_KEY", res.content)
            self.assertNotIn(b"SnapshotRepository", res.content)
            self.assertNotIn(b"CREATE TABLE", res.content)

    # -------------------------------------------------------------------------
    # 3. SQL Injection Defense
    # -------------------------------------------------------------------------
    def test_sqli_defense_in_route_parameters_and_queries(self) -> None:
        """SQL injection attempts in snapshot_id and query params must not affect the database."""
        headers = {"X-FuelV-Key": self.test_write_key}
        with patch.dict(os.environ, {"FUELV_WRITE_KEY": self.test_write_key}):
            # Create a legit snapshot first
            snap = self.client.post(
                "/api/snapshots",
                headers=headers,
                json={"odometer_reading": 5000.0, "total_expense": 200.0, "fuel_dispensed": 2.0},
            ).json()

            sqli_payloads = [
                "' OR '1'='1",
                "'; DROP TABLE fuel_snapshots; --",
                "1' UNION SELECT 1,2,3,4,5,6,7,8,9,10,11,12,13--",
                "admin'--",
            ]

            for sqli in sqli_payloads:
                # 1. SQLi in snapshot_id for PUT
                put_res = self.client.put(
                    f"/api/snapshots/{sqli}",
                    headers=headers,
                    json={"total_expense": 999.0},
                )
                self.assertEqual(put_res.status_code, 404)

                # 2. SQLi in snapshot_id for DELETE
                del_res = self.client.delete(f"/api/snapshots/{sqli}", headers=headers)
                self.assertEqual(del_res.status_code, 404)

                # 3. SQLi in group_by query param
                group_res = self.client.get(f"/api/snapshots/grouped?by={sqli}")
                self.assertEqual(group_res.status_code, 422)  # FastAPI Literal validation blocks invalid enum

            # Confirm original snapshot still intact and table exists
            verify_res = self.client.get(f"/api/snapshots/latest")
            self.assertEqual(verify_res.status_code, 200)
            self.assertEqual(verify_res.json()["latest"]["id"], snap["id"])

    def test_slowapi_rate_limiting_enforcement(self) -> None:
        """Verify that slowapi enforces rate limiting and returns 429 when limits are exceeded."""
        test_app = create_app(db_path=self.db_path)
        client = TestClient(test_app)

        # /api/snapshots/latest is limited to 60/minute
        hit_429 = False
        for _ in range(65):
            res = client.get("/api/snapshots/latest")
            if res.status_code == 429:
                hit_429 = True
                self.assertIn("Rate limit exceeded", res.json()["detail"])
                self.assertEqual(res.headers.get("Retry-After"), "60")
                break

        self.assertTrue(hit_429, "Rate limiter should have returned 429 after exceeding 60 requests/min")

    def test_stored_xss_mime_sanitization_and_r2_key_traversal_defense(self) -> None:
        """Block SVG/HTML MIME types on image upload, normalize octet-stream, and reject key traversal."""
        from backend.storage.r2 import R2Storage, resolve_image_url

        headers = {"X-FuelV-Key": self.test_write_key}
        captured_mimes: list[str] = []

        def fake_upload(data: bytes, filename: str, content_type: str = "image/jpeg", max_retries: int = 3) -> str:
            captured_mimes.append(content_type)
            return self.app.state.r2.build_object_key(filename)

        with patch.dict(os.environ, {"FUELV_WRITE_KEY": self.test_write_key, "FUELV_DISABLE_AUTH": "0"}):
            with patch.object(self.app.state.r2, "upload_image", side_effect=fake_upload):
                # 1. HTML / SVG MIME uploads must be rejected (prevents Stored XSS on public R2 origin)
                for evil_mime in ("text/html", "image/svg+xml", "application/javascript"):
                    res = self.client.post(
                        "/api/snapshots",
                        headers=headers,
                        data={"odometer_reading": "1000", "total_expense": "100", "fuel_dispensed": "1"},
                        files={"odo_image": ("evil.jpg", b"<script>alert(1)</script>", evil_mime)},
                    )
                    self.assertEqual(res.status_code, 400, f"Expected 400 for MIME {evil_mime}")

                # 2. application/octet-stream falls back safely to extension MIME
                res_ok = self.client.post(
                    "/api/snapshots",
                    headers=headers,
                    data={"odometer_reading": "1000", "total_expense": "100", "fuel_dispensed": "1"},
                    files={"odo_image": ("photo.png", b"png_bytes", "application/octet-stream")},
                )
                self.assertEqual(res_ok.status_code, 201)
                self.assertEqual(captured_mimes[-1], "image/png")

            # 3. JSON path traversal / non-http scheme in odo_image_path rejected with 422
            for bad_path in ("fvs/../secret.jpg", "..\\evil.jpg", "javascript:alert(1)"):
                res_bad_json = self.client.post(
                    "/api/snapshots",
                    headers=headers,
                    json={
                        "odometer_reading": 1000.0,
                        "total_expense": 100.0,
                        "fuel_dispensed": 1.0,
                        "odo_image_path": bad_path,
                    },
                )
                self.assertEqual(res_bad_json.status_code, 422)

        # 4. R2Storage cross-platform traversal & delete/resolve protections
        r2 = R2Storage(
            account_id="acct",
            access_key_id="key",
            secret_access_key="secret",
            bucket_name="public-bucket",
            key_prefix="fvs",
            public_base_url="https://r2.pritabrata.com",
        )
        self.assertEqual(r2.build_object_key("..\\..\\windows\\evil.jpg"), "fvs/evil.jpg")
        with self.assertRaises(ValueError):
            r2.build_object_key("..")
        self.assertFalse(r2.delete_image("fvs/../other-prefix/secret.jpg"))
        self.assertIsNone(resolve_image_url("fvs\\..\\secret.jpg"))
        self.assertNotEqual(resolve_image_url("fvs/../other/secret.jpg"), "https://r2.pritabrata.com/fvs/../other/secret.jpg")

    def test_r2_upload_rollback_and_error_redaction_and_retry_policy(self) -> None:
        """Verify compensating R2 delete on second-file failure, redacted 502 error, and no retry on 403."""
        from unittest.mock import MagicMock
        from backend.storage.r2 import R2Storage

        headers = {"X-FuelV-Key": self.test_write_key}
        deleted_keys: list[str | None] = []

        def upload_side_effect(data: bytes, filename: str, content_type: str = "image/jpeg", max_retries: int = 3) -> str:
            if "_pump" in filename:
                raise RuntimeError("R2 PUT failed with HTTP 500: <Error><AWSAccessKeyId>SECRET_ID</AWSAccessKeyId></Error>")
            return self.app.state.r2.build_object_key(filename)

        with patch.dict(os.environ, {"FUELV_WRITE_KEY": self.test_write_key, "FUELV_DISABLE_AUTH": "0"}):
            with patch("backend.main.logger"), patch.object(
                self.app.state.r2, "upload_image", side_effect=upload_side_effect
            ), patch.object(
                self.app.state.r2, "delete_image", side_effect=lambda k: deleted_keys.append(k) or True
            ):
                res = self.client.post(
                    "/api/snapshots",
                    headers=headers,
                    data={"odometer_reading": "1000", "total_expense": "100", "fuel_dispensed": "1"},
                    files={
                        "odo_image": ("odo.jpg", b"odo_bytes", "image/jpeg"),
                        "pump_image": ("pump.jpg", b"pump_bytes", "image/jpeg"),
                    },
                )
                self.assertEqual(res.status_code, 502)
                self.assertNotIn("SECRET_ID", res.json()["detail"])
                self.assertNotIn("<Error>", res.json()["detail"])
                self.assertEqual(len(deleted_keys), 1)
                self.assertTrue(deleted_keys[0].startswith("fvs/") and deleted_keys[0].endswith("_odo.jpg"))

        # Verify 403 does not retry, while 503 retries
        r2 = R2Storage(
            account_id="acct",
            access_key_id="key",
            secret_access_key="secret",
            bucket_name="public-bucket",
            key_prefix="fvs",
            public_base_url="https://r2.pritabrata.com",
        )
        mock_client = MagicMock()
        mock_resp_403 = MagicMock(status_code=403, text="<Error>SignatureDoesNotMatch</Error>")
        mock_client.put.return_value = mock_resp_403
        with patch.object(r2, "_get_client", return_value=mock_client), patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                r2.upload_image(b"abc", "a.jpg", max_retries=3)
            self.assertEqual(mock_client.put.call_count, 1)

        mock_client.reset_mock()
        mock_resp_503 = MagicMock(status_code=503, text="SlowDown")
        mock_resp_200 = MagicMock(status_code=200, text="")
        mock_client.put.side_effect = [mock_resp_503, mock_resp_200]
        with patch.object(r2, "_get_client", return_value=mock_client), patch("time.sleep"):
            key = r2.upload_image(b"abc", "a.jpg", max_retries=3)
            self.assertEqual(key, "fvs/a.jpg")
            self.assertEqual(mock_client.put.call_count, 2)

    def test_oversized_upload_rejected_and_gemini_header_auth(self) -> None:
        """Oversized >15MB uploads return 413, and GeminiFlashExtractor uses x-goog-api-key header."""
        from unittest.mock import MagicMock
        from backend.extraction.gemini import GeminiFlashExtractor

        headers = {"X-FuelV-Key": self.test_write_key}
        oversized = b"x" * (15 * 1024 * 1024 + 1024)
        with patch.dict(os.environ, {"FUELV_WRITE_KEY": self.test_write_key, "FUELV_DISABLE_AUTH": "0"}):
            res = self.client.post(
                "/api/extract",
                headers=headers,
                files={"odo_image": ("huge.jpg", oversized, "image/jpeg")},
            )
            self.assertEqual(res.status_code, 413)

        # Gemini API key must be sent in x-goog-api-key header, never in URL query string
        captured_req = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({
                    "candidates": [{"content": {"parts": [{"text": '{"odometer_reading": 100.0, "total_expense": 50.0, "fuel_dispensed": 1.0}'}]}}]
                }).encode("utf-8")

        def fake_urlopen(req, timeout=30):
            captured_req["url"] = req.full_url
            captured_req["headers"] = {k.lower(): v for k, v in req.header_items()}
            return FakeResp()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ext = GeminiFlashExtractor(api_key="super-secret-gemini-key", model_name="gemini-3.5-flash-lite")
            out = ext.extract(b"img", "image/jpeg", None, None)
            self.assertEqual(out["odometer_reading"], 100.0)
            self.assertNotIn("super-secret-gemini-key", captured_req["url"])
            self.assertEqual(captured_req["headers"].get("x-goog-api-key"), "super-secret-gemini-key")

    def test_migration_refuses_path_traversal_in_db_path(self) -> None:
        """Migration script must refuse DB paths attempting directory traversal."""
        from backend.scripts.migrate_images_to_r2 import migrate_local_images_to_r2
        from backend.storage.r2 import R2Storage

        self.app.state.repo.insert_snapshot(
            captured_at="2026-11-12T10:00:00Z",
            odometer_reading=10000.0,
            total_expense=100.0,
            fuel_dispensed=1.0,
            odo_image_path="../../etc/passwd",
        )
        r2 = R2Storage(
            account_id="acct",
            access_key_id="key",
            secret_access_key="secret",
            bucket_name="public-bucket",
            key_prefix="fvs",
            public_base_url="https://r2.pritabrata.com",
        )
        summary = migrate_local_images_to_r2(
            db_path=self.db_path,
            images_dir=self.img_dir,
            storage=r2,
            delete_local=True,
            dry_run=False,
        )
        self.assertEqual(summary["migrated_files"], 0)
        self.assertTrue(any("traversal" in e.lower() for e in summary["errors"]))


class TestDataIntegrityAndEdgeCases(unittest.TestCase):
    """Rigorous Data Integrity, Decimal Precision, and Edge Case Tests."""

    def setUp(self) -> None:
        self.env_patcher = patch.dict(os.environ, {"FUELV_WRITE_KEY": ""})
        self.env_patcher.start()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_data_integrity.db"
        self.repo = SnapshotRepository(db_path=self.db_path)

        self.app = create_app(db_path=self.db_path)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.tmp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Extreme Outliers, Zero, and Negative Values
    # -------------------------------------------------------------------------
    def test_negative_values_rejected_by_api(self) -> None:
        """Pydantic validation must reject negative odometer, expense, or fuel."""
        bad_payloads = [
            {"odometer_reading": -100.0, "total_expense": 500.0, "fuel_dispensed": 5.0},
            {"odometer_reading": 1000.0, "total_expense": -50.0, "fuel_dispensed": 5.0},
            {"odometer_reading": 1000.0, "total_expense": 500.0, "fuel_dispensed": -0.1},
        ]
        for p in bad_payloads:
            res = self.client.post("/api/snapshots", json=p)
            self.assertEqual(res.status_code, 422, f"Payload {p} should be rejected with 422")

    def test_nan_and_infinity_rejected_by_snapshot_api_and_normalization(self) -> None:
        """Non-finite floats (inf, -inf, nan) must be rejected so SQLite/JSON never corrupts."""
        from backend.extraction.base import normalize_extracted_json

        for bad_val in ("inf", "-inf", "nan", "Infinity", "NaN"):
            res = self.client.post(
                "/api/snapshots",
                data={
                    "odometer_reading": bad_val,
                    "total_expense": "100.0",
                    "fuel_dispensed": "2.0",
                },
                files={"odo_image": ("empty.jpg", b"", "image/jpeg")},
            )
            self.assertEqual(res.status_code, 422, f"Expected 422 for odometer_reading={bad_val!r}")

        snap = self.client.post(
            "/api/snapshots",
            json={"odometer_reading": 100.0, "total_expense": 50.0, "fuel_dispensed": 1.0},
        ).json()
        res_put = self.client.put(
            f"/api/snapshots/{snap['id']}",
            content='{"odometer_reading": Infinity}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(res_put.status_code, 422)

        normalized = normalize_extracted_json(
            {
                "odometer_reading": float("inf"),
                "total_expense": float("nan"),
                "fuel_dispensed": 4.25,
            }
        )
        self.assertEqual(
            normalized,
            {"odometer_reading": None, "total_expense": None, "fuel_dispensed": 4.25},
        )

    def test_zero_fuel_and_zero_expense_handled_gracefully(self) -> None:
        """Zero fuel dispensed must not trigger division by zero in efficiency calculations."""
        s1 = self.repo.insert_snapshot(
            captured_at="2026-11-01T10:00:00+05:30",
            odometer_reading=10000.0,
            total_expense=0.0,
            fuel_dispensed=0.0,
        )
        self.assertNotIn("km_per_l", s1["extra_data"])

        # Second snapshot with 0 fuel dispensed
        s2 = self.repo.insert_snapshot(
            captured_at="2026-11-02T10:00:00+05:30",
            odometer_reading=10100.0,
            total_expense=0.0,
            fuel_dispensed=0.0,
        )
        self.assertEqual(s2["extra_data"]["distance_delta"], 100.0)
        self.assertNotIn("km_per_l", s2["extra_data"])  # Avoid division by zero

    def test_extreme_outliers(self) -> None:
        """Test astronomical odometer readings, tiny fuel amounts, and huge expenses."""
        s1 = self.repo.insert_snapshot(
            captured_at="2026-01-01T00:00:00Z",
            odometer_reading=999999999.0,
            total_expense=999999.99,
            fuel_dispensed=50.0,
        )
        s2 = self.repo.insert_snapshot(
            captured_at="2026-01-02T00:00:00Z",
            odometer_reading=999999999.1,
            total_expense=1.0,
            fuel_dispensed=0.001,
        )
        self.assertEqual(s2["extra_data"]["distance_delta"], 0.1)
        self.assertEqual(s2["extra_data"]["km_per_l"], 100.0)  # 0.1 / 0.001 = 100.0

    def test_odometer_rollback_handling(self) -> None:
        """When an odometer reading is less than the previous snapshot, delta and km/L must not be negative."""
        s1 = self.repo.insert_snapshot(
            captured_at="2026-11-01T10:00:00Z",
            odometer_reading=20000.0,
            total_expense=500.0,
            fuel_dispensed=10.0,
        )
        # S2 has lower odometer reading (e.g. meter replacement or user typo)
        s2 = self.repo.insert_snapshot(
            captured_at="2026-11-02T10:00:00Z",
            odometer_reading=19500.0,
            total_expense=500.0,
            fuel_dispensed=10.0,
        )
        self.assertEqual(s2["extra_data"]["previous_odometer"], 20000.0)
        self.assertNotIn("distance_delta", s2["extra_data"])
        self.assertNotIn("km_per_l", s2["extra_data"])

    # -------------------------------------------------------------------------
    # 2. Decimal Precision & Floating-Point Stability
    # -------------------------------------------------------------------------
    def test_floating_point_stability_in_grouping(self) -> None:
        """Verify grouping avoids IEEE 754 floating point drift (e.g. 0.1 + 0.2 = 0.30000000000000004)."""
        # Insert 10 snapshots with fractional expense and fuel
        for i in range(10):
            self.repo.insert_snapshot(
                captured_at=f"2026-11-1{i}T10:00:00Z",
                odometer_reading=1000.0 + (i * 10.1),
                total_expense=10.10,
                fuel_dispensed=1.111,
            )

        grouped = self.repo.get_grouped_snapshots("month")
        # 10 * 10.10 = 101.0
        self.assertEqual(grouped["overall_expense"], 101.0)
        self.assertEqual(grouped["groups"][0]["total_expense"], 101.0)
        # 10 * 1.111 = 11.11
        self.assertEqual(grouped["overall_fuel_dispensed"], 11.11)
        self.assertEqual(grouped["groups"][0]["total_fuel_dispensed"], 11.11)

    # -------------------------------------------------------------------------
    # 3. Empty Database & Single Snapshot Boundaries
    # -------------------------------------------------------------------------
    def test_empty_database_behavior(self) -> None:
        """Empty database returns clean empty structures without raising exceptions."""
        latest = self.repo.get_latest_snapshot()
        self.assertIsNone(latest)

        latest_res = self.client.get("/api/snapshots/latest")
        self.assertEqual(latest_res.status_code, 200)
        self.assertIsNone(latest_res.json()["latest"])

        for period in ["day", "week", "month", "year"]:
            grouped = self.repo.get_grouped_snapshots(period)  # type: ignore[arg-type]
            self.assertEqual(grouped["total_snapshots"], 0)
            self.assertEqual(grouped["overall_expense"], 0.0)
            self.assertEqual(grouped["overall_fuel_dispensed"], 0.0)
            self.assertEqual(grouped["overall_distance"], 0.0)
            self.assertEqual(grouped["groups"], [])

    def test_single_snapshot_boundary(self) -> None:
        """A single snapshot cannot compute distance or efficiency deltas."""
        snap = self.repo.insert_snapshot(
            captured_at="2026-05-15T12:00:00Z",
            odometer_reading=4500.0,
            total_expense=300.0,
            fuel_dispensed=3.5,
        )
        self.assertNotIn("previous_odometer", snap["extra_data"])
        self.assertNotIn("distance_delta", snap["extra_data"])
        self.assertNotIn("km_per_l", snap["extra_data"])

        grouped = self.repo.get_grouped_snapshots("month")
        self.assertEqual(grouped["total_snapshots"], 1)
        self.assertEqual(grouped["overall_distance"], 0.0)
        grp = grouped["groups"][0]
        self.assertEqual(grp["min_odometer"], 4500.0)
        self.assertEqual(grp["max_odometer"], 4500.0)

    # -------------------------------------------------------------------------
    # 4. Multi-Year and Multi-Week Boundary Grouping
    # -------------------------------------------------------------------------
    def test_year_and_week_boundary_transitions(self) -> None:
        """Test transitions across Week 52/53 and Year 2025/2026 boundary."""
        # 2025-12-28 (Sun, Week 51)
        self.repo.insert_snapshot(
            captured_at="2025-12-28T10:00:00Z",
            odometer_reading=5000.0,
            total_expense=100.0,
            fuel_dispensed=2.0,
        )
        # 2025-12-31 (Wed, Week 52)
        self.repo.insert_snapshot(
            captured_at="2025-12-31T18:00:00Z",
            odometer_reading=5100.0,
            total_expense=100.0,
            fuel_dispensed=2.0,
        )
        # 2026-01-01 (Thu, Week 00 of 2026)
        self.repo.insert_snapshot(
            captured_at="2026-01-01T08:00:00Z",
            odometer_reading=5200.0,
            total_expense=100.0,
            fuel_dispensed=2.0,
        )
        # 2026-01-05 (Mon, Week 01 of 2026)
        self.repo.insert_snapshot(
            captured_at="2026-01-05T09:00:00Z",
            odometer_reading=5350.0,
            total_expense=150.0,
            fuel_dispensed=3.0,
        )

        # Year grouping
        by_year = self.repo.get_grouped_snapshots("year")
        self.assertEqual(len(by_year["groups"]), 2)
        self.assertEqual(by_year["groups"][0]["period"], "2026")
        self.assertEqual(by_year["groups"][0]["snapshot_count"], 2)
        self.assertEqual(by_year["groups"][1]["period"], "2025")
        self.assertEqual(by_year["groups"][1]["snapshot_count"], 2)

        # Week grouping
        by_week = self.repo.get_grouped_snapshots("week")
        week_periods = [g["period"] for g in by_week["groups"]]
        self.assertEqual(week_periods, ["2026-W01", "2026-W00", "2025-W52", "2025-W51"])

        # Overall distance across the 4 logs
        self.assertEqual(by_year["overall_distance"], 350.0)

    # -------------------------------------------------------------------------
    # 5. Out-of-Order Insertions and Chronological Chain Recalculation
    # -------------------------------------------------------------------------
    def test_chronological_chain_recalculation_on_out_of_order_insertion(self) -> None:
        """When logs are inserted out of chronological order, all deltas must recalculate correctly."""
        # 1. Insert S1: Nov 1 (odo 1000)
        s1 = self.repo.insert_snapshot(
            captured_at="2026-11-01T10:00:00Z",
            odometer_reading=1000.0,
            total_expense=500.0,
            fuel_dispensed=10.0,
        )
        # 2. Insert S3: Nov 10 (odo 2000, delta was initially 1000.0)
        s3 = self.repo.insert_snapshot(
            captured_at="2026-11-10T10:00:00Z",
            odometer_reading=2000.0,
            total_expense=500.0,
            fuel_dispensed=10.0,
        )
        self.assertEqual(s3["extra_data"]["previous_odometer"], 1000.0)
        self.assertEqual(s3["extra_data"]["distance_delta"], 1000.0)
        self.assertEqual(s3["extra_data"]["km_per_l"], 100.0)

        # 3. Insert S2 out-of-order: Nov 5 (odo 1600, fuel 10.0)
        s2 = self.repo.insert_snapshot(
            captured_at="2026-11-05T10:00:00Z",
            odometer_reading=1600.0,
            total_expense=500.0,
            fuel_dispensed=10.0,
        )
        # S2 should have prev=1000, delta=600, kmpl=60.0
        self.assertEqual(s2["extra_data"]["previous_odometer"], 1000.0)
        self.assertEqual(s2["extra_data"]["distance_delta"], 600.0)
        self.assertEqual(s2["extra_data"]["km_per_l"], 60.0)

        # S3 MUST HAVE BEEN AUTOMATICALLY RECALCULATED:
        # prev should now be 1600, delta 400.0 (2000 - 1600), kmpl 40.0 (400 / 10.0)
        s3_refreshed = self.repo.get_snapshot(s3["id"])
        self.assertIsNotNone(s3_refreshed)
        self.assertEqual(s3_refreshed["extra_data"]["previous_odometer"], 1600.0)
        self.assertEqual(s3_refreshed["extra_data"]["distance_delta"], 400.0)
        self.assertEqual(s3_refreshed["extra_data"]["km_per_l"], 40.0)

    # -------------------------------------------------------------------------
    # 6. Snapshot Updates and Deletions
    # -------------------------------------------------------------------------
    def test_snapshot_updates_and_deletions_recalculate_subsequent_deltas(self) -> None:
        """Updating or deleting a snapshot must propagate efficiency updates to subsequent snapshots."""
        s1 = self.repo.insert_snapshot(
            captured_at="2026-11-01T10:00:00Z",
            odometer_reading=1000.0,
            total_expense=100.0,
            fuel_dispensed=5.0,
        )
        s2 = self.repo.insert_snapshot(
            captured_at="2026-11-05T10:00:00Z",
            odometer_reading=1250.0,
            total_expense=100.0,
            fuel_dispensed=5.0,
        )
        s3 = self.repo.insert_snapshot(
            captured_at="2026-11-10T10:00:00Z",
            odometer_reading=1500.0,
            total_expense=100.0,
            fuel_dispensed=5.0,
        )

        # Before update: s2 delta = 250 (50 km/L), s3 delta = 250 (50 km/L)
        self.assertEqual(s3["extra_data"]["distance_delta"], 250.0)

        # Update s2 odometer to 1300.0
        updated_s2 = self.repo.update_snapshot(s2["id"], odometer_reading=1300.0)
        self.assertEqual(updated_s2["extra_data"]["distance_delta"], 300.0)
        self.assertEqual(updated_s2["extra_data"]["km_per_l"], 60.0)

        # s3 must reflect new s2 odo: delta = 1500 - 1300 = 200.0, km/L = 200 / 5 = 40.0
        s3_after_s2_update = self.repo.get_snapshot(s3["id"])
        self.assertEqual(s3_after_s2_update["extra_data"]["previous_odometer"], 1300.0)
        self.assertEqual(s3_after_s2_update["extra_data"]["distance_delta"], 200.0)
        self.assertEqual(s3_after_s2_update["extra_data"]["km_per_l"], 40.0)

        # Update s2 fuel dispensed to 0.0 -> s2 km/L cleared, delta preserved
        s2_zero_fuel = self.repo.update_snapshot(s2["id"], fuel_dispensed=0.0)
        self.assertEqual(s2_zero_fuel["extra_data"]["distance_delta"], 300.0)
        self.assertNotIn("km_per_l", s2_zero_fuel["extra_data"])

        # Delete intermediate snapshot s2 -> s3 now connects directly to s1!
        # Delta becomes 1500 - 1000 = 500.0, km/L = 500 / 5 = 100.0
        deleted = self.repo.delete_snapshot(s2["id"])
        self.assertTrue(deleted)

        s3_after_delete = self.repo.get_snapshot(s3["id"])
        self.assertEqual(s3_after_delete["extra_data"]["previous_odometer"], 1000.0)
        self.assertEqual(s3_after_delete["extra_data"]["distance_delta"], 500.0)
        self.assertEqual(s3_after_delete["extra_data"]["km_per_l"], 100.0)


if __name__ == "__main__":
    unittest.main()
