from __future__ import annotations

import math
import os
from typing import Any, Protocol, TypedDict


class ExtractedFuelData(TypedDict):
    """Strict 3-parameter JSON contract returned by any extraction pipeline."""

    odometer_reading: float | None
    total_expense: float | None
    fuel_dispensed: float | None


def normalize_extracted_json(payload: dict[str, Any] | None) -> ExtractedFuelData:
    """Guarantees the extractor output strictly conforms to the 3-parameter JSON contract."""
    if not isinstance(payload, dict):
        return {
            "odometer_reading": None,
            "total_expense": None,
            "fuel_dispensed": None,
        }

    def _to_float(val: Any) -> float | None:
        if val is None or val == "":
            return None
        try:
            num = float(val)
            if not math.isfinite(num) or num < 0:
                return None
            return round(num, 3)
        except (TypeError, ValueError):
            return None

    return {
        "odometer_reading": _to_float(payload.get("odometer_reading")),
        "total_expense": _to_float(payload.get("total_expense")),
        "fuel_dispensed": _to_float(payload.get("fuel_dispensed")),
    }


class FuelExtractor(Protocol):
    """Swappable interface for extracting fuel snapshot numbers from Odometer & Pump images."""

    model_id: str

    def extract(
        self,
        odo_bytes: bytes | None,
        odo_mime: str | None,
        pump_bytes: bytes | None,
        pump_mime: str | None,
    ) -> ExtractedFuelData:
        """Takes raw bytes of odometer and fuel pump images and returns strict 3-field JSON."""
        ...


def get_extractor() -> FuelExtractor:
    """Factory that returns the configured extraction pipeline implementation."""
    backend = os.getenv("EXTRACTOR_BACKEND", "gemini").strip().lower()
    if backend == "custom":
        from .custom import CustomModelExtractor

        return CustomModelExtractor()

    from .gemini import GeminiFlashExtractor

    return GeminiFlashExtractor()
