"""
Access control for the Tripwire dashboard.

Tripwire's target list is a list of people an analyst is watching, and its
alert history quotes what those people published. That is not something to
serve to whoever finds the URL, so the app fails closed: every route requires
an authenticated session, and when no password is configured one is generated
at startup and printed to the log rather than leaving the dashboard open.

Credentials come from the environment, in order of preference:

    DASHBOARD_PASSWORD_HASH   a werkzeug password hash (recommended)
    DASHBOARD_PASSWORD        the password in plain text
    (neither)                 a random password, generated once per process

Scripted callers that cannot hold a cookie may set API_TOKEN and send it as
`Authorization: Bearer <token>`.
"""

import hmac
import logging
import secrets
import threading
import time
from functools import wraps

from flask import (
    Blueprint, current_app, jsonify, redirect, render_template,
    request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import config

log = logging.getLogger("tripwire.auth")

bp = Blueprint("auth", __name__)

# Endpoints reachable without a session. Everything else — including any URL
# that does not resolve — requires one, so an anonymous caller cannot map the
# API by watching which paths 404 and which redirect.
PUBLIC_ENDPOINTS = {"auth.login", "static", "healthz"}

# Failed logins are throttled per client address. A single shared password is
# exactly the shape brute force is good against, and the window is long enough
# that guessing is impractical while a mistyped password still clears quickly.
LOCKOUT_THRESHOLD = 8
LOCKOUT_WINDOW = 600  # seconds
_MAX_TRACKED_CLIENTS = 2048

_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def _resolve_credential() -> tuple[str, str, bool]:
    """
    Return (password_hash, plain_password, was_generated).

    Exactly one of the two credential forms is populated: a configured hash is
    used as-is, a configured plain password is compared directly, and with
    neither set a password is generated so the dashboard is never unprotected.
    """
    if config.DASHBOARD_PASSWORD_HASH:
        return config.DASHBOARD_PASSWORD_HASH, "", False
    if config.DASHBOARD_PASSWORD:
        return "", config.DASHBOARD_PASSWORD, False
    return "", secrets.token_urlsafe(18), True


_PASSWORD_HASH, _PASSWORD_PLAIN, PASSWORD_GENERATED = _resolve_credential()


def verify_password(candidate: str) -> bool:
    """Check a submitted password against the configured credential."""
    if not candidate:
        return False
    if _PASSWORD_HASH:
        return check_password_hash(_PASSWORD_HASH, candidate)
    return hmac.compare_digest(candidate, _PASSWORD_PLAIN)


def _credential_fingerprint() -> str:
    """
    A stable marker for the credential currently in force.

    It is stored in the session so that changing the password logs out every
    existing session, instead of leaving old cookies valid indefinitely.
    """
    import hashlib
    material = (_PASSWORD_HASH or _PASSWORD_PLAIN).encode()
    return hashlib.sha256(material).hexdigest()[:16]


def _token_is_valid() -> bool:
    """True when the request carries the configured API bearer token."""
    if not config.API_TOKEN:
        return False
    header = request.headers.get("Authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return False
    return hmac.compare_digest(presented.strip(), config.API_TOKEN)


def session_is_valid() -> bool:
    return (
        session.get("authenticated") is True
        and session.get("credential") == _credential_fingerprint()
    )


# ---------------------------------------------------------------------------
# Brute-force throttling
# ---------------------------------------------------------------------------

def _client_key() -> str:
    return request.remote_addr or "unknown"


def lockout_seconds_remaining() -> int:
    """Seconds until this client may try again, or 0 if it may try now."""
    now = time.time()
    key = _client_key()
    with _failures_lock:
        attempts = [t for t in _failures.get(key, []) if now - t < LOCKOUT_WINDOW]
        if attempts:
            _failures[key] = attempts
        else:
            _failures.pop(key, None)
        if len(attempts) < LOCKOUT_THRESHOLD:
            return 0
        return max(1, int(LOCKOUT_WINDOW - (now - attempts[0])))


def record_failure() -> None:
    now = time.time()
    key = _client_key()
    with _failures_lock:
        # Sweep expired clients before growing the map, so a spray of forged
        # source addresses cannot make it grow without bound.
        if len(_failures) > _MAX_TRACKED_CLIENTS:
            for tracked, attempts in list(_failures.items()):
                if all(now - t >= LOCKOUT_WINDOW for t in attempts):
                    del _failures[tracked]
        _failures.setdefault(key, []).append(now)


def clear_failures() -> None:
    with _failures_lock:
        _failures.pop(_client_key(), None)


# ---------------------------------------------------------------------------
# Request guard
# ---------------------------------------------------------------------------

def _unauthenticated_response():
    """
    Answer in the shape the caller can act on: JSON for the API, a redirect to
    the login page for anything a browser is displaying.
    """
    if request.path.startswith("/api/"):
        return jsonify({"error": "Authentication required"}), 401
    return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))


def require_login():
    """`before_request` hook: reject anything without a session or token."""
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if session_is_valid() or _token_is_valid():
        return None
    return _unauthenticated_response()


def login_required(view):
    """Decorator form, for routes registered outside the guarded app."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not (session_is_valid() or _token_is_valid()):
            return _unauthenticated_response()
        return view(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _safe_next(raw: str) -> str:
    """
    Only ever redirect back to a path on this site.

    A `next` value is attacker-supplied, so anything that could leave the
    origin — an absolute URL, or a protocol-relative `//host` — is discarded.
    """
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return url_for("index")
    return raw


@bp.route("/login", methods=["GET", "POST"])
def login():
    target = _safe_next(request.values.get("next", ""))

    if session_is_valid():
        return redirect(target)

    if request.method == "GET":
        return render_template("login.html", next=target)

    locked = lockout_seconds_remaining()
    if locked:
        minutes = max(1, round(locked / 60))
        return render_template(
            "login.html", next=target,
            error=f"Too many failed attempts. Try again in {minutes} minute(s).",
        ), 429

    if not verify_password(request.form.get("password", "")):
        record_failure()
        log.warning("Failed login attempt from %s", _client_key())
        return render_template(
            "login.html", next=target, error="Incorrect password.",
        ), 401

    clear_failures()
    session.clear()
    session["authenticated"] = True
    session["credential"] = _credential_fingerprint()
    session.permanent = True
    log.info("Successful login from %s", _client_key())
    return redirect(target)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Startup wiring
# ---------------------------------------------------------------------------

def init_app(app):
    """Attach session hardening, the login routes, and the request guard."""
    from datetime import timedelta

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.SECURE_COOKIES,
        PERMANENT_SESSION_LIFETIME=timedelta(days=config.SESSION_LIFETIME_DAYS),
    )

    if config.TRUST_PROXY:
        # Behind Render's proxy every request otherwise appears to come from
        # the proxy itself, which would collapse the login throttle into a
        # single shared bucket for the whole internet.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.register_blueprint(bp)
    app.before_request(require_login)
    return app


def log_startup_state():
    """Say plainly, once, how the dashboard is protected on this run."""
    if PASSWORD_GENERATED:
        log.warning(
            "No DASHBOARD_PASSWORD set — generated one for this run only:\n"
            "\n    %s\n\n"
            "It changes on every restart. Set DASHBOARD_PASSWORD (or "
            "DASHBOARD_PASSWORD_HASH) to keep a stable login.",
            _PASSWORD_PLAIN,
        )
    if config.SECRET_KEY_GENERATED:
        log.warning(
            "No SECRET_KEY set — generated a random one. Logins will not "
            "survive a restart until SECRET_KEY is configured."
        )
    if not config.SECURE_COOKIES:
        log.info(
            "SECURE_COOKIES is off — fine over http://localhost, but set it "
            "to 1 wherever Tripwire is served over HTTPS."
        )


if __name__ == "__main__":
    # `python3 auth.py` prints a hash to paste into DASHBOARD_PASSWORD_HASH,
    # so the password itself never has to be stored in the environment.
    import getpass

    first = getpass.getpass("New dashboard password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords did not match.")
    if len(first) < 8:
        raise SystemExit("Use at least 8 characters.")
    print("\nDASHBOARD_PASSWORD_HASH=" + generate_password_hash(first))
