from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from .base import ExtractedFuelData, normalize_extracted_json


class CustomModelExtractor:
    """Adapter for a self-hosted / fine-tuned vision model endpoint.

    Expects the endpoint at `CUSTOM_EXTRACTOR_URL` to accept a JSON POST body:
    {
        "model": "<model_id>",
        "odo_image_base64": "...",
        "odo_mime_type": "image/jpeg",
        "pump_image_base64": "...",
        "pump_mime_type": "image/jpeg"
    }
    and return JSON containing:
    {
        "odometer_reading": float | null,
        "total_expense": float | null,
        "fuel_dispensed": float | null
    }
    """

    def __init__(
        self,
        endpoint_url: str | None = None,
        model_id: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.endpoint_url = (
            endpoint_url or os.getenv("CUSTOM_EXTRACTOR_URL", "http://localhost:8080/predict").strip()
        )
        self.model_id = model_id or os.getenv("CUSTOM_EXTRACTOR_MODEL_ID", "custom-fuel-ft-v1").strip()
        self.api_key = api_key or os.getenv("CUSTOM_EXTRACTOR_API_KEY", "").strip()

    def extract(
        self,
        odo_bytes: bytes | None,
        odo_mime: str | None,
        pump_bytes: bytes | None,
        pump_mime: str | None,
    ) -> ExtractedFuelData:
        if not odo_bytes and not pump_bytes:
            return normalize_extracted_json(None)

        payload = {
            "model": self.model_id,
            "odo_image_base64": base64.b64encode(odo_bytes).decode("ascii") if odo_bytes else None,
            "odo_mime_type": odo_mime if odo_bytes else None,
            "pump_image_base64": base64.b64encode(pump_bytes).decode("ascii") if pump_bytes else None,
            "pump_mime_type": pump_mime if pump_bytes else None,
        }

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            self.endpoint_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Custom model error ({exc.code}): {err_body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Failed to reach custom extractor at {self.endpoint_url}: {exc.reason}") from exc

        # Support either top-level keys or nested {"extraction": {...}}
        target = resp_data.get("extraction", resp_data) if isinstance(resp_data, dict) else None
        return normalize_extracted_json(target)
