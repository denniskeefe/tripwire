import os
import secrets

from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USERNAME)

ANALYST_EMAIL = os.getenv("ANALYST_EMAIL", "")

DEFAULT_CHECK_INTERVAL = int(os.getenv("DEFAULT_CHECK_INTERVAL", "15"))

# A failing target stops producing alerts, and no alerts is exactly what a
# quiet target looks like — so repeated failures are mailed out rather than
# left in the dashboard. One transient network blip should not send mail, so
# the alert waits for this many consecutive failures. Set to 0 to disable.
FAILURE_ALERT_THRESHOLD = int(os.getenv("FAILURE_ALERT_THRESHOLD", "3"))

# Placeholder values shipped in .env.example. Treating them as unset keeps a
# copied-but-unedited template from silently becoming the production secret.
_PLACEHOLDERS = {
    "change-me",
    "change-me-to-a-random-string",
    "your_password_here",
}


def _configured(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    return "" if value in _PLACEHOLDERS else value


# A session secret that anyone can guess is a forgeable login cookie, so an
# unset (or still-templated) key becomes a fresh random one. Sessions then end
# at restart, which is the safe failure rather than the convenient one.
SECRET_KEY = _configured("SECRET_KEY") or secrets.token_hex(32)
SECRET_KEY_GENERATED = not _configured("SECRET_KEY")

# ── Dashboard access ───────────────────────────────────────────────────────
# The target list names the people an analyst is watching, so the dashboard is
# never served without a password. Set DASHBOARD_PASSWORD_HASH in production
# (see `python3 auth.py`); DASHBOARD_PASSWORD is the plain-text shortcut.
DASHBOARD_PASSWORD = _configured("DASHBOARD_PASSWORD")
DASHBOARD_PASSWORD_HASH = _configured("DASHBOARD_PASSWORD_HASH")

# Optional bearer token for scripted API access, where a session cookie is not
# practical. Unset means the API is reachable only from a logged-in session.
API_TOKEN = _configured("API_TOKEN")

SESSION_LIFETIME_DAYS = int(os.getenv("SESSION_LIFETIME_DAYS", "7"))

# Off by default so cookies work over plain http on localhost; render.yaml
# turns both on, where traffic is HTTPS and arrives through a proxy.
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "0").lower() not in ("0", "", "false", "no")
TRUST_PROXY = os.getenv("TRUST_PROXY", "0").lower() not in ("0", "", "false", "no")

# ── Scraper credentials ────────────────────────────────────────────────────
TWITTER_COOKIES_FILE  = os.getenv("TWITTER_COOKIES_FILE", "")
LINKEDIN_COOKIES_FILE = os.getenv("LINKEDIN_COOKIES_FILE", "")
FACEBOOK_COOKIES_FILE = os.getenv("FACEBOOK_COOKIES_FILE", "")

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "tripwire.db"))
