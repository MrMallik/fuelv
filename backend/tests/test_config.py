from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.config import validate_environment


class TestConfigValidation(unittest.TestCase):
    """Rigorous tests for zero-hardcoding environment configuration validator."""

    def setUp(self) -> None:
        self.valid_env = {
            "R2_ACCOUNT_ID": "test_account_id_12345",
            "R2_ACCESS_KEY_ID": "test_access_key",
            "R2_SECRET_ACCESS_KEY": "test_secret_key",
            "R2_BUCKET_NAME": "test-bucket",
            "R2_PUBLIC_BASE_URL": "https://r2.example.com",
            "GEMINI_API_KEY": "test_gemini_key",
            "FUELV_WRITE_KEY": "550e8400-e29b-41d4-a716-446655440000",
            "FUELV_DISABLE_AUTH": "0",
        }

    def test_validation_succeeds_when_all_configured(self) -> None:
        """All required variables present results in is_valid=True with zero missing vars."""
        with patch.dict(os.environ, self.valid_env, clear=True):
            result = validate_environment(load_dotenv=False, exit_on_failure=False)
            self.assertTrue(result.is_valid)
            self.assertEqual(result.missing_vars, [])

    def test_validation_fails_when_r2_vars_missing(self) -> None:
        """Missing any Cloudflare R2 variable flags it and marks is_valid=False."""
        for r2_key in ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME", "R2_PUBLIC_BASE_URL"]:
            env = dict(self.valid_env)
            env[r2_key] = ""
            with patch.dict(os.environ, env, clear=True):
                result = validate_environment(load_dotenv=False, exit_on_failure=False)
                self.assertFalse(result.is_valid)
                self.assertTrue(any(r2_key in item for item in result.missing_vars))

    def test_validation_fails_when_gemini_key_missing(self) -> None:
        """Gemini backend requires GEMINI_API_KEY."""
        env = dict(self.valid_env)
        env["GEMINI_API_KEY"] = ""
        with patch.dict(os.environ, env, clear=True):
            result = validate_environment(load_dotenv=False, exit_on_failure=False)
            self.assertFalse(result.is_valid)
            self.assertTrue(any("GEMINI_API_KEY" in item for item in result.missing_vars))

    def test_validation_fails_when_write_key_missing(self) -> None:
        """Missing FUELV_WRITE_KEY without auth bypass triggers validation failure."""
        env = dict(self.valid_env)
        env["FUELV_WRITE_KEY"] = ""
        env["FUELV_DISABLE_AUTH"] = "0"
        with patch.dict(os.environ, env, clear=True):
            result = validate_environment(load_dotenv=False, exit_on_failure=False)
            self.assertFalse(result.is_valid)
            self.assertTrue(any("FUELV_WRITE_KEY" in item for item in result.missing_vars))

    def test_validation_succeeds_when_auth_disabled_for_local_testing(self) -> None:
        """Setting FUELV_DISABLE_AUTH=1 allows running without FUELV_WRITE_KEY."""
        env = dict(self.valid_env)
        env["FUELV_WRITE_KEY"] = ""
        env["FUELV_DISABLE_AUTH"] = "1"
        with patch.dict(os.environ, env, clear=True):
            result = validate_environment(load_dotenv=False, exit_on_failure=False)
            self.assertTrue(result.is_valid)

    def test_exit_on_failure_stops_application(self) -> None:
        """validate_environment(exit_on_failure=True) calls sys.exit(1) on invalid config."""
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                validate_environment(load_dotenv=False, exit_on_failure=True)
            self.assertEqual(ctx.exception.code, 1)

    def test_validation_fails_on_invalid_r2_public_base_url_scheme(self) -> None:
        """R2_PUBLIC_BASE_URL without http/https scheme must fail pre-flight validation."""
        env = dict(self.valid_env)
        env["R2_PUBLIC_BASE_URL"] = "r2.example.com"
        with patch.dict(os.environ, env, clear=True):
            result = validate_environment(load_dotenv=False, exit_on_failure=False)
            self.assertFalse(result.is_valid)
            self.assertTrue(any("R2_PUBLIC_BASE_URL" in item for item in result.missing_vars))

    def test_validation_warns_on_non_uuid_write_key(self) -> None:
        """Non-UUID FUELV_WRITE_KEY produces a warning."""
        env = dict(self.valid_env)
        env["FUELV_WRITE_KEY"] = "not-a-uuid"
        with patch.dict(os.environ, env, clear=True):
            result = validate_environment(load_dotenv=False, exit_on_failure=False)
            self.assertTrue(result.is_valid)
            self.assertTrue(any("FUELV_WRITE_KEY" in w for w in result.warnings))

    def test_lifespan_validation_halts_startup_on_missing_config(self) -> None:
        """FastAPI lifespan hook raises RuntimeError and aborts startup when validation fails."""
        from fastapi.testclient import TestClient
        from backend.main import create_app

        with patch.dict(os.environ, {}, clear=True):
            with patch("backend.main.validate_environment") as mock_val:
                from backend.config import ValidationResult
                mock_val.return_value = ValidationResult(is_valid=False, missing_vars=["R2_BUCKET_NAME"], warnings=[])
                app = create_app(validate_on_startup=True)
                with self.assertRaises(RuntimeError) as ctx:
                    with TestClient(app):
                        pass
                self.assertIn("FuelV startup halted: missing required configuration", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
