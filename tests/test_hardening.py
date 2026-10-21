"""
Tests for URL safety, platform detection, database integrity and the check
queue — the parts that decide what Tripwire will fetch and how safely it runs.

Run with:  python3 -m unittest discover tests
"""

import logging
import os
import socket
import tempfile
import threading
import unittest
from unittest import mock

import auth
import config
import database as db
import monitor
import urlsafety
from app import app
from scrapers import detect_platform, generic

logging.disable(logging.CRITICAL)

PASSWORD = "correct-horse-battery-staple"


def resolves_to(*addresses):
    """A getaddrinfo stand-in that answers with fixed addresses."""
    def fake(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in addresses]
    return fake


class TestCheckUrl(unittest.TestCase):
    def check(self, url, *addresses):
        addresses = addresses or ("93.184.216.34",)
        with mock.patch.object(config, "ALLOW_PRIVATE_URLS", False), \
                mock.patch("urlsafety.socket.getaddrinfo", resolves_to(*addresses)):
            return urlsafety.check_url(url)

    def test_public_https_is_allowed(self):
        self.assertIsNone(self.check("https://example.com/profile"))

    def test_scheme_must_be_http_or_https(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://x",
                    "javascript:alert(1)", "httpfoo://example.com"):
            with self.subTest(url=url):
                self.assertIsNotNone(self.check(url))

    def test_rejects_private_and_special_addresses(self):
        for address in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1",
                        "169.254.169.254", "0.0.0.0", "100.64.0.1", "::1",
                        "fe80::1", "fd00::1", "::ffff:127.0.0.1"):
            with self.subTest(address=address):
                self.assertIn("non-public", self.check("https://example.com/", address))

    def test_one_private_answer_among_public_ones_is_enough_to_reject(self):
        self.assertIsNotNone(self.check("https://example.com/", "93.184.216.34", "10.0.0.5"))

    def test_ip_literals_are_checked_too(self):
        self.assertIsNotNone(self.check("http://169.254.169.254/latest/meta-data/",
                                        "169.254.169.254"))
        self.assertIsNotNone(self.check("http://[::1]:8080/", "::1"))

    def test_embedded_credentials_rejected(self):
        self.assertIsNotNone(self.check("https://user:pw@example.com/"))

    def test_malformed_urls_rejected(self):
        for url in ("", "   ", "https://", "http://example.com:notaport/", "https://[::1"):
            with self.subTest(url=url):
                self.assertIsNotNone(self.check(url))

    def test_unresolvable_host_rejected(self):
        with mock.patch.object(config, "ALLOW_PRIVATE_URLS", False), \
                mock.patch("urlsafety.socket.getaddrinfo", side_effect=socket.gaierror):
            self.assertIn("cannot resolve", urlsafety.check_url("https://nope.invalid/"))

    def test_override_allows_private_addresses(self):
        with mock.patch.object(config, "ALLOW_PRIVATE_URLS", True):
            self.assertIsNone(urlsafety.check_url("http://192.168.1.10/status"))
            # The shape checks still apply.
            self.assertIsNotNone(urlsafety.check_url("file:///etc/passwd"))


class TestDetectPlatform(unittest.TestCase):
    def test_real_profiles(self):
        cases = {
            "https://x.com/someone": "twitter",
            "https://twitter.com/someone": "twitter",
            "https://mobile.twitter.com/someone": "twitter",
            "https://www.linkedin.com/in/someone/": "linkedin",
            "https://linkedin.com/company/acme": "linkedin",
            "https://www.facebook.com/somepage": "facebook",
            "https://example.com/blog": "generic",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(detect_platform(url), expected)

    def test_lookalike_urls_are_generic(self):
        # Each of these contains a platform's name somewhere, but none is on
        # that platform, so none may reach a scraper that carries its cookies.
        for url in (
            "https://evil.example/?next=x.com/a",
            "https://evil.example/twitter.com/someone",
            "https://notx.com/someone",
            "https://x.com.evil.example/someone",
            "https://evil.example/linkedin.com/in/someone",
            "https://facebook.com.evil.example/page",
            "https://example.com/#facebook.com/",
        ):
            with self.subTest(url=url):
                self.assertEqual(detect_platform(url), "generic")


class _FakeResponse:
    def __init__(self, status=200, headers=None, body=b"<p>hello</p>"):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308) and "Location" in self.headers

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} error")

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestGenericScraperSafety(unittest.TestCase):
    def scrape(self, url, responses, resolver):
        with mock.patch.object(config, "ALLOW_PRIVATE_URLS", False), \
                mock.patch("urlsafety.socket.getaddrinfo", resolver), \
                mock.patch.object(generic.requests, "get", side_effect=responses) as get:
            return generic.GenericScraper().scrape(url), get

    def test_fetches_a_public_page(self):
        result, _ = self.scrape("https://example.com/", [_FakeResponse()],
                                resolves_to("93.184.216.34"))
        self.assertTrue(result.success, result.error)
        self.assertIn("hello", result.content)

    def test_private_target_is_never_requested(self):
        result, get = self.scrape("http://169.254.169.254/", [_FakeResponse()],
                                  resolves_to("169.254.169.254"))
        self.assertFalse(result.success)
        self.assertIn("non-public", result.error)
        get.assert_not_called()

    def test_redirect_to_a_private_address_is_refused(self):
        def resolver(host, *a, **k):
            return resolves_to("10.0.0.5" if host == "internal.test" else "93.184.216.34")(host)

        redirect = _FakeResponse(302, {"Location": "http://internal.test/admin"})
        result, get = self.scrape("https://example.com/", [redirect, _FakeResponse()], resolver)
        self.assertFalse(result.success)
        self.assertIn("non-public", result.error)
        self.assertEqual(get.call_count, 1)  # the internal URL was never fetched

    def test_relative_redirect_is_followed(self):
        redirect = _FakeResponse(301, {"Location": "/moved"})
        result, get = self.scrape("https://example.com/old", [redirect, _FakeResponse()],
                                  resolves_to("93.184.216.34"))
        self.assertTrue(result.success, result.error)
        self.assertEqual(get.call_args.args[0], "https://example.com/moved")

    def test_redirect_loop_gives_up(self):
        loop = [_FakeResponse(302, {"Location": "https://example.com/"})] * 20
        result, _ = self.scrape("https://example.com/", loop, resolves_to("93.184.216.34"))
        self.assertFalse(result.success)
        self.assertIn("redirect", result.error.lower())

    def test_oversized_response_is_refused(self):
        big = _FakeResponse(body=b"a" * (generic.MAX_BODY_BYTES + 1))
        result, _ = self.scrape("https://example.com/", [big], resolves_to("93.184.216.34"))
        self.assertFalse(result.success)
        self.assertIn("larger than", result.error)


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._patches = [
            mock.patch.object(db, "DB_PATH", self.db_path),
            mock.patch.object(auth, "_PASSWORD_PLAIN", PASSWORD),
            mock.patch.object(auth, "_PASSWORD_HASH", ""),
            mock.patch.object(config, "API_TOKEN", ""),
            mock.patch.object(config, "ALLOW_PRIVATE_URLS", False),
            mock.patch("urlsafety.socket.getaddrinfo", resolves_to("93.184.216.34")),
            mock.patch.object(monitor, "schedule_target"),
            mock.patch.object(monitor, "unschedule_target"),
        ]
        for p in self._patches:
            p.start()
        db.init_db()
        auth._failures.clear()
        app.config["TESTING"] = True
        self.client = app.test_client()
        self.client.post("/login", data={"password": PASSWORD})

    def tearDown(self):
        for p in self._patches:
            p.stop()
        auth._failures.clear()
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.db_path + suffix):
                os.unlink(self.db_path + suffix)

    def add(self, **body):
        return self.client.post("/api/targets", json=body)


class TestAddTargets(ApiTestCase):
    def test_valid_url_is_added_with_detected_platform(self):
        res = self.add(urls=["https://x.com/someone"])
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.get_json()["added"][0]["platform"], "twitter")

    def test_private_url_is_rejected_and_nothing_is_stored(self):
        with mock.patch("urlsafety.socket.getaddrinfo", resolves_to("127.0.0.1")):
            res = self.add(urls=["http://localhost:5500/api/targets"])
        self.assertEqual(res.status_code, 400)
        self.assertIn("non-public", res.get_json()["details"][0])
        self.assertEqual(db.get_targets(), [])

    def test_one_bad_url_rejects_the_whole_batch(self):
        res = self.add(urls=["https://example.com/ok", "file:///etc/passwd"])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(db.get_targets(), [])

    def test_non_http_schemes_rejected(self):
        self.assertEqual(self.add(urls=["ftp://example.com/x"]).status_code, 400)
        self.assertEqual(self.add(urls=["httpfoo://example.com"]).status_code, 400)

    def test_lookalike_url_is_stored_as_generic(self):
        res = self.add(urls=["https://example.com/?next=x.com/a"])
        self.assertEqual(res.get_json()["added"][0]["platform"], "generic")

    def test_duplicate_urls_in_one_request_are_added_once(self):
        res = self.add(urls=["https://example.com/a", "https://example.com/a"])
        self.assertEqual(len(res.get_json()["added"]), 1)

    def test_urls_raw_is_still_supported(self):
        res = self.add(urls_raw="https://example.com/a\nhttps://example.com/b\n")
        self.assertEqual(len(res.get_json()["added"]), 2)

    def test_malformed_input_is_a_400_not_a_500(self):
        for body in ({"urls": 5}, {"urls": [None, 3]}, {"urls": {"a": 1}}):
            with self.subTest(body=body):
                self.assertEqual(self.add(**body).status_code, 400)

    def test_bad_interval_falls_back_and_good_one_is_clamped(self):
        res = self.add(urls=["https://example.com/a"], check_interval="soon")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.get_json()["added"][0]["check_interval"],
                         config.DEFAULT_CHECK_INTERVAL)
        res = self.add(urls=["https://example.com/b"], check_interval=1)
        self.assertEqual(res.get_json()["added"][0]["check_interval"], 5)

    def test_alerts_limit_is_bounded_and_tolerant(self):
        self.assertEqual(self.client.get("/api/alerts?limit=abc").status_code, 200)
        self.assertEqual(self.client.get("/api/alerts?limit=-5").status_code, 200)
        self.assertEqual(self.client.get("/api/alerts?limit=999999999").status_code, 200)


class TestHealthz(ApiTestCase):
    def test_reachable_without_login_and_reveals_nothing(self):
        anonymous = app.test_client()
        res = anonymous.get("/healthz")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), {"status": "ok"})

    def test_other_routes_still_require_login(self):
        self.assertEqual(app.test_client().get("/api/targets").status_code, 401)


class TestDatabaseIntegrity(ApiTestCase):
    def test_foreign_keys_and_wal_are_on_for_every_connection(self):
        conn = db.get_db()
        try:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertGreaterEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 1000)
        finally:
            conn.close()

    def test_deleting_a_target_removes_its_snapshots_and_alerts(self):
        target_id = db.add_target("https://example.com/", "T", "generic", 15)
        db.save_snapshot(target_id, "h", "content", "content")
        db.record_alert(target_id, "T", "https://example.com/", "diff")

        res = self.client.delete(f"/api/targets/{target_id}")
        self.assertEqual(res.status_code, 200)

        conn = db.get_db()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0], 0)
        finally:
            conn.close()

    def test_concurrent_writers_do_not_hit_database_is_locked(self):
        target_id = db.add_target("https://example.com/", "T", "generic", 15)
        errors = []

        def hammer():
            try:
                for i in range(25):
                    db.save_snapshot(target_id, f"h{i}", "x" * 1000, "x")
                    db.touch_target(target_id)
            except Exception as e:  # noqa: BLE001 - the failure is the assertion
                errors.append(e)

        threads = [threading.Thread(target=hammer) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class TestCheckQueue(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.target_id = db.add_target("https://example.com/", "T", "generic", 15)

    def test_check_endpoint_queues_and_returns_immediately(self):
        with mock.patch.object(monitor, "queue_check") as queued:
            res = self.client.post(f"/api/targets/{self.target_id}/check")
        self.assertEqual(res.status_code, 202)
        self.assertTrue(res.get_json()["queued"])
        queued.assert_called_once_with(self.target_id)

    def test_check_endpoint_404s_for_unknown_target(self):
        self.assertEqual(self.client.post("/api/targets/999/check").status_code, 404)

    def test_same_target_is_never_checked_twice_at_once(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def slow(target_id):
            calls.append(target_id)
            entered.set()
            release.wait(5)

        with mock.patch.object(monitor, "_check_target", side_effect=slow):
            first = threading.Thread(target=monitor.check_target, args=(self.target_id,))
            first.start()
            self.assertTrue(entered.wait(5))
            monitor.check_target(self.target_id)  # overlapping request: skipped
            release.set()
            first.join()
        self.assertEqual(calls, [self.target_id])
        # ...and the guard is released afterwards.
        with mock.patch.object(monitor, "_check_target") as again:
            monitor.check_target(self.target_id)
        again.assert_called_once()

    def test_target_deleted_mid_check_is_discarded_quietly(self):
        import sqlite3
        with mock.patch.object(monitor, "_check_target", side_effect=sqlite3.IntegrityError):
            monitor.check_target(self.target_id)  # must not raise
        self.assertNotIn(self.target_id, monitor._running)

    def test_stored_platform_that_does_not_match_the_url_uses_generic(self):
        # A row tagged 'twitter' but pointing elsewhere (say, from before the
        # detection fix) must not reach the cookie-carrying browser scraper.
        bad_id = db.add_target("https://evil.example/?x=twitter.com/a", "Bad", "twitter", 15)
        used = []
        stub = mock.Mock()
        stub.scrape.return_value = mock.Mock(success=False, error="stop here")
        with mock.patch.object(monitor, "get_scraper",
                               side_effect=lambda p: used.append(p) or stub), \
                mock.patch.object(monitor, "send_failure_alert", return_value=True):
            monitor.check_target(bad_id)
        self.assertEqual(used, ["generic"])

    def test_pool_is_bounded(self):
        self.assertEqual(monitor.scheduler._executors["default"]._pool._max_workers,
                         config.MAX_CONCURRENT_CHECKS)


if __name__ == "__main__":
    unittest.main()
