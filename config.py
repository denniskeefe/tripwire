import os
from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USERNAME)

ANALYST_EMAIL = os.getenv("ANALYST_EMAIL", "")

DEFAULT_CHECK_INTERVAL = int(os.getenv("DEFAULT_CHECK_INTERVAL", "15"))

SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

TWITTER_COOKIES_FILE = os.getenv("TWITTER_COOKIES_FILE", "")

DB_PATH = os.path.join(os.path.dirname(__file__), "tripwire.db")
