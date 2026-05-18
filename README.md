# ⚡ Tripwire — OSINT Social Media Monitor

Monitor social media profiles for new activity and receive instant email alerts when something changes. Built for OSINT analysts who need to track multiple targets simultaneously.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Flask](https://img.shields.io/badge/flask-3.0-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

- **Bulk URL submission** — paste dozens of profile URLs at once
- **Multi-platform** — dedicated Twitter/X scraper (Playwright) + generic scraper for any other URL
- **Scheduled polling** — each target checks on its own configurable interval (minimum 5 minutes)
- **Change detection** — content is hashed on every check; a unified diff is generated when something changes
- **Email alerts** — HTML + plain-text email sent via SMTP the moment a change is detected, with the diff inline
- **Web dashboard** — add, pause, or delete targets; view live status and browse full alert history with a built-in diff viewer

---

## Screenshots

### Dashboard
```
┌──────────────────────────────────────────────────────────────┐
│ ⚡ Tripwire  OSINT Monitor          12 active   3 alerts today │
├──────────────────────────────────────────────────────────────┤
│  Add Targets                                                  │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ https://x.com/target_handle                            │  │
│  │ https://reddit.com/user/target_user                    │  │
│  └────────────────────────────────────────────────────────┘  │
│  Label prefix: [Subject A    ]  Every: [15] min  [+ Add]     │
├──────────────────────────────────────────────────────────────┤
│  Monitored Targets                    [Filter targets…]       │
│  Label         URL              Platform  Int   Last Checked  │
│  Subject A #1  x.com/target…   𝕏 Twitter  15m   2m ago  ✓OK │
│  Subject A #2  reddit.com/…    🌐 Generic  15m   2m ago  ★ ! │
└──────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Clone
```bash
git clone https://github.com/denniskeefe/tripwire.git
cd tripwire
```

### 2. Configure
```bash
cp .env.example .env
```
Open `.env` and fill in your SMTP credentials and analyst email:
```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=your_app_password
ANALYST_EMAIL=analyst@yourorg.com
DEFAULT_CHECK_INTERVAL=15
```
> **Gmail users:** use an [App Password](https://myaccount.google.com/apppasswords), not your account password.

### 3. Install
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

### 4. Run
```bash
.venv/bin/python3 app.py
```
Open **http://localhost:5500** in your browser.

---

## Usage

### Adding targets
Paste one or more profile URLs into the **Add Targets** box — one per line. Optionally set a label prefix (e.g. `Subject A`) and a check interval.

```
https://x.com/username
https://reddit.com/user/username
https://somesite.com/profile/123
```

Platform is detected automatically:
| URL pattern | Scraper used |
|---|---|
| `x.com/*` or `twitter.com/*` | Playwright (headless Chromium) |
| Everything else | requests + BeautifulSoup |

### Alert emails
When a change is detected you'll receive an email with:
- Target label, platform, and URL
- Timestamp of detection
- Unified diff of what changed

### Dashboard actions
| Button | Action |
|---|---|
| ↻ | Run an immediate manual check |
| ⏸ / ▶ | Pause or resume scheduled polling |
| ✕ | Delete target and all its history |
| View diff | Open the full diff for any past alert |

---

## Twitter/X — Authenticated Scraping

Twitter now requires login to view most profile content. Without cookies Tripwire will still detect page-level changes, but tweet text may not be accessible.

**To enable authenticated scraping:**

1. Log in to [x.com](https://x.com) in Chrome.
2. Install the [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) extension.
3. Click the extension → **Export as JSON**.
4. Save the file somewhere safe (e.g. `~/.tripwire/twitter_cookies.json`).
5. Add to your `.env`:
   ```env
   TWITTER_COOKIES_FILE=/Users/you/.tripwire/twitter_cookies.json
   ```
6. Restart Tripwire.

> Keep the cookies file outside the repo directory. It is already covered by `.gitignore` but treat it like a password.

---

## Project Structure

```
tripwire/
├── app.py              # Flask application + REST API
├── monitor.py          # APScheduler background polling engine
├── notifier.py         # SMTP email alert (HTML + plain text)
├── database.py         # SQLite — targets, snapshots, alerts
├── config.py           # Reads .env settings
├── scrapers/
│   ├── base.py         # ScrapeResult dataclass + hash helper
│   ├── generic.py      # requests + BeautifulSoup scraper
│   └── twitter.py      # Playwright headless Chromium scraper
├── static/
│   ├── style.css       # Dashboard styles
│   └── app.js          # Dashboard logic (vanilla JS)
├── templates/
│   └── index.html      # Single-page web UI
├── .env.example        # Configuration template
└── requirements.txt
```

---

## REST API

The dashboard talks to a simple JSON API you can also hit directly:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/targets` | List all targets |
| `POST` | `/api/targets` | Add targets (see below) |
| `DELETE` | `/api/targets/<id>` | Remove a target |
| `POST` | `/api/targets/<id>/toggle` | Pause / resume |
| `POST` | `/api/targets/<id>/check` | Trigger immediate check |
| `GET` | `/api/alerts` | List recent alerts (`?limit=N`) |

**Add targets via curl:**
```bash
curl -X POST http://localhost:5500/api/targets \
  -H 'Content-Type: application/json' \
  -d '{
    "urls_raw": "https://x.com/handle\nhttps://reddit.com/user/handle",
    "label_prefix": "Subject A",
    "check_interval": 10
  }'
```

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP port (STARTTLS) |
| `SMTP_USERNAME` | — | SMTP login username |
| `SMTP_PASSWORD` | — | SMTP login password / app password |
| `SMTP_FROM` | same as username | From address shown in alerts |
| `ANALYST_EMAIL` | — | Recipient(s) — comma-separated for multiple |
| `DEFAULT_CHECK_INTERVAL` | `15` | Default polling interval in minutes |
| `SECRET_KEY` | `change-me` | Flask session secret — change in production |
| `TWITTER_COOKIES_FILE` | — | Path to Twitter/X cookies JSON |

---

## Requirements

- Python 3.10+
- Chromium (installed automatically via `playwright install chromium`)
- An SMTP account (Gmail, Outlook, or any mail server)

---

## License

MIT
