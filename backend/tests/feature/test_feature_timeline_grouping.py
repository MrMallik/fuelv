from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.db.repository import SnapshotRepository
from backend.main import create_app


class TestFeatureTimelineGrouping(unittest.TestCase):
    """Feature tests for timeline groupings (day, week, month, year), aggregate consistency,
    leap year & year boundary transitions, timezone prefix handling, and empty DB states.
    """

    def setUp(self) -> None:
        self.env_patcher = patch.dict(
            os.environ,
            {
                "FUELV_WRITE_KEY": "",
                "FUELV_DISABLE_AUTH": "1",
                "RATE_LIMIT_ENABLED": "false",
            },
            clear=False,
        )
        self.env_patcher.start()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_grouping.db"
        self.img_dir = Path(self.tmp_dir.name) / "images"
        self.img_dir.mkdir(parents=True, exist_ok=True)
        self.repo = SnapshotRepository(db_path=self.db_path)
        self.app = create_app(
            db_path=self.db_path,
            images_dir=self.img_dir,
            validate_on_startup=False,
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.tmp_dir.cleanup()

    def test_empty_database_returns_zeroed_aggregates_for_all_periods(self) -> None:
        """Empty database returns zero totals and empty groups for day, week, month, and year."""
        for period in ("day", "week", "month", "year"):
            res = self.client.get(f"/api/snapshots/grouped?by={period}")
            self.assertEqual(res.status_code, 200)
            body = res.json()
            self.assertEqual(body["group_by"], period)
            self.assertEqual(body["total_snapshots"], 0)
            self.assertEqual(body["total_logs"], 0)
            self.assertEqual(body["overall_expense"], 0.0)
            self.assertEqual(body["overall_fuel_dispensed"], 0.0)
            self.assertEqual(body["overall_distance"], 0.0)
            self.assertEqual(body["groups"], [])

    def test_single_snapshot_has_zero_overall_distance(self) -> None:
        """A single snapshot cannot compute a span and reports overall_distance=0.0."""
        self.repo.insert_snapshot(
            captured_at="2026-06-15T11:30:00+05:30",
            odometer_reading=8450.2,
            total_expense=350.5,
            fuel_dispensed=3.45,
        )
        grouped = self.repo.get_grouped_snapshots("day")
        self.assertEqual(grouped["total_snapshots"], 1)
        self.assertEqual(grouped["total_logs"], 1)
        self.assertEqual(grouped["overall_expense"], 350.5)
        self.assertEqual(grouped["overall_fuel_dispensed"], 3.45)
        self.assertEqual(grouped["overall_distance"], 0.0)
        self.assertEqual(len(grouped["groups"]), 1)
        grp = grouped["groups"][0]
        self.assertEqual(grp["period"], "2026-06-15")
        self.assertEqual(grp["min_odometer"], 8450.2)
        self.assertEqual(grp["max_odometer"], 8450.2)

    def test_grouping_across_day_week_month_year_and_aggregate_consistency(self) -> None:
        """All 4 grouping modes preserve aggregate sums, min/max odometer, and descending order."""
        entries = [
            ("2025-12-30T09:00:00+05:30", 1000.0, 100.12, 1.111),
            ("2025-12-30T19:00:00+05:30", 1120.5, 200.23, 2.222),
            ("2026-01-02T08:00:00+05:30", 1300.0, 300.34, 3.333),
            ("2026-01-12T16:15:00+05:30", 1550.5, 400.45, 4.444),
        ]
        for ts, odo, exp, fuel in entries:
            self.repo.insert_snapshot(
                captured_at=ts,
                odometer_reading=odo,
                total_expense=exp,
                fuel_dispensed=fuel,
            )

        # Day grouping: 2025-12-30 has 2 snapshots; 2026-01-02 and 2026-01-12 have 1 each
        by_day = self.repo.get_grouped_snapshots("day")
        self.assertEqual([g["period"] for g in by_day["groups"]], ["2026-01-12", "2026-01-02", "2025-12-30"])
        self.assertEqual(by_day["overall_expense"], 1001.14)
        self.assertEqual(by_day["overall_fuel_dispensed"], 11.11)
        self.assertEqual(by_day["overall_distance"], 550.5)

        dec30 = by_day["groups"][2]
        self.assertEqual(dec30["snapshot_count"], 2)
        self.assertEqual(dec30["total_expense"], 300.35)
        self.assertEqual(dec30["total_fuel_dispensed"], 3.333)
        self.assertEqual(dec30["min_odometer"], 1000.0)
        self.assertEqual(dec30["max_odometer"], 1120.5)
        # Snapshots inside group are ordered newest first
        self.assertEqual(dec30["snapshots"][0]["captured_at"], "2025-12-30T19:00:00+05:30")
        self.assertEqual(dec30["snapshots"][1]["captured_at"], "2025-12-30T09:00:00+05:30")

        # Week grouping: 2025-12-30 is W52, 2026-01-02 is W00, 2026-01-12 is W02
        by_week = self.repo.get_grouped_snapshots("week")
        self.assertEqual([g["period"] for g in by_week["groups"]], ["2026-W02", "2026-W00", "2025-W52"])

        # Month grouping
        by_month = self.repo.get_grouped_snapshots("month")
        self.assertEqual([g["period"] for g in by_month["groups"]], ["2026-01", "2025-12"])
        self.assertEqual(by_month["groups"][0]["total_expense"], 700.79)
        self.assertEqual(by_month["groups"][0]["min_odometer"], 1300.0)
        self.assertEqual(by_month["groups"][0]["max_odometer"], 1550.5)

        # Year grouping
        by_year = self.repo.get_grouped_snapshots("year")
        self.assertEqual([g["period"] for g in by_year["groups"]], ["2026", "2025"])
        self.assertEqual(by_year["groups"][0]["snapshot_count"], 2)
        self.assertEqual(by_year["groups"][1]["snapshot_count"], 2)

        # Cross-granularity invariant: sum of group totals equals overall totals
        for period in ("day", "week", "month", "year"):
            data = self.client.get(f"/api/snapshots/grouped?by={period}").json()
            self.assertEqual(sum(g["snapshot_count"] for g in data["groups"]), data["total_snapshots"])
            self.assertEqual(round(sum(g["total_expense"] for g in data["groups"]), 2), data["overall_expense"])
            self.assertEqual(
                round(sum(g["total_fuel_dispensed"] for g in data["groups"]), 3),
                data["overall_fuel_dispensed"],
            )

    def test_leap_year_feb_29_and_timezone_offset_bucketing(self) -> None:
        """Leap-day 2024-02-29 is bucketed accurately and timezone offsets preserve wall-clock local date."""
        self.repo.insert_snapshot(
            captured_at="2024-02-29T23:45:00+05:30",
            odometer_reading=2000.0,
            total_expense=250.0,
            fuel_dispensed=2.5,
        )
        self.repo.insert_snapshot(
            captured_at="2024-02-29T01:15:00-08:00",
            odometer_reading=2100.0,
            total_expense=150.0,
            fuel_dispensed=1.5,
        )
        self.repo.insert_snapshot(
            captured_at="2024-03-01T09:00:00Z",
            odometer_reading=2250.0,
            total_expense=300.0,
            fuel_dispensed=3.0,
        )

        by_day = self.repo.get_grouped_snapshots("day")
        self.assertEqual([g["period"] for g in by_day["groups"]], ["2024-03-01", "2024-02-29"])
        leap_grp = by_day["groups"][1]
        self.assertEqual(leap_grp["snapshot_count"], 2)
        self.assertEqual(leap_grp["total_expense"], 400.0)
        self.assertEqual(leap_grp["min_odometer"], 2000.0)
        self.assertEqual(leap_grp["max_odometer"], 2100.0)

        by_month = self.repo.get_grouped_snapshots("month")
        self.assertEqual([g["period"] for g in by_month["groups"]], ["2024-03", "2024-02"])

    def test_unparseable_timestamp_falls_back_to_unknown_and_invalid_period_rejected(self) -> None:
        """Invalid group_by raises ValueError / 422; unparseable captured_at falls back to 'Unknown'."""
        with self.assertRaises(ValueError):
            self.repo.get_grouped_snapshots("quarter")  # type: ignore[arg-type]

        bad_query = self.client.get("/api/snapshots/grouped?by=quarter")
        self.assertEqual(bad_query.status_code, 422)

        self.repo.insert_snapshot(
            captured_at="not-an-iso-timestamp",
            odometer_reading=500.0,
            total_expense=50.0,
            fuel_dispensed=0.5,
        )
        grouped = self.repo.get_grouped_snapshots("month")
        self.assertEqual(grouped["groups"][0]["period"], "Unknown")
        self.assertEqual(grouped["groups"][0]["snapshot_count"], 1)


if __name__ == "__main__":
    unittest.main()
