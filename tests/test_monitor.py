"""
Integration tests for the check → diff → alert path.

These cover the two failure modes the normalization work targets:
  1. A page whose only movement is noise must not raise an alert.
  2. A change deep in a long page must appear in the alert diff.

Run with:  python3 -m unittest discover tests
"""

import logging
import os
import tempfile
import unittest
from unittest import mock

import database as db
import monitor
from scrapers.base import build_result

logging.disable(logging.CRITICAL)  # monitor logs every check; keep test output readable


class StubScraper:
    """Returns queued raw page text, normalized through the real pipeline."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.seen_specs = []

    def scrape(self, url, ignore_spec=""):
        self.seen_specs.append(ignore_spec)
        raw = self.pages.pop(0) if self.pages else ""
        return build_result(raw, ignore_spec)


class MonitorTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._patch_db = mock.patch.object(db, "DB_PATH", self.db_path)
        self._patch_db.start()
        db.init_db()

        self.sent = []
        self._patch_alert = mock.patch.object(
            monitor, "send_alert",
            side_effect=lambda target, summary: self.sent.append(summary) or True,
        )
        self._patch_alert.start()

    def tearDown(self):
        self._patch_alert.stop()
        self._patch_db.stop()
        os.unlink(self.db_path)

    def run_checks(self, pages, ignore_patterns=""):
        target_id = db.add_target("https://example.com/p", "T", "generic", 15, ignore_patterns)
        scraper = StubScraper(pages)
        with mock.patch.object(monitor, "get_scraper", return_value=scraper):
            for _ in pages:
                monitor.check_target(target_id)
        return target_id, scraper


class TestNoiseDoesNotAlert(MonitorTestCase):
    def test_baseline_check_never_alerts(self):
        self.run_checks(["Hello world"])
        self.assertEqual(self.sent, [])
        self.assertEqual(len(db.get_alerts()), 0)

    def test_counter_and_timestamp_churn_produces_no_alert(self):
        pages = [
            "Jane Doe\n@jane · 2h\nThe post text\n1,204 likes 8.7K views",
            "Jane Doe\n@jane · 3h\nThe post text\n1,251 likes 9.1K views",
            "Jane Doe\n@jane · 5h\nThe post text\n1,388 likes 9.9K views",
        ]
        self.run_checks(pages)
        self.assertEqual(self.sent, [], "noise-only churn should not alert")
        self.assertEqual(len(db.get_alerts()), 0)

    def test_unchanged_content_writes_no_extra_snapshots(self):
        target_id, _ = self.run_checks(["Same page"] * 4)
        conn = db.get_db()
        count = conn.execute(
            "SELECT COUNT(*) c FROM snapshots WHERE target_id = ?", (target_id,)
        ).fetchone()["c"]
        conn.close()
        self.assertEqual(count, 1, "only the baseline snapshot should be stored")

    def test_last_checked_still_advances_without_a_change(self):
        target_id, _ = self.run_checks(["Same page", "Same page"])
        self.assertIsNotNone(db.get_target(target_id)["last_checked"])
        self.assertEqual(db.get_target(target_id)["status"], "ok")


class TestRealChangesAlert(MonitorTestCase):
    def test_new_content_alerts_once(self):
        pages = [
            "@jane · 2h\nOriginal post\n100 likes",
            "@jane · 3h\nBrand new post\nOriginal post\n140 likes",
        ]
        self.run_checks(pages)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Brand new post", self.sent[0])

    def test_change_beyond_the_old_excerpt_limit_appears_in_diff(self):
        """Regression: diffs used to be built from a 500-char excerpt."""
        filler = "\n".join(f"boilerplate line {i}" for i in range(300))
        pages = [
            filler + "\nthe original ending",
            filler + "\nthe SECRET new ending",
        ]
        self.run_checks(pages)
        self.assertEqual(len(self.sent), 1)
        summary = self.sent[0]
        self.assertIn("SECRET new ending", summary)
        self.assertNotIn("text diff is empty", summary)

    def test_summary_reports_line_counts(self):
        self.run_checks(["line one", "line one\nline two"])
        self.assertRegex(self.sent[0], r"\d+ line\(s\) added, \d+ removed")

    def test_alert_row_is_recorded_with_the_diff(self):
        self.run_checks(["before text", "after text"])
        alerts = db.get_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIn("after text", alerts[0]["change_summary"])
        self.assertEqual(alerts[0]["email_sent"], 1)


class TestIgnorePatternsReachTheScraper(MonitorTestCase):
    def test_spec_is_passed_through(self):
        _, scraper = self.run_checks(["a", "b"], ignore_patterns="^Sponsored")
        self.assertEqual(scraper.seen_specs, ["^Sponsored", "^Sponsored"])

    def test_custom_pattern_suppresses_an_alert(self):
        pages = [
            "Real content\nSponsored: widget A",
            "Real content\nSponsored: widget B",
        ]
        self.run_checks(pages, ignore_patterns="^Sponsored.*$")
        self.assertEqual(self.sent, [])


class TestSnapshotRetention(MonitorTestCase):
    def test_snapshots_are_capped(self):
        pages = [f"unique content {i}" for i in range(db.SNAPSHOT_RETENTION + 15)]
        target_id, _ = self.run_checks(pages)
        conn = db.get_db()
        count = conn.execute(
            "SELECT COUNT(*) c FROM snapshots WHERE target_id = ?", (target_id,)
        ).fetchone()["c"]
        conn.close()
        self.assertLessEqual(count, db.SNAPSHOT_RETENTION)

    def test_most_recent_snapshot_survives_pruning(self):
        pages = [f"unique content {i}" for i in range(db.SNAPSHOT_RETENTION + 5)]
        target_id, _ = self.run_checks(pages)
        latest = db.get_latest_snapshot(target_id)
        self.assertIn(f"unique content {len(pages) - 1}", latest["content"])


class TestMigration(unittest.TestCase):
    def test_migration_adds_columns_to_an_old_database(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            # Build the pre-migration schema by hand.
            import sqlite3
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL,
                    label TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'generic',
                    check_interval INTEGER NOT NULL DEFAULT 15,
                    active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
                    last_checked TEXT, last_changed TEXT, status TEXT DEFAULT 'pending');
                CREATE TABLE snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, target_id INTEGER NOT NULL,
                    content_hash TEXT NOT NULL, content_excerpt TEXT,
                    checked_at TEXT NOT NULL);
            """)
            conn.execute(
                "INSERT INTO targets (url, label, created_at) VALUES ('u', 'l', 'now')")
            conn.commit()
            conn.close()

            with mock.patch.object(db, "DB_PATH", path):
                db.init_db()
                target = db.get_target(1)
                self.assertEqual(target["ignore_patterns"], "")
                probe = db.get_db()
                try:
                    cols = {r["name"] for r in probe.execute("PRAGMA table_info(snapshots)")}
                finally:
                    probe.close()
                self.assertIn("content", cols)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
