from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any

from .base import ExtractedFuelData, normalize_extracted_json

EXTRACTION_PROMPT = """You are a specialized OCR extraction pipeline for a vehicle fuel logging app.
Analyze the provided image(s):
1. Odometer image (if provided): Read the total vehicle odometer reading (in km or miles). Ignore trip meter readings if both total odometer and trip meter are visible.
2. Fuel pump display image (if provided): Read the total fuel cost/sale amount (`total_expense`) and the total volume of fuel dispensed in liters/gallons (`fuel_dispensed`). Ignore unit price per liter/gallon.

Return ONLY a JSON object with these exact 3 keys:
- "odometer_reading": number or null
- "total_expense": number or null
- "fuel_dispensed": number or null
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "odometer_reading": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Total vehicle odometer reading from the dashboard/odometer image.",
        },
        "total_expense": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Total fuel cost/expense shown on the fuel pump display.",
        },
        "fuel_dispensed": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Total volume of fuel dispensed (liters or gallons) shown on the fuel pump display.",
        },
    },
    "required": ["odometer_reading", "total_expense", "fuel_dispensed"],
}


class GeminiFlashExtractor:
    """Gemini Flash implementation of the FuelExtractor interface."""

    def __init__(self, api_key: str | None = None, model_name: str | None = None) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        self.model_id = model_name or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()

    def extract(
        self,
        odo_bytes: bytes | None,
        odo_mime: str | None,
        pump_bytes: bytes | None,
        pump_mime: str | None,
    ) -> ExtractedFuelData:
        if not odo_bytes and not pump_bytes:
            return normalize_extracted_json(None)

        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured. Set GEMINI_API_KEY in your environment or use manual fallback."
            )

        parts: list[dict[str, Any]] = [{"text": EXTRACTION_PROMPT}]

        if odo_bytes:
            parts.append({"text": "Image 1: Vehicle Odometer"})
            parts.append(
                {
                    "inline_data": {
                        "mime_type": odo_mime or "image/jpeg",
                        "data": base64.b64encode(odo_bytes).decode("ascii"),
                    }
                }
            )

        if pump_bytes:
            parts.append({"text": "Image 2: Fuel Pump Display"})
            parts.append(
                {
                    "inline_data": {
                        "mime_type": pump_mime or "image/jpeg",
                        "data": base64.b64encode(pump_bytes).decode("ascii"),
                    }
                }
            )

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_id}:generateContent?key={self.api_key}"
        )
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
                "responseSchema": RESPONSE_SCHEMA,
            },
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gemini API error ({exc.code}): {err_body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Failed to reach Gemini API: {exc.reason}") from exc

        try:
            raw_text = resp_data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(raw_text)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unexpected response structure from Gemini: {resp_data}") from exc

        return normalize_extracted_json(parsed)
