from __future__ import annotations

import hashlib
import hmac
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx

from backend.storage.r2 import (
    R2Storage,
    _extract_safe_filename,
    _get_signature_key,
    _is_safe_r2_key,
    resolve_image_url,
)


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)


class TestRegressionR2Storage(unittest.TestCase):
    """Regression tests for Cloudflare R2 SigV4 signing, canonical formatting,
    key prefix / traversal guards, URL resolution, and transient failure recovery.
    """

    def setUp(self) -> None:
        self.r2 = R2Storage(
            account_id="acct123456",
            access_key_id="AKIA_TEST_KEY",
            secret_access_key="SECRET_TEST_KEY",
            bucket_name="public-bucket",
            key_prefix="fvs",
            public_base_url="https://r2.example.com/",
        )

    def tearDown(self) -> None:
        self.r2.close()

    def test_sigv4_signature_derivation_and_canonical_request_deterministic(self) -> None:
        """_build_signed_headers produces an exact AWS SigV4 signature matching reference HMAC-SHA256."""
        payload = b"hello-fuelv-jpeg"
        with patch("backend.storage.r2.datetime", _FixedDateTime):
            url, headers = self.r2._build_signed_headers(
                method="PUT",
                object_key="fvs/my photo.jpg",
                payload=payload,
                extra_headers={
                    "Content-Type": "image/jpeg\r\nX-Injected: bad",
                    "Host": "evil.example.com",
                    "Authorization": "spoof",
                },
            )

        self.assertEqual(
            url,
            "https://acct123456.r2.cloudflarestorage.com/public-bucket/fvs/my%20photo.jpg",
        )
        # Reserved headers cannot be overridden; CRLF collapsed to single spaces
        self.assertEqual(headers["Content-Type"], "image/jpeg X-Injected: bad")
        self.assertEqual(headers["x-amz-date"], "20261127T120000Z")
        expected_payload_hash = hashlib.sha256(payload).hexdigest()
        self.assertEqual(headers["x-amz-content-sha256"], expected_payload_hash)

        # Independent reference SigV4 signature computation
        canonical_headers = (
            f"content-type:image/jpeg X-Injected: bad\n"
            f"host:acct123456.r2.cloudflarestorage.com\n"
            f"x-amz-content-sha256:{expected_payload_hash}\n"
            f"x-amz-date:20261127T120000Z\n"
        )
        signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            [
                "PUT",
                "/public-bucket/fvs/my%20photo.jpg",
                "",
                canonical_headers,
                signed_headers,
                expected_payload_hash,
            ]
        )
        scope = "20261127/auto/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                "20261127T120000Z",
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = _get_signature_key("SECRET_TEST_KEY", "20261127", "auto", "s3")
        expected_sig = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        self.assertEqual(
            headers["Authorization"],
            f"AWS4-HMAC-SHA256 Credential=AKIA_TEST_KEY/{scope}, SignedHeaders={signed_headers}, Signature={expected_sig}",
        )

    def test_key_prefix_validation_and_filename_path_traversal_guards(self) -> None:
        """Path traversal, backslashes, null bytes, and outside-prefix keys are sanitized or blocked."""
        self.assertEqual(_extract_safe_filename("../../etc/passwd.jpg"), "passwd.jpg")
        self.assertEqual(_extract_safe_filename("..\\..\\windows\\cmd.png"), "cmd.png")
        self.assertEqual(_extract_safe_filename("my photo (1).jpg"), "my_photo_1_.jpg")

        for bad_name in ("", ".", "..", "///", "..."):
            with self.assertRaises(ValueError):
                _extract_safe_filename(bad_name)

        self.assertTrue(_is_safe_r2_key("fvs/2026_odo.jpg", "fvs"))
        self.assertTrue(_is_safe_r2_key("/fvs/nested/photo.png", "fvs"))

        unsafe_keys = [
            "",
            "fvs/",
            "fvs",
            "other/photo.jpg",
            "fvs/..",
            "fvs/../secret.jpg",
            "fvs\\photo.jpg",
            "fvs/photo.jpg\x00.png",
        ]
        for key in unsafe_keys:
            self.assertFalse(_is_safe_r2_key(key, "fvs"), f"Key should be rejected: {key!r}")
            self.assertFalse(self.r2.head_image(key))
            self.assertFalse(self.r2.delete_image(key))

    def test_resolve_image_url_handles_r2_keys_absolute_urls_legacy_paths_and_traversal(self) -> None:
        """resolve_image_url resolves R2 keys to CDN URLs, preserves http(s), handles legacy paths, and rejects traversal."""
        with patch.dict(
            os.environ,
            {"R2_KEY_PREFIX": "fvs", "R2_PUBLIC_BASE_URL": "https://cdn.example.com/"},
            clear=False,
        ):
            self.assertIsNone(resolve_image_url(None))
            self.assertIsNone(resolve_image_url(""))
            self.assertIsNone(resolve_image_url("fvs\\evil.jpg"))
            self.assertIsNone(resolve_image_url("fvs/evil.jpg\x00"))
            self.assertIsNone(resolve_image_url(".."))
            self.assertIsNone(resolve_image_url("."))

            self.assertEqual(
                resolve_image_url("https://other.cdn/img.jpg"),
                "https://other.cdn/img.jpg",
            )
            self.assertEqual(
                resolve_image_url("fvs/20261127_odo.jpg"),
                "https://cdn.example.com/fvs/20261127_odo.jpg",
            )
            self.assertEqual(
                resolve_image_url("/fvs/20261127_pump.jpg"),
                "https://cdn.example.com/fvs/20261127_pump.jpg",
            )
            # Traversal containing prefix must NOT resolve to public CDN prefix URL
            resolved_traversal = resolve_image_url("fvs/../secret.jpg")
            self.assertFalse((resolved_traversal or "").startswith("https://cdn.example.com/"))

            # Legacy local path falls back to /api/images/<leaf>
            self.assertEqual(resolve_image_url("images/legacy_odo.jpg"), "/api/images/legacy_odo.jpg")

        with patch.dict(os.environ, {"R2_KEY_PREFIX": "fvs", "R2_PUBLIC_BASE_URL": ""}, clear=False):
            self.assertEqual(resolve_image_url("fvs/sample.jpg"), "/fvs/sample.jpg")

    def test_upload_image_retries_transient_errors_and_network_drops(self) -> None:
        """upload_image retries transient network errors and 5xx/429 responses, failing fast on 403."""
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.is_closed = False

        ok_resp = MagicMock(status_code=200, text="OK")
        transient_resp = MagicMock(status_code=503, text="Slow Down")
        mock_client.put.side_effect = [
            httpx.ConnectError("connection dropped"),
            transient_resp,
            ok_resp,
        ]

        with patch("backend.storage.r2.logger"), patch.object(
            self.r2, "_get_client", return_value=mock_client
        ), patch(
            "backend.storage.r2.time.sleep"
        ) as mock_sleep:
            key = self.r2.upload_image(b"jpeg-data", "trip_odo.jpg", max_retries=3)

        self.assertEqual(key, "fvs/trip_odo.jpg")
        self.assertEqual(mock_client.put.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

        # Non-transient 403 fails immediately without retries
        mock_client.reset_mock()
        mock_client.put.side_effect = [MagicMock(status_code=403, text="Forbidden")]
        with patch("backend.storage.r2.logger"), patch.object(
            self.r2, "_get_client", return_value=mock_client
        ), patch(
            "backend.storage.r2.time.sleep"
        ) as mock_sleep:
            with self.assertRaises(RuntimeError) as ctx:
                self.r2.upload_image(b"jpeg-data", "forbidden.jpg", max_retries=3)
        self.assertIn("403", str(ctx.exception))
        self.assertEqual(mock_client.put.call_count, 1)
        mock_sleep.assert_not_called()

        # Exhausted retries raise RuntimeError
        mock_client.reset_mock()
        mock_client.put.side_effect = httpx.ReadTimeout("timed out")
        with patch("backend.storage.r2.logger"), patch.object(
            self.r2, "_get_client", return_value=mock_client
        ), patch("backend.storage.r2.time.sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                self.r2.upload_image(b"jpeg-data", "timeout.jpg", max_retries=2)
        self.assertIn("Failed to upload fvs/timeout.jpg", str(ctx.exception))
        self.assertEqual(mock_client.put.call_count, 2)

    def test_unconfigured_r2_and_graceful_delete_head_error_handling(self) -> None:
        """Unconfigured R2 obeys R2_REQUIRE_CREDENTIALS; delete_image and head_image handle errors gracefully."""
        unconfigured = R2Storage(
            account_id="",
            access_key_id="",
            secret_access_key="",
            bucket_name="",
            public_base_url="",
        )
        self.assertFalse(unconfigured.is_configured)
        with patch.dict(os.environ, {"R2_REQUIRE_CREDENTIALS": "false"}, clear=False):
            self.assertEqual(unconfigured.upload_image(b"x", "virtual.jpg"), "fvs/virtual.jpg")
        with patch.dict(os.environ, {"R2_REQUIRE_CREDENTIALS": "true"}, clear=False):
            with self.assertRaises(RuntimeError):
                unconfigured.upload_image(b"x", "virtual.jpg")

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.is_closed = False
        mock_client.delete.side_effect = [
            MagicMock(status_code=204),
            MagicMock(status_code=404),
            MagicMock(status_code=500),
            httpx.ConnectError("network down"),
        ]
        mock_client.head.side_effect = [
            MagicMock(status_code=200),
            MagicMock(status_code=404),
            httpx.ReadTimeout("timeout"),
        ]

        with patch.object(self.r2, "_get_client", return_value=mock_client):
            self.assertTrue(self.r2.delete_image("fvs/a.jpg"))
            self.assertTrue(self.r2.delete_image("fvs/missing.jpg"))
            self.assertFalse(self.r2.delete_image("fvs/server_err.jpg"))
            self.assertFalse(self.r2.delete_image("fvs/net_drop.jpg"))

            self.assertTrue(self.r2.head_image("fvs/exists.jpg"))
            self.assertFalse(self.r2.head_image("fvs/not_found.jpg"))
            self.assertFalse(self.r2.head_image("fvs/timeout.jpg"))


if __name__ == "__main__":
    unittest.main()
