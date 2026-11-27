from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.db.repository import SnapshotRepository


class TestRegressionConcurrencyWal(unittest.TestCase):
    """Regression tests verifying SQLite WAL mode pragmas, rollback safety,
    and concurrent multi-thread read/write resilience without OperationalError locks.
    """

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_wal_concurrency.db"
        self.repo = SnapshotRepository(db_path=self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_wal_synchronous_busy_timeout_and_foreign_keys_pragmas(self) -> None:
        """Repository enforces WAL journal mode, synchronous=NORMAL, busy_timeout=5000, foreign_keys=ON."""
        with self.repo._connect() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            synchronous = conn.execute("PRAGMA synchronous;").fetchone()[0]
            busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
            foreign_keys = conn.execute("PRAGMA foreign_keys;").fetchone()[0]

        self.assertEqual(str(journal_mode).lower(), "wal")
        self.assertEqual(synchronous, 1)  # 1 == NORMAL
        self.assertEqual(busy_timeout, 5000)
        self.assertEqual(foreign_keys, 1)

    def test_connect_context_rolls_back_on_exception(self) -> None:
        """An unhandled exception inside _connect() rolls back uncommitted writes cleanly."""
        try:
            with self.repo._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    INSERT INTO fuel_snapshots (
                        id, captured_at, odometer_reading, total_expense, fuel_dispensed, entry_mode
                    ) VALUES ('rollback-id', '2026-11-01T10:00:00Z', 100.0, 10.0, 1.0, 'manual')
                    """
                )
                raise RuntimeError("Simulated mid-transaction failure")
        except RuntimeError:
            pass

        self.assertIsNone(self.repo.get_snapshot("rollback-id"))

    def test_concurrent_burst_inserts_and_reads_without_database_locked(self) -> None:
        """Burst concurrent writes and reads complete without OperationalError and leave a valid chronological chain."""
        total_writes = 32
        total_reads = 32

        def write_worker(idx: int) -> dict:
            # Deterministic strictly-increasing timestamp and odometer
            return self.repo.insert_snapshot(
                captured_at=f"2026-11-01T{idx // 60:02d}:{idx % 60:02d}:00Z",
                odometer_reading=1000.0 + idx * 100.0,
                total_expense=200.0,
                fuel_dispensed=2.0,
                extra_data={"worker_idx": idx},
            )

        def read_worker(_: int) -> tuple[int, dict | None]:
            grouped = self.repo.get_grouped_snapshots("month")
            latest = self.repo.get_latest_snapshot()
            return grouped["total_snapshots"], latest

        with ThreadPoolExecutor(max_workers=12) as pool:
            write_futures = [pool.submit(write_worker, i) for i in range(total_writes)]
            read_futures = [pool.submit(read_worker, i) for i in range(total_reads)]

            inserted = [f.result() for f in write_futures]
            reads = [f.result() for f in read_futures]

        self.assertEqual(len(inserted), total_writes)
        for count, _ in reads:
            self.assertGreaterEqual(count, 0)
            self.assertLessEqual(count, total_writes)

        # Verify all 32 writes landed and chronological chain is 100% consistent
        grouped = self.repo.get_grouped_snapshots("month")
        self.assertEqual(grouped["total_snapshots"], total_writes)
        snaps_asc = list(reversed(grouped["groups"][0]["snapshots"]))

        self.assertNotIn("previous_odometer", snaps_asc[0]["extra_data"])
        for i in range(1, len(snaps_asc)):
            prev_snap = snaps_asc[i - 1]
            curr_snap = snaps_asc[i]
            self.assertEqual(curr_snap["extra_data"]["previous_odometer"], prev_snap["odometer_reading"])
            self.assertEqual(curr_snap["extra_data"]["distance_delta"], 100.0)
            self.assertEqual(curr_snap["extra_data"]["km_per_l"], 50.0)

    def test_concurrent_updates_and_deletes_under_wal(self) -> None:
        """Concurrent update_snapshot and delete_snapshot calls succeed without database lock errors."""
        seeded_ids = [
            self.repo.insert_snapshot(
                captured_at=f"2026-11-02T{i:02d}:00:00Z",
                odometer_reading=2000.0 + i * 50.0,
                total_expense=100.0,
                fuel_dispensed=2.0,
            )["id"]
            for i in range(16)
        ]

        def updater(snap_id: str) -> dict | None:
            return self.repo.update_snapshot(snap_id, total_expense=125.0)

        def deleter(snap_id: str) -> bool:
            return self.repo.delete_snapshot(snap_id)

        with ThreadPoolExecutor(max_workers=8) as pool:
            upd_futures = [pool.submit(updater, sid) for sid in seeded_ids[:8]]
            del_futures = [pool.submit(deleter, sid) for sid in seeded_ids[8:]]

            updated = [f.result() for f in upd_futures]
            deleted = [f.result() for f in del_futures]

        self.assertTrue(all(item is not None and item["total_expense"] == 125.0 for item in updated))
        self.assertTrue(all(deleted))

        remaining = self.repo.get_grouped_snapshots("month")
        self.assertEqual(remaining["total_snapshots"], 8)
        snaps_asc = list(reversed(remaining["groups"][0]["snapshots"]))
        self.assertNotIn("previous_odometer", snaps_asc[0]["extra_data"])
        for i in range(1, len(snaps_asc)):
            self.assertEqual(
                snaps_asc[i]["extra_data"]["previous_odometer"],
                snaps_asc[i - 1]["odometer_reading"],
            )
            self.assertEqual(snaps_asc[i]["extra_data"]["distance_delta"], 50.0)
            self.assertEqual(snaps_asc[i]["extra_data"]["km_per_l"], 25.0)

        # Ensure WAL checkpoint / file integrity passes SQLite quick_check
        with self.repo._connect() as conn:
            check = conn.execute("PRAGMA quick_check;").fetchone()[0]
            self.assertEqual(check, "ok")
        self.assertTrue(isinstance(conn, sqlite3.Connection))


if __name__ == "__main__":
    unittest.main()
