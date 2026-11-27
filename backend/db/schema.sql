PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS fuel_snapshots (
    id                   TEXT PRIMARY KEY,
    captured_at          TEXT NOT NULL,             -- ISO-8601 timestamp (e.g., '2026-11-17T22:50:00+05:30')

    -- Stored image relative paths inside data/images/
    odo_image_path       TEXT,
    pump_image_path      TEXT,

    -- Core v1 metrics (user-confirmed final values)
    odometer_reading     REAL NOT NULL,
    total_expense        REAL NOT NULL,
    fuel_dispensed       REAL NOT NULL,

    -- Extraction pipeline provenance & ground-truth dataset tracking for future fine-tuning
    entry_mode           TEXT NOT NULL DEFAULT 'vision'
                        CHECK (entry_mode IN ('vision', 'manual', 'vision_edited')),
    extractor_model      TEXT,
    raw_extraction_json TEXT CHECK (raw_extraction_json IS NULL OR json_valid(raw_extraction_json)),

    -- Extensible JSON envelope for future schema additions (vehicle_id, fuel_grade, station, notes, etc.)
    schema_version       INTEGER NOT NULL DEFAULT 1,
    extra_data          TEXT NOT NULL DEFAULT '{}'
                        CHECK (json_valid(extra_data)),

    created_at          TEXT NOT NULL DEFAULT ( strftime('%Y-%m-%dT%H:%M:%SZ', 'now') )
);

CREATE INDEX IF NOT EXISTS idx_fuel_snapshots_captured_at
    ON fuel_snapshots (captured_at DESC);
