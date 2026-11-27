from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.extraction import (
    CustomModelExtractor,
    GeminiFlashExtractor,
    get_extractor,
    normalize_extracted_json,
)
from backend.main import create_app


class _FakeUrlopenResponse:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _FakeUrlopenResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class TestFeatureVlmExtractionContracts(unittest.TestCase):
    """Feature tests for VLM extraction contract normalization, swappable extractors,
    error fallback, efficiency preview, and /api/extract metadata warnings.
    """

    def setUp(self) -> None:
        self.env_patcher = patch.dict(
            os.environ,
            {
                "FUELV_WRITE_KEY": "",
                "FUELV_DISABLE_AUTH": "1",
                "RATE_LIMIT_ENABLED": "false",
                "EXTRACTOR_BACKEND": "gemini",
                "GEMINI_API_KEY": "fake-gemini-key",
            },
            clear=False,
        )
        self.env_patcher.start()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_extract.db"
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

    def test_normalize_handles_none_non_dicts_and_missing_keys(self) -> None:
        """Non-dict payloads and missing keys yield the exact 3-key contract with None values."""
        expected_empty = {
            "odometer_reading": None,
            "total_expense": None,
            "fuel_dispensed": None,
        }
        self.assertEqual(normalize_extracted_json(None), expected_empty)
        self.assertEqual(normalize_extracted_json([]), expected_empty)  # type: ignore[arg-type]
        self.assertEqual(normalize_extracted_json("invalid"), expected_empty)  # type: ignore[arg-type]
        self.assertEqual(normalize_extracted_json({}), expected_empty)
        self.assertEqual(
            normalize_extracted_json({"odometer_reading": 1200}),
            {
                "odometer_reading": 1200.0,
                "total_expense": None,
                "fuel_dispensed": None,
            },
        )

    def test_normalize_converts_strings_rounds_precision_and_discards_extra_keys(self) -> None:
        """Numeric strings normalize to 3-decimal floats, zeros stay 0.0, and uncontracted keys are stripped."""
        raw = {
            "odometer_reading": " 14520.5678 ",
            "total_expense": "0",
            "fuel_dispensed": 5.3814,
            "confidence": 0.99,
            "unit_price": 102.5,
            "vehicle": "My Bike",
            "extra_nested": {"raw": "text"},
        }
        normalized = normalize_extracted_json(raw)
        self.assertEqual(
            normalized,
            {
                "odometer_reading": 14520.568,
                "total_expense": 0.0,
                "fuel_dispensed": 5.381,
            },
        )
        self.assertEqual(
            set(normalized.keys()),
            {"odometer_reading", "total_expense", "fuel_dispensed"},
        )

    def test_normalize_rejects_malformed_negative_nan_and_inf_values(self) -> None:
        """Negative values, NaN, +/-Inf, empty strings, and non-numeric strings become None."""
        raw = {
            "odometer_reading": float("nan"),
            "total_expense": float("inf"),
            "fuel_dispensed": -4.2,
        }
        self.assertEqual(
            normalize_extracted_json(raw),
            {"odometer_reading": None, "total_expense": None, "fuel_dispensed": None},
        )

        malformed = {
            "odometer_reading": "inf",
            "total_expense": "-100.5",
            "fuel_dispensed": "₹500.00",
        }
        self.assertEqual(
            normalize_extracted_json(malformed),
            {"odometer_reading": None, "total_expense": None, "fuel_dispensed": None},
        )

        empty_vals = {
            "odometer_reading": "",
            "total_expense": "   ",
            "fuel_dispensed": ["5.0"],
        }
        self.assertEqual(
            normalize_extracted_json(empty_vals),
            {"odometer_reading": None, "total_expense": None, "fuel_dispensed": None},
        )

    def test_normalize_extreme_valid_values(self) -> None:
        """Extreme large and tiny positive finite numbers are rounded cleanly without overflow."""
        normalized = normalize_extracted_json(
            {
                "odometer_reading": 99999999.9999,
                "total_expense": 0.0004,
                "fuel_dispensed": 0.0006,
            }
        )
        self.assertEqual(normalized["odometer_reading"], 100000000.0)
        self.assertEqual(normalized["total_expense"], 0.0)
        self.assertEqual(normalized["fuel_dispensed"], 0.001)

    def test_extractor_factory_and_gemini_header_auth(self) -> None:
        """get_extractor switches backends via env, and Gemini sends API key in header (not URL)."""
        with patch.dict(os.environ, {"EXTRACTOR_BACKEND": "  CUSTOM "}, clear=False):
            ext = get_extractor()
            self.assertIsInstance(ext, CustomModelExtractor)

        gemini = GeminiFlashExtractor(api_key="secret-gem-key", model_name="gemini-3.5-flash-lite")
        # Empty images short-circuit without network call
        self.assertEqual(
            gemini.extract(None, None, None, None),
            {"odometer_reading": None, "total_expense": None, "fuel_dispensed": None},
        )

        captured_reqs: list = []

        def fake_urlopen(req, timeout=30):
            captured_reqs.append(req)
            return _FakeUrlopenResponse(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            {
                                                "odometer_reading": "15200.4",
                                                "total_expense": 550,
                                                "fuel_dispensed": 5.25,
                                                "ignored": "meta",
                                            }
                                        )
                                    }
                                ]
                            }
                        }
                    ]
                }
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = gemini.extract(b"odo-img", "image/jpeg", b"pump-img", "image/png")

        self.assertEqual(
            res,
            {"odometer_reading": 15200.4, "total_expense": 550.0, "fuel_dispensed": 5.25},
        )
        req = captured_reqs[0]
        self.assertNotIn("key=", req.full_url)
        self.assertEqual(req.get_header("X-goog-api-key"), "secret-gem-key")

    def test_gemini_and_custom_extractors_raise_runtime_error_on_malformed_or_http_errors(self) -> None:
        """Extractors raise clear RuntimeError on missing key, HTTP failure, or malformed JSON."""
        no_key_gemini = GeminiFlashExtractor(api_key="")
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
            no_key_gemini = GeminiFlashExtractor()
            with self.assertRaises(RuntimeError):
                no_key_gemini.extract(b"bytes", "image/jpeg", None, None)

        gemini = GeminiFlashExtractor(api_key="key")
        with patch(
            "urllib.request.urlopen",
            return_value=_FakeUrlopenResponse({"candidates": [{"content": {"parts": [{"text": "{bad-json"}]}}]}),
        ):
            with self.assertRaises(RuntimeError):
                gemini.extract(b"bytes", "image/jpeg", None, None)

        custom = CustomModelExtractor(endpoint_url="http://localhost:9999/predict", api_key="tok")
        with patch(
            "urllib.request.urlopen",
            return_value=_FakeUrlopenResponse(
                {"extraction": {"odometer_reading": "2200", "total_expense": 100, "fuel_dispensed": "1.25"}}
            ),
        ):
            out = custom.extract(b"odo", "image/jpeg", None, None)
        self.assertEqual(out, {"odometer_reading": 2200.0, "total_expense": 100.0, "fuel_dispensed": 1.25})

        http_err = urllib.error.HTTPError(
            url="http://localhost:9999/predict",
            code=503,
            msg="Service Unavailable",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b"upstream busy"),
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            with self.assertRaises(RuntimeError) as ctx:
                custom.extract(b"odo", "image/jpeg", None, None)
        self.assertIn("503", str(ctx.exception))

    def test_extract_endpoint_returns_efficiency_preview_and_meta_warning_on_failure(self) -> None:
        """POST /api/extract computes stateless efficiency preview and returns meta.warning on extractor error."""
        # Requires at least one image
        no_img_res = self.client.post("/api/extract")
        self.assertEqual(no_img_res.status_code, 400)

        # Insert baseline snapshot so /api/extract can compute delta preview
        self.app.state.repo.insert_snapshot(
            captured_at="2026-11-01T10:00:00Z",
            odometer_reading=10000.0,
            total_expense=500.0,
            fuel_dispensed=5.0,
        )

        fake_extractor = MagicMock()
        fake_extractor.model_id = "gemini-3.5-flash-lite"
        fake_extractor.extract.return_value = {
            "odometer_reading": 10250.0,
            "total_expense": 520.0,
            "fuel_dispensed": 5.0,
        }

        with patch("backend.main.get_extractor", return_value=fake_extractor):
            ok_res = self.client.post(
                "/api/extract",
                files={
                    "odo_image": ("odo.jpg", b"fake-odo", "image/jpeg"),
                    "pump_image": ("pump.jpg", b"fake-pump", "image/jpeg"),
                },
            )

        self.assertEqual(ok_res.status_code, 200)
        ok_body = ok_res.json()
        self.assertEqual(ok_body["extraction"]["odometer_reading"], 10250.0)
        self.assertEqual(
            ok_body["efficiency"],
            {"previous_odometer": 10000.0, "distance_delta": 250.0, "km_per_l": 50.0},
        )
        self.assertIsNone(ok_body["meta"]["warning"])
        self.assertIsNone(ok_body["meta"]["odo_image_path"])
        self.assertIsNone(ok_body["meta"]["pump_image_path"])
        self.assertEqual(list(self.img_dir.iterdir()), [])

        # Graceful fallback when extractor fails: HTTP 200, null numbers, populated meta.warning
        failing_extractor = MagicMock()
        failing_extractor.model_id = "gemini-3.5-flash-lite"
        failing_extractor.extract.side_effect = RuntimeError("Gemini quota exceeded")

        with patch("backend.main.logger"), patch("backend.main.get_extractor", return_value=failing_extractor):
            warn_res = self.client.post(
                "/api/extract",
                files={"odo_image": ("odo.jpg", b"fake-odo", "image/jpeg")},
            )

        self.assertEqual(warn_res.status_code, 200)
        warn_body = warn_res.json()
        self.assertEqual(
            warn_body["extraction"],
            {"odometer_reading": None, "total_expense": None, "fuel_dispensed": None},
        )
        self.assertEqual(
            warn_body["efficiency"],
            {"previous_odometer": 10000.0, "distance_delta": None, "km_per_l": None},
        )
        self.assertEqual(warn_body["meta"]["warning"], "Gemini quota exceeded")
        self.assertEqual(list(self.img_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
