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
from scrapers.base import ScrapeResult, build_result

logging.disable(logging.CRITICAL)  # monitor logs every check; keep test output readable


class StubScraper:
    """
    Returns queued raw page text, normalized through the real pipeline.

    A queued page may instead be a ``Fail("reason")``, which produces the same
    unsuccessful result a real scraper returns when a fetch breaks.
    """

    def __init__(self, pages):
        self.pages = list(pages)
        self.seen_specs = []

    def scrape(self, url, ignore_spec=""):
        self.seen_specs.append(ignore_spec)
        raw = self.pages.pop(0) if self.pages else ""
        if isinstance(raw, Fail):
            return ScrapeResult(success=False, error=raw.reason)
        return build_result(raw, ignore_spec)


class Fail:
    """Marker for a queued page that should fail to scrape."""

    def __init__(self, reason="connection refused"):
        self.reason = reason


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

        # Failure mail can be told to fail delivery, so the retry path is
        # testable; recovery mail is only ever recorded.
        self.failure_mail = []
        self.failure_mail_delivers = True
        self.recovery_mail = []
        self._patch_failure = mock.patch.object(
            monitor, "send_failure_alert",
            side_effect=lambda target, error, failures, since="": (
                self.failure_mail.append((error, failures, since)),
                self.failure_mail_delivers,
            )[1],
        )
        self._patch_failure.start()
        self._patch_recovery = mock.patch.object(
            monitor, "send_recovery_notice",
            side_effect=lambda target, failures, since="": (
                self.recovery_mail.append((failures, since)) or True
            ),
        )
        self._patch_recovery.start()

    def tearDown(self):
        self._patch_recovery.stop()
        self._patch_failure.stop()
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

    def test_status_reads_changed_after_a_change(self):
        """Regression: the snapshot write used to overwrite this with 'ok'."""
        target_id, _ = self.run_checks(["before text", "after text"])
        target = db.get_target(target_id)
        self.assertEqual(target["status"], "changed")
        self.assertIsNotNone(target["last_changed"])

    def test_status_returns_to_ok_on_the_next_quiet_check(self):
        """The badge reports the latest check, not a change that has passed."""
        target_id, _ = self.run_checks(["before", "after", "after"])
        self.assertEqual(db.get_target(target_id)["status"], "ok")

    def test_baseline_check_leaves_status_ok(self):
        target_id, _ = self.run_checks(["first page"])
        self.assertEqual(db.get_target(target_id)["status"], "ok")

    def test_a_change_still_clears_a_failure_streak(self):
        target_id, _ = self.run_checks(["before", Fail(), "after"])
        target = db.get_target(target_id)
        self.assertEqual(target["status"], "changed")
        self.assertEqual(target["consecutive_failures"], 0)

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


class TestFailureAlerts(MonitorTestCase):
    """
    A target that cannot be scraped emits no change alerts, which looks exactly
    like a target with nothing to report. These cover the mail that breaks that
    ambiguity.
    """

    def setUp(self):
        super().setUp()
        self._patch_threshold = mock.patch.object(monitor.config, "FAILURE_ALERT_THRESHOLD", 3)
        self._patch_threshold.start()

    def tearDown(self):
        self._patch_threshold.stop()
        super().tearDown()

    def test_a_single_failure_does_not_send_mail(self):
        self.run_checks([Fail("timeout")])
        self.assertEqual(self.failure_mail, [])

    def test_failures_below_the_threshold_stay_quiet(self):
        self.run_checks([Fail(), Fail()])
        self.assertEqual(self.failure_mail, [])

    def test_threshold_reached_sends_one_alert(self):
        self.run_checks([Fail("cookies expired")] * 3)
        self.assertEqual(len(self.failure_mail), 1)
        error, failures, since = self.failure_mail[0]
        self.assertEqual(error, "cookies expired")
        self.assertEqual(failures, 3)
        self.assertTrue(since, "the alert should say when the outage started")

    def test_continued_failure_does_not_re_alert(self):
        self.run_checks([Fail()] * 8)
        self.assertEqual(len(self.failure_mail), 1, "one mail per outage, not per check")

    def test_undeliverable_alert_is_retried_on_the_next_check(self):
        self.failure_mail_delivers = False
        target_id, _ = self.run_checks([Fail()] * 4)
        self.assertEqual(len(self.failure_mail), 2, "a failed send must not be recorded as sent")
        self.assertEqual(db.get_target(target_id)["failure_alert_sent"], 0)

    def test_empty_page_counts_as_a_failure(self):
        """Markup that yields no text is the quiet way a scraper dies."""
        self.run_checks(["", "", ""])
        self.assertEqual(len(self.failure_mail), 1)

    def test_failure_count_is_recorded_on_the_target(self):
        target_id, _ = self.run_checks([Fail("boom")] * 3)
        target = db.get_target(target_id)
        self.assertEqual(target["consecutive_failures"], 3)
        self.assertEqual(target["failure_alert_sent"], 1)
        self.assertIsNotNone(target["first_failure_at"])
        self.assertTrue(target["status"].startswith("error"))

    def test_a_success_clears_the_failure_streak(self):
        target_id, _ = self.run_checks([Fail(), Fail(), "real content"])
        target = db.get_target(target_id)
        self.assertEqual(target["consecutive_failures"], 0)
        self.assertIsNone(target["first_failure_at"])
        self.assertEqual(target["status"], "ok")

    def test_intermittent_failures_never_reach_the_threshold(self):
        self.run_checks([Fail(), "page", Fail(), "page", Fail(), "page"])
        self.assertEqual(self.failure_mail, [], "the streak must reset on every success")

    def test_recovery_is_reported_after_an_alerted_outage(self):
        self.run_checks([Fail()] * 3 + ["back online"])
        self.assertEqual(len(self.recovery_mail), 1)
        failures, since = self.recovery_mail[0]
        self.assertEqual(failures, 3)
        self.assertTrue(since)

    def test_no_recovery_notice_without_a_failure_alert(self):
        self.run_checks([Fail(), "back online"])
        self.assertEqual(self.recovery_mail, [], "nothing was reported, so nothing recovered")

    def test_recovery_is_reported_once(self):
        self.run_checks([Fail()] * 3 + ["back online", "back online"])
        self.assertEqual(len(self.recovery_mail), 1)

    def test_content_that_changed_during_an_outage_still_alerts(self):
        self.run_checks(["original", Fail(), Fail(), Fail(), "changed while down"])
        self.assertEqual(len(self.recovery_mail), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("changed while down", self.sent[0])

    def test_threshold_of_zero_disables_the_alert(self):
        with mock.patch.object(monitor.config, "FAILURE_ALERT_THRESHOLD", 0):
            self.run_checks([Fail()] * 10)
        self.assertEqual(self.failure_mail, [])

    def test_a_paused_target_is_not_checked(self):
        target_id = db.add_target("https://example.com/p", "T", "generic", 15)
        db.toggle_target(target_id, False)
        with mock.patch.object(monitor, "get_scraper", return_value=StubScraper([Fail()] * 5)):
            for _ in range(5):
                monitor.check_target(target_id)
        self.assertEqual(self.failure_mail, [])
        self.assertEqual(db.get_target(target_id)["consecutive_failures"], 0)


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
                target_cols = set(target)
                self.assertIn("consecutive_failures", target_cols)
                self.assertIn("failure_alert_sent", target_cols)
                self.assertIn("first_failure_at", target_cols)
                self.assertEqual(target["consecutive_failures"], 0)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
