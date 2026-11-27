from __future__ import annotations

import hmac
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.main import MAX_IMAGE_BYTES, create_app, get_client_ip
from backend.storage.r2 import R2Storage


class TestRegressionSecurityAuth(unittest.TestCase):
    """Regression tests for UUID write protection, constant-time digest checks,
    rate limiting (slowapi), proxy IP extraction, and upload extension/size guards.
    """

    def setUp(self) -> None:
        self.write_key = str(uuid.uuid4())
        self.env_patcher = patch.dict(
            os.environ,
            {
                "R2_ACCOUNT_ID": "test_acct",
                "R2_ACCESS_KEY_ID": "test_ak",
                "R2_SECRET_ACCESS_KEY": "test_sk",
                "R2_BUCKET_NAME": "public-bucket",
                "R2_PUBLIC_BASE_URL": "https://r2.example.com",
                "FUELV_WRITE_KEY": self.write_key,
                "FUELV_DISABLE_AUTH": "0",
                "RATE_LIMIT_ENABLED": "false",
            },
            clear=False,
        )
        self.env_patcher.start()

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_sec.db"
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

    def test_timing_safe_uuid_check_and_auth_bypass_flag(self) -> None:
        """Mutating endpoints enforce timing-safe UUID checks unless FUELV_DISABLE_AUTH=1."""
        with patch.object(hmac, "compare_digest", wraps=hmac.compare_digest) as spy_cmp:
            denied = self.client.post(
                "/api/snapshots",
                headers={"X-FuelV-Key": str(uuid.uuid4())},
                json={"odometer_reading": 100.0, "total_expense": 10.0, "fuel_dispensed": 1.0},
            )
            self.assertEqual(denied.status_code, 401)
            self.assertTrue(spy_cmp.called)

        # Case-insensitive & braced UUID accepted
        ok = self.client.post(
            "/api/snapshots",
            headers={"X-FuelV-Key": f" {{{self.write_key.upper()}}} "},
            json={"odometer_reading": 100.0, "total_expense": 10.0, "fuel_dispensed": 1.0},
        )
        self.assertEqual(ok.status_code, 201)

        # FUELV_DISABLE_AUTH=1 bypasses write key requirement even when FUELV_WRITE_KEY is set
        with patch.dict(os.environ, {"FUELV_DISABLE_AUTH": "1"}, clear=False):
            bypass_res = self.client.post(
                "/api/snapshots",
                json={"odometer_reading": 150.0, "total_expense": 20.0, "fuel_dispensed": 2.0},
            )
            self.assertEqual(bypass_res.status_code, 201)

    def test_unauthenticated_read_endpoints_always_accessible(self) -> None:
        """Read-only endpoints remain open without X-FuelV-Key even when write protection is active."""
        for endpoint in ("/api/health", "/api/snapshots/latest", "/api/snapshots/grouped"):
            res = self.client.get(endpoint)
            self.assertEqual(res.status_code, 200)

        self.assertTrue(self.client.get("/api/health").json()["write_protection_enabled"])

    def test_client_ip_extraction_priority_for_rate_limiting(self) -> None:
        """get_client_ip prioritizes CF-Connecting-IP, then first X-Forwarded-For, then client.host."""
        req = MagicMock()
        req.headers = {
            "CF-Connecting-IP": " 203.0.113.10 ",
            "X-Forwarded-For": "198.51.100.1, 10.0.0.1",
        }
        req.client = MagicMock(host="127.0.0.1")
        self.assertEqual(get_client_ip(req), "203.0.113.10")

        req.headers = {"X-Forwarded-For": "198.51.100.2, 10.0.0.1"}
        self.assertEqual(get_client_ip(req), "198.51.100.2")

        req.headers = {}
        self.assertEqual(get_client_ip(req), "127.0.0.1")

        req.client = None
        self.assertEqual(get_client_ip(req), "127.0.0.1")

    def test_slowapi_rate_limiting_and_health_exemption(self) -> None:
        """SlowAPI enforces per-IP limits on /api/extract & /api/snapshots/latest, marks /api/health exempt, and honors RATE_LIMIT_ENABLED=false."""
        with patch.dict(os.environ, {"RATE_LIMIT_ENABLED": "true"}, clear=False):
            rl_app = create_app(db_path=self.db_path, images_dir=self.img_dir, validate_on_startup=False)
            rl_client = TestClient(rl_app)

            # /api/health is registered in limiter._exempt_routes
            self.assertTrue(
                any("health_check" in route_name for route_name in rl_app.state.limiter._exempt_routes)
            )

            # /api/snapshots/latest is limited to 60/minute per IP
            latest_statuses = [
                rl_client.get("/api/snapshots/latest", headers={"CF-Connecting-IP": "198.51.100.50"}).status_code
                for _ in range(61)
            ]
            self.assertEqual(latest_statuses[:60], [200] * 60)
            self.assertEqual(latest_statuses[60], 429)

            # /api/extract is limited to 15/minute per client IP
            fake_ext = MagicMock()
            fake_ext.model_id = "gemini-3.5-flash-lite"
            fake_ext.extract.return_value = {
                "odometer_reading": 100.0,
                "total_expense": 10.0,
                "fuel_dispensed": 1.0,
            }
            headers_ip1 = {"X-FuelV-Key": self.write_key, "CF-Connecting-IP": "203.0.113.1"}
            with patch("backend.main.get_extractor", return_value=fake_ext):
                statuses = [
                    rl_client.post(
                        "/api/extract",
                        headers=headers_ip1,
                        files={"odo_image": ("odo.jpg", b"abc", "image/jpeg")},
                    ).status_code
                    for _ in range(16)
                ]
                self.assertEqual(statuses[:15], [200] * 15)
                self.assertEqual(statuses[15], 429)

                # Separate IP retains full quota
                ip2_res = rl_client.post(
                    "/api/extract",
                    headers={"X-FuelV-Key": self.write_key, "CF-Connecting-IP": "203.0.113.2"},
                    files={"odo_image": ("odo.jpg", b"abc", "image/jpeg")},
                )
                self.assertEqual(ip2_res.status_code, 200)

        # RATE_LIMIT_ENABLED=false disables rate limits completely
        for _ in range(65):
            res = self.client.get("/api/snapshots/latest")
            self.assertEqual(res.status_code, 200)

    def test_upload_extension_sanitization_and_max_image_size_guard(self) -> None:
        """Allowed image extensions stay intact; disallowed extensions coerce to .jpg; >15MB payloads return 413."""
        headers = {"X-FuelV-Key": self.write_key}

        with patch.object(
            self.r2,
            "upload_image",
            side_effect=lambda data, filename, content_type="image/jpeg", max_retries=3: self.r2.build_object_key(
                filename
            ),
        ):
            # Allowed extensions (.PNG, .webp) preserved in lower-case
            ok_res = self.client.post(
                "/api/snapshots",
                headers=headers,
                data={"odometer_reading": "500", "total_expense": "50", "fuel_dispensed": "1.0"},
                files={
                    "odo_image": ("photo.PNG", b"png-bytes", "image/png"),
                    "pump_image": ("pump.webp", b"webp-bytes", "image/webp"),
                },
            )
            self.assertEqual(ok_res.status_code, 201)
            self.assertTrue(ok_res.json()["odo_image_path"].endswith("_odo.png"))
            self.assertTrue(ok_res.json()["pump_image_path"].endswith("_pump.webp"))

            # Dangerous extensions (.svg, .html, .php) coerced to .jpg
            for bad_name in ("payload.svg", "xss.html", "../../shell.php", "no_ext"):
                sanitized_res = self.client.post(
                    "/api/snapshots",
                    headers=headers,
                    data={"odometer_reading": "600", "total_expense": "60", "fuel_dispensed": "1.0"},
                    files={"odo_image": (bad_name, b"bytes", "application/octet-stream")},
                )
                self.assertEqual(sanitized_res.status_code, 201)
                self.assertTrue(sanitized_res.json()["odo_image_path"].endswith("_odo.jpg"))

        # Oversized (> 15 MB) rejected with 413 on /api/extract
        oversized = b"0" * (MAX_IMAGE_BYTES + 1)
        extract_413 = self.client.post(
            "/api/extract",
            headers=headers,
            files={"odo_image": ("huge.jpg", oversized, "image/jpeg")},
        )
        self.assertEqual(extract_413.status_code, 413)

        # Oversized pump_image on /api/snapshots returns 413 AND deletes already-uploaded odo_image from R2
        deleted_keys: list[str | None] = []
        with patch.object(
            self.r2,
            "upload_image",
            side_effect=lambda data, filename, content_type="image/jpeg", max_retries=3: self.r2.build_object_key(
                filename
            ),
        ), patch.object(
            self.r2,
            "delete_image",
            side_effect=lambda key: deleted_keys.append(key) or True,
        ):
            snap_413 = self.client.post(
                "/api/snapshots",
                headers=headers,
                data={"odometer_reading": "700", "total_expense": "70", "fuel_dispensed": "1.0"},
                files={
                    "odo_image": ("valid.jpg", b"small-img", "image/jpeg"),
                    "pump_image": ("huge.jpg", oversized, "image/jpeg"),
                },
            )
        self.assertEqual(snap_413.status_code, 413)
        self.assertEqual(len(deleted_keys), 1)
        self.assertTrue((deleted_keys[0] or "").endswith("_odo.jpg"))


if __name__ == "__main__":
    unittest.main()
