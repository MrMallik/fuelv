"""FuelV Configuration & Pre-Flight Validation Module.

Enforces zero hardcoded secrets and validates all required infrastructure
details (Cloudflare R2, Gemini VLM, and Security Keys) before app startup.
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


def load_dotenv_if_present() -> None:
    """Load variables from .env file into os.environ if present."""
    env_file = ROOT_DIR / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    missing_vars: list[str]
    warnings: list[str]


def validate_environment(
    *,
    load_dotenv: bool = True,
    exit_on_failure: bool = False,
) -> ValidationResult:
    """Validate all required environment configuration for FuelV before starting.

    Required Infrastructure Details:
    1. Cloudflare R2 S3-Compatible Object Storage:
       - R2_ACCOUNT_ID: Cloudflare Account ID
       - R2_ACCESS_KEY_ID: S3 Access Key ID
       - R2_SECRET_ACCESS_KEY: S3 Secret Access Key
       - R2_BUCKET_NAME: R2 Bucket Name (e.g. public-bucket)
       - R2_PUBLIC_BASE_URL: Public CDN Base URL (e.g. https://r2.yourdomain.com)

    2. VLM Extraction Engine:
       - GEMINI_API_KEY: Required when EXTRACTOR_BACKEND is 'gemini' (default)

    3. Security & Access:
       - FUELV_WRITE_KEY: Required UUID write protection key (or FUELV_DISABLE_AUTH=1 for dev)
    """
    if load_dotenv:
        load_dotenv_if_present()

    missing: list[str] = []
    warnings: list[str] = []

    # 1. Cloudflare R2 Object Storage
    required_r2 = {
        "R2_ACCOUNT_ID": "Cloudflare Account ID (Cloudflare Dashboard > R2)",
        "R2_ACCESS_KEY_ID": "S3-compatible Access Key ID",
        "R2_SECRET_ACCESS_KEY": "S3-compatible Secret Access Key",
        "R2_BUCKET_NAME": "Target R2 Bucket Name",
        "R2_PUBLIC_BASE_URL": "Public CDN Endpoint URL (e.g. https://r2.yourdomain.com)",
    }
    for var_name, desc in required_r2.items():
        val = os.getenv(var_name, "").strip()
        if not val:
            missing.append(f"{var_name}: {desc}")
        elif var_name == "R2_PUBLIC_BASE_URL" and not val.startswith(("http://", "https://")):
            missing.append(
                f"{var_name}: Must start with http:// or https:// (got {val!r})"
            )

    # 2. VLM Extraction Engine
    backend = os.getenv("EXTRACTOR_BACKEND", "gemini").strip().lower()
    if backend == "gemini":
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not gemini_key:
            missing.append("GEMINI_API_KEY: Google Gemini API key for VLM snapshot extraction")

    # 3. Write Protection Key
    auth_disabled = os.getenv("FUELV_DISABLE_AUTH", "").strip().lower() in ("1", "true", "yes")
    write_key = os.getenv("FUELV_WRITE_KEY", "").strip()
    if not write_key and not auth_disabled:
        missing.append(
            "FUELV_WRITE_KEY: UUID access key for write protection (or set FUELV_DISABLE_AUTH=1 for local testing)"
        )
    elif write_key and not auth_disabled:
        try:
            uuid.UUID(write_key)
        except ValueError:
            warnings.append(
                "FUELV_WRITE_KEY is not a valid UUID; write requests will be rejected by UUID parser."
            )

    is_valid = len(missing) == 0

    if not is_valid:
        banner = [
            "\n" + "=" * 78,
            " ❌ FATAL: FuelV Pre-Flight Configuration Validation Failed!",
            "=" * 78,
            "The application cannot start because the following required details are missing:\n",
        ]
        for item in missing:
            banner.append(f"  • {item}")
        banner.extend([
            "\nPlease supply these settings in your environment or in a .env file.",
            "=" * 78 + "\n",
        ])
        error_msg = "\n".join(banner)
        sys.stderr.write(error_msg)
        sys.stderr.flush()

        if exit_on_failure:
            sys.exit(1)

    return ValidationResult(is_valid=is_valid, missing_vars=missing, warnings=warnings)


if __name__ == "__main__":
    validate_environment(exit_on_failure=True)
    print("✅ FuelV configuration is valid.")
