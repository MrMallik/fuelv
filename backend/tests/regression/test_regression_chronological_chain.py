from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.db.repository import SnapshotRepository


class TestRegressionChronologicalChain(unittest.TestCase):
    """Regression tests guaranteeing _recalculate_chronological_chain maintains
    strict odometer/distance/efficiency consistency across backdated inserts, edits,
    deletions, zero-fuel entries, and odometer rollbacks.
    """

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_chain.db"
        self.repo = SnapshotRepository(db_path=self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_backdated_insert_at_beginning_promotes_old_first_log(self) -> None:
        """Inserting a log chronologically earlier than the current first log turns the old first log into a linked second log."""
        s_later = self.repo.insert_snapshot(
            captured_at="2026-11-10T10:00:00Z",
            odometer_reading=1500.0,
            total_expense=500.0,
            fuel_dispensed=10.0,
        )
        self.assertNotIn("previous_odometer", s_later["extra_data"])
        self.assertNotIn("distance_delta", s_later["extra_data"])
        self.assertNotIn("km_per_l", s_later["extra_data"])

        s_earlier = self.repo.insert_snapshot(
            captured_at="2026-11-01T10:00:00Z",
            odometer_reading=1000.0,
            total_expense=400.0,
            fuel_dispensed=8.0,
        )
        self.assertNotIn("previous_odometer", s_earlier["extra_data"])
        self.assertNotIn("distance_delta", s_earlier["extra_data"])
        self.assertNotIn("km_per_l", s_earlier["extra_data"])

        refreshed_later = self.repo.get_snapshot(s_later["id"])
        self.assertIsNotNone(refreshed_later)
        self.assertEqual(refreshed_later["extra_data"]["previous_odometer"], 1000.0)
        self.assertEqual(refreshed_later["extra_data"]["distance_delta"], 500.0)
        self.assertEqual(refreshed_later["extra_data"]["km_per_l"], 50.0)

    def test_backdated_insert_in_middle_relinks_subsequent_chain(self) -> None:
        """Inserting a log in the middle of an existing chain splits deltas cleanly."""
        s1 = self.repo.insert_snapshot(
            captured_at="2026-11-01T10:00:00Z",
            odometer_reading=1000.0,
            total_expense=100.0,
            fuel_dispensed=2.0,
        )
        s3 = self.repo.insert_snapshot(
            captured_at="2026-11-20T10:00:00Z",
            odometer_reading=1600.0,
            total_expense=300.0,
            fuel_dispensed=6.0,
        )
        self.assertEqual(s3["extra_data"]["distance_delta"], 600.0)
        self.assertEqual(s3["extra_data"]["km_per_l"], 100.0)

        s2 = self.repo.insert_snapshot(
            captured_at="2026-11-10T10:00:00Z",
            odometer_reading=1250.0,
            total_expense=250.0,
            fuel_dispensed=5.0,
        )
        self.assertEqual(s2["extra_data"]["previous_odometer"], 1000.0)
        self.assertEqual(s2["extra_data"]["distance_delta"], 250.0)
        self.assertEqual(s2["extra_data"]["km_per_l"], 50.0)

        s3_after = self.repo.get_snapshot(s3["id"])
        self.assertIsNotNone(s3_after)
        self.assertEqual(s3_after["extra_data"]["previous_odometer"], 1250.0)
        self.assertEqual(s3_after["extra_data"]["distance_delta"], 350.0)
        self.assertEqual(s3_after["extra_data"]["km_per_l"], 58.33)
        self.assertNotIn("previous_odometer", self.repo.get_snapshot(s1["id"])["extra_data"])  # type: ignore[index]

    def test_update_intermediate_odometer_fuel_and_captured_at_reorders_chain(self) -> None:
        """Updating odometer, fuel volume, or captured_at propagates recalculation across the reordered chain."""
        s1 = self.repo.insert_snapshot(
            captured_at="2026-11-01T10:00:00Z",
            odometer_reading=2000.0,
            total_expense=200.0,
            fuel_dispensed=4.0,
        )
        s2 = self.repo.insert_snapshot(
            captured_at="2026-11-05T10:00:00Z",
            odometer_reading=2200.0,
            total_expense=200.0,
            fuel_dispensed=4.0,
        )
        s3 = self.repo.insert_snapshot(
            captured_at="2026-11-10T10:00:00Z",
            odometer_reading=2500.0,
            total_expense=300.0,
            fuel_dispensed=5.0,
        )

        # Edit s2 fuel only -> s2 km/L updates, s3 untouched
        s2_fuel_edit = self.repo.update_snapshot(s2["id"], fuel_dispensed=5.0)
        self.assertEqual(s2_fuel_edit["extra_data"]["distance_delta"], 200.0)
        self.assertEqual(s2_fuel_edit["extra_data"]["km_per_l"], 40.0)
        self.assertEqual(self.repo.get_snapshot(s3["id"])["extra_data"]["distance_delta"], 300.0)  # type: ignore[index]

        # Edit s2 odometer -> both s2 and s3 recalculate
        s2_odo_edit = self.repo.update_snapshot(s2["id"], odometer_reading=2300.0)
        self.assertEqual(s2_odo_edit["extra_data"]["distance_delta"], 300.0)
        self.assertEqual(s2_odo_edit["extra_data"]["km_per_l"], 60.0)
        s3_after_odo = self.repo.get_snapshot(s3["id"])
        self.assertEqual(s3_after_odo["extra_data"]["previous_odometer"], 2300.0)  # type: ignore[index]
        self.assertEqual(s3_after_odo["extra_data"]["distance_delta"], 200.0)  # type: ignore[index]
        self.assertEqual(s3_after_odo["extra_data"]["km_per_l"], 40.0)  # type: ignore[index]

        # Move s3 timestamp before s1 and adjust odo to 1800 -> s3 becomes first, s1 becomes second
        self.repo.update_snapshot(s3["id"], captured_at="2026-10-25T10:00:00Z", odometer_reading=1800.0)
        s3_reordered = self.repo.get_snapshot(s3["id"])
        s1_reordered = self.repo.get_snapshot(s1["id"])
        s2_reordered = self.repo.get_snapshot(s2["id"])

        self.assertNotIn("previous_odometer", s3_reordered["extra_data"])  # type: ignore[index]
        self.assertNotIn("distance_delta", s3_reordered["extra_data"])  # type: ignore[index]
        self.assertNotIn("km_per_l", s3_reordered["extra_data"])  # type: ignore[index]

        self.assertEqual(s1_reordered["extra_data"]["previous_odometer"], 1800.0)  # type: ignore[index]
        self.assertEqual(s1_reordered["extra_data"]["distance_delta"], 200.0)  # type: ignore[index]
        self.assertEqual(s1_reordered["extra_data"]["km_per_l"], 50.0)  # type: ignore[index]

        self.assertEqual(s2_reordered["extra_data"]["previous_odometer"], 2000.0)  # type: ignore[index]
        self.assertEqual(s2_reordered["extra_data"]["distance_delta"], 300.0)  # type: ignore[index]

    def test_delete_first_middle_and_last_logs(self) -> None:
        """Deleting first, middle, or last log maintains valid links across remaining snapshots."""
        s1 = self.repo.insert_snapshot(
            captured_at="2026-11-01T10:00:00Z",
            odometer_reading=1000.0,
            total_expense=100.0,
            fuel_dispensed=2.0,
        )
        s2 = self.repo.insert_snapshot(
            captured_at="2026-11-02T10:00:00Z",
            odometer_reading=1100.0,
            total_expense=100.0,
            fuel_dispensed=2.0,
        )
        s3 = self.repo.insert_snapshot(
            captured_at="2026-11-03T10:00:00Z",
            odometer_reading=1250.0,
            total_expense=150.0,
            fuel_dispensed=3.0,
        )
        s4 = self.repo.insert_snapshot(
            captured_at="2026-11-04T10:00:00Z",
            odometer_reading=1450.0,
            total_expense=200.0,
            fuel_dispensed=4.0,
        )

        # 1. Delete middle log s2 -> s3 bridges directly to s1
        self.assertTrue(self.repo.delete_snapshot(s2["id"]))
        s3_after_mid = self.repo.get_snapshot(s3["id"])
        self.assertEqual(s3_after_mid["extra_data"]["previous_odometer"], 1000.0)  # type: ignore[index]
        self.assertEqual(s3_after_mid["extra_data"]["distance_delta"], 250.0)  # type: ignore[index]
        self.assertEqual(s3_after_mid["extra_data"]["km_per_l"], 83.33)  # type: ignore[index]

        # 2. Delete first log s1 -> s3 becomes new first, clearing its previous_odometer/delta/km_per_l
        self.assertTrue(self.repo.delete_snapshot(s1["id"]))
        s3_after_first = self.repo.get_snapshot(s3["id"])
        self.assertNotIn("previous_odometer", s3_after_first["extra_data"])  # type: ignore[index]
        self.assertNotIn("distance_delta", s3_after_first["extra_data"])  # type: ignore[index]
        self.assertNotIn("km_per_l", s3_after_first["extra_data"])  # type: ignore[index]

        # 3. Delete last log s4 -> s3 remains untouched as sole first log
        self.assertTrue(self.repo.delete_snapshot(s4["id"]))
        self.assertIsNone(self.repo.get_snapshot(s4["id"]))
        self.assertEqual(self.repo.get_latest_snapshot()["id"], s3["id"])  # type: ignore[index]

    def test_zero_fuel_equal_odometer_and_odometer_reset_edge_cases(self) -> None:
        """Zero fuel clears km_per_l, zero distance yields 0.0 km/L, and odometer rollback clears stale delta/km_per_l."""
        s1 = self.repo.insert_snapshot(
            captured_at="2026-11-01T10:00:00Z",
            odometer_reading=10000.0,
            total_expense=500.0,
            fuel_dispensed=5.0,
        )
        # Zero distance (same odometer) with positive fuel -> delta=0.0, km_per_l=0.0
        s2_same_odo = self.repo.insert_snapshot(
            captured_at="2026-11-02T10:00:00Z",
            odometer_reading=10000.0,
            total_expense=200.0,
            fuel_dispensed=2.0,
        )
        self.assertEqual(s2_same_odo["extra_data"]["previous_odometer"], 10000.0)
        self.assertEqual(s2_same_odo["extra_data"]["distance_delta"], 0.0)
        self.assertEqual(s2_same_odo["extra_data"]["km_per_l"], 0.0)

        # Positive distance with zero fuel -> delta preserved, km_per_l omitted
        s3_zero_fuel = self.repo.insert_snapshot(
            captured_at="2026-11-03T10:00:00Z",
            odometer_reading=10150.0,
            total_expense=0.0,
            fuel_dispensed=0.0,
        )
        self.assertEqual(s3_zero_fuel["extra_data"]["distance_delta"], 150.0)
        self.assertNotIn("km_per_l", s3_zero_fuel["extra_data"])

        # Positive distance and fuel first, then edited downward below previous odometer (rollback)
        s4 = self.repo.insert_snapshot(
            captured_at="2026-11-04T10:00:00Z",
            odometer_reading=10300.0,
            total_expense=300.0,
            fuel_dispensed=3.0,
        )
        self.assertEqual(s4["extra_data"]["distance_delta"], 150.0)
        self.assertEqual(s4["extra_data"]["km_per_l"], 50.0)

        # Rollback edit: odo=500.0 (< 10150.0) must clear stale distance_delta & km_per_l
        s4_rollback = self.repo.update_snapshot(s4["id"], odometer_reading=500.0)
        self.assertEqual(s4_rollback["extra_data"]["previous_odometer"], 10150.0)  # type: ignore[index]
        self.assertNotIn("distance_delta", s4_rollback["extra_data"])  # type: ignore[index]
        self.assertNotIn("km_per_l", s4_rollback["extra_data"])  # type: ignore[index]

        # Subsequent log after reset computes delta from 500.0
        s5_after_reset = self.repo.insert_snapshot(
            captured_at="2026-11-05T10:00:00Z",
            odometer_reading=750.0,
            total_expense=500.0,
            fuel_dispensed=5.0,
        )
        self.assertEqual(s5_after_reset["extra_data"]["previous_odometer"], 500.0)
        self.assertEqual(s5_after_reset["extra_data"]["distance_delta"], 250.0)
        self.assertEqual(s5_after_reset["extra_data"]["km_per_l"], 50.0)
        self.assertIsNotNone(s1)

    def test_identical_captured_at_tie_breaks_by_created_at(self) -> None:
        """Snapshots sharing the exact same captured_at tie-break deterministically on created_at ASC."""
        s_a = self.repo.insert_snapshot(
            captured_at="2026-11-15T12:00:00Z",
            odometer_reading=3000.0,
            total_expense=100.0,
            fuel_dispensed=1.0,
        )
        s_b = self.repo.insert_snapshot(
            captured_at="2026-11-15T12:00:00Z",
            odometer_reading=3120.0,
            total_expense=200.0,
            fuel_dispensed=2.0,
        )
        with self.repo._connect() as conn:
            conn.execute(
                "UPDATE fuel_snapshots SET created_at = '2026-11-15T12:00:00Z' WHERE id = ?",
                (s_a["id"],),
            )
            conn.execute(
                "UPDATE fuel_snapshots SET created_at = '2026-11-15T12:00:05Z' WHERE id = ?",
                (s_b["id"],),
            )
            self.repo._recalculate_chronological_chain(conn)
            conn.commit()

        a_ref = self.repo.get_snapshot(s_a["id"])
        b_ref = self.repo.get_snapshot(s_b["id"])
        self.assertNotIn("previous_odometer", a_ref["extra_data"])  # type: ignore[index]
        self.assertEqual(b_ref["extra_data"]["previous_odometer"], 3000.0)  # type: ignore[index]
        self.assertEqual(b_ref["extra_data"]["distance_delta"], 120.0)  # type: ignore[index]
        self.assertEqual(b_ref["extra_data"]["km_per_l"], 60.0)  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
