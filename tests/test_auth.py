"""
Tests for dashboard access control.

The property that matters: nothing that reveals or changes the target list is
reachable without a credential, and a shared password cannot be brute forced.

Run with:  python3 -m unittest discover tests
"""

import logging
import os
import tempfile
import unittest
from unittest import mock

import auth
import config
import database as db
from app import app

logging.disable(logging.CRITICAL)  # failed logins are logged; keep output clean

PASSWORD = "correct-horse-battery-staple"


class AuthTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._patches = [
            mock.patch.object(db, "DB_PATH", self.db_path),
            mock.patch.object(auth, "_PASSWORD_PLAIN", PASSWORD),
            mock.patch.object(auth, "_PASSWORD_HASH", ""),
            mock.patch.object(config, "API_TOKEN", ""),
        ]
        for p in self._patches:
            p.start()
        db.init_db()

        auth._failures.clear()
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        auth._failures.clear()
        os.unlink(self.db_path)

    def login(self, password=PASSWORD):
        return self.client.post("/login", data={"password": password})


class TestGuard(AuthTestCase):
    def test_dashboard_redirects_to_login(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.headers["Location"])

    def test_api_returns_401_not_a_redirect(self):
        # A redirect would arrive at the dashboard's HTML and be reported as a
        # parse failure, hiding the real reason from the caller.
        res = self.client.get("/api/targets")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.get_json()["error"], "Authentication required")

    def test_every_api_route_is_guarded(self):
        cases = [
            ("get", "/api/targets"),
            ("post", "/api/targets"),
            ("get", "/api/alerts"),
            ("get", "/api/noise-rules"),
            ("delete", "/api/targets/1"),
            ("post", "/api/targets/1/toggle"),
            ("post", "/api/targets/1/check"),
            ("put", "/api/targets/1/patterns"),
        ]
        for method, path in cases:
            with self.subTest(path=f"{method.upper()} {path}"):
                res = getattr(self.client, method)(path)
                self.assertEqual(res.status_code, 401)

    def test_unknown_paths_do_not_reveal_themselves(self):
        # Anonymous callers get the same answer for real and imaginary routes.
        res = self.client.get("/does-not-exist")
        self.assertEqual(res.status_code, 302)

    def test_login_page_and_static_are_public(self):
        self.assertEqual(self.client.get("/login").status_code, 200)
        self.assertEqual(self.client.get("/static/style.css").status_code, 200)


class TestLogin(AuthTestCase):
    def test_correct_password_grants_access(self):
        res = self.login()
        self.assertEqual(res.status_code, 302)
        self.assertEqual(self.client.get("/api/targets").status_code, 200)

    def test_wrong_password_is_rejected(self):
        res = self.login("hunter2")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(self.client.get("/api/targets").status_code, 401)

    def test_empty_password_is_rejected(self):
        self.assertEqual(self.login("").status_code, 401)

    def test_hashed_credential_is_accepted(self):
        from werkzeug.security import generate_password_hash
        with mock.patch.object(auth, "_PASSWORD_HASH", generate_password_hash(PASSWORD)), \
             mock.patch.object(auth, "_PASSWORD_PLAIN", ""):
            self.assertEqual(self.login().status_code, 302)
            self.assertEqual(self.client.get("/api/targets").status_code, 200)

    def test_logout_ends_the_session(self):
        self.login()
        self.assertEqual(self.client.post("/logout").status_code, 302)
        self.assertEqual(self.client.get("/api/targets").status_code, 401)

    def test_changing_the_password_invalidates_old_sessions(self):
        self.login()
        with mock.patch.object(auth, "_PASSWORD_PLAIN", "a-different-password"):
            self.assertEqual(self.client.get("/api/targets").status_code, 401)

    def test_next_must_stay_on_this_site(self):
        for hostile in ("//evil.example", "https://evil.example/x"):
            with self.subTest(next=hostile):
                res = self.client.post(
                    "/login", data={"password": PASSWORD, "next": hostile})
                self.assertEqual(res.headers["Location"], "/")

    def test_next_returns_to_the_requested_page(self):
        res = self.client.post(
            "/login", data={"password": PASSWORD, "next": "/?filter=x"})
        self.assertEqual(res.headers["Location"], "/?filter=x")


class TestThrottling(AuthTestCase):
    def test_repeated_failures_lock_the_client_out(self):
        for _ in range(auth.LOCKOUT_THRESHOLD):
            self.login("wrong")

        res = self.login("wrong")
        self.assertEqual(res.status_code, 429)

        # The lockout has to hold even for the right password, or an attacker
        # simply keeps guessing until one lands.
        self.assertEqual(self.login().status_code, 429)
        self.assertEqual(self.client.get("/api/targets").status_code, 401)

    def test_a_successful_login_clears_the_counter(self):
        for _ in range(auth.LOCKOUT_THRESHOLD - 1):
            self.login("wrong")
        self.assertEqual(self.login().status_code, 302)

        self.client.post("/logout")
        for _ in range(auth.LOCKOUT_THRESHOLD - 1):
            self.login("wrong")
        self.assertEqual(self.login().status_code, 302)

    def test_expired_failures_stop_counting(self):
        for _ in range(auth.LOCKOUT_THRESHOLD):
            self.login("wrong")
        stale = [t - auth.LOCKOUT_WINDOW - 1 for t in auth._failures["127.0.0.1"]]
        auth._failures["127.0.0.1"] = stale
        self.assertEqual(self.login().status_code, 302)


class TestApiToken(AuthTestCase):
    def test_bearer_token_authenticates_scripted_callers(self):
        with mock.patch.object(config, "API_TOKEN", "s3cret-token"):
            res = self.client.get(
                "/api/targets", headers={"Authorization": "Bearer s3cret-token"})
            self.assertEqual(res.status_code, 200)

    def test_wrong_token_is_rejected(self):
        with mock.patch.object(config, "API_TOKEN", "s3cret-token"):
            res = self.client.get(
                "/api/targets", headers={"Authorization": "Bearer nope"})
            self.assertEqual(res.status_code, 401)

    def test_token_auth_is_off_when_unconfigured(self):
        # Otherwise an unset token would match an empty presented credential.
        res = self.client.get("/api/targets", headers={"Authorization": "Bearer "})
        self.assertEqual(res.status_code, 401)


class TestPasswordIsAlwaysRequired(AuthTestCase):
    def test_a_password_is_generated_when_none_is_configured(self):
        with mock.patch.object(config, "DASHBOARD_PASSWORD", ""), \
             mock.patch.object(config, "DASHBOARD_PASSWORD_HASH", ""):
            pw_hash, plain, generated = auth._resolve_credential()
        self.assertTrue(generated)
        self.assertEqual(pw_hash, "")
        self.assertGreaterEqual(len(plain), 16)

    def test_configured_hash_wins_over_plain_password(self):
        with mock.patch.object(config, "DASHBOARD_PASSWORD", "plain"), \
             mock.patch.object(config, "DASHBOARD_PASSWORD_HASH", "a-hash"):
            pw_hash, plain, generated = auth._resolve_credential()
        self.assertEqual((pw_hash, plain, generated), ("a-hash", "", False))


if __name__ == "__main__":
    unittest.main()
