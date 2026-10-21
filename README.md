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
- **Change detection** — content is normalized and hashed on every check; a unified diff over the full page is generated when something changes
- **Noise filtering** — timestamps, engagement counters, and cache tokens are neutralized before comparison, plus per-target regex ignore patterns
- **Email alerts** — HTML + plain-text email sent via SMTP the moment a change is detected, with the diff inline
- **Failure alerts** — a target that fails several checks in a row is emailed about, so a dead scraper never passes for a quiet target; a recovery notice closes the loop
- **Web dashboard** — add, pause, or delete targets; view live status and browse full alert history with a built-in diff viewer
- **Password-protected** — the dashboard and API require a login; nothing is served anonymously

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
DASHBOARD_PASSWORD=pick-something-long
```
> **Gmail users:** use an [App Password](https://myaccount.google.com/apppasswords), not your account password.

> **Dashboard password:** if you leave `DASHBOARD_PASSWORD` unset, Tripwire generates one at
> startup and prints it to the console. It changes on every restart, so set your own for
> anything longer-lived than a quick look.

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

### Noise filtering

Most pages contain text that changes on every fetch without anything actually
happening: relative timestamps, like and view counters, cache-busting tokens.
Hashing that raw text would make every check look like a change, so Tripwire
normalizes content before comparing it — volatile text is replaced with a
stable placeholder (`<time>`, `<date>`, `<count>`, `<token>`).

Built-in rule groups:

| Group | Neutralizes |
|---|---|
| `relative_time` | "2 hours ago", "just now", "· 5h" |
| `timestamp` | clock times and calendar dates |
| `engagement` | like / view / follower / comment counters |
| `tokens` | long hex or base64 strings (nonces, cache keys) |

When a page still has site-specific churn, add **ignore patterns** — per target,
either when adding it or later via the ⚙ button:

```
# one regex per line; matching text is dropped before comparison
^Trending now$
^#\w+ <count> posts$

# prefix with ! to switch off a built-in group
!engagement
```

Patterns run *after* the built-in rules, so write them against the placeholder
form shown in the dashboard's diff view (`<count>`, not `1.2K`). An invalid
regex is rejected with the offending line number rather than silently ignored.

### Alert emails
When a change is detected you'll receive an email with:
- Target label, platform, and URL
- Timestamp of detection
- Unified diff of what changed

### Failure alerts

A target that cannot be scraped produces no change alerts — and no change alerts is
exactly what a target with nothing to report looks like. Expired cookies, a page whose
markup no longer yields any text, a blocked IP or a deleted profile would otherwise end
the monitoring without ever saying so.

So consecutive failures are counted per target, and once a target reaches
`FAILURE_ALERT_THRESHOLD` (default 3) you get an email naming the error, how many checks
have failed, and when the run started. At the default 15-minute interval that is roughly
45 minutes of silence before you hear about it — long enough to ride out a transient
network blip, short enough to matter.

The alert is sent **once per outage**, not once per check; re-sending every 15 minutes
would only teach you to filter it. The next successful check clears the counter and sends
a short recovery notice, so an outage you were told about is always explicitly closed.

If the failure email itself cannot be delivered, it is not recorded as sent and is retried
on the next check.

Set `FAILURE_ALERT_THRESHOLD=0` to switch failure mail off entirely. The dashboard shows
the run length on the status badge regardless (`✗ Error 4×`), with the error, start time,
and whether it has been mailed in the tooltip.

### Dashboard actions
| Button | Action |
|---|---|
| ↻ | Run an immediate manual check |
| ⚙ | Edit ignore patterns (a dot means some are set) |
| ⏸ / ▶ | Pause or resume scheduled polling |
| ✕ | Delete target and all its history |
| View diff | Open the full diff for any past alert |

---

## Access Control

Tripwire's target list names the people you are watching and its alert history quotes what
they published, so nothing is served anonymously. Every page and every API route requires a
login — including URLs that do not exist, which redirect rather than 404, so an anonymous
visitor cannot map the API by probing.

**There is no way to run it without a password.** With none configured, Tripwire generates
one for that run and logs it:

```
WARNING  No DASHBOARD_PASSWORD set — generated one for this run only:

    K7pN2vQxRt4mZbYw8LcF3sHd
```

### Setting a password

For local use, put it in `.env`:
```env
DASHBOARD_PASSWORD=pick-something-long
```

For anything internet-facing, store a hash instead so the password itself never sits in the
environment:
```bash
.venv/bin/python3 auth.py
```
Paste the printed line into `.env` (or your host's environment) as `DASHBOARD_PASSWORD_HASH`.
A configured hash always wins over a plain-text password.

Changing the password signs out every existing session.

### Sessions

| Setting | Purpose |
|---|---|
| `SECRET_KEY` | Signs the session cookie. Unset — or left at the template's placeholder — means a random key per process, so logins end at restart. |
| `SESSION_LIFETIME_DAYS` | How long a login lasts. Default 7. |
| `SECURE_COOKIES` | Set to `1` when served over HTTPS so the cookie is never sent in the clear. |
| `TRUST_PROXY` | Set to `1` behind a reverse proxy so the real client IP is used, not the proxy's. |

Cookies are `HttpOnly` and `SameSite=Lax`, which also keeps another site from driving the
API with your session.

### Failed logins

Eight failures from one address within ten minutes locks that address out for the rest of
the window — a correct password included, since otherwise an attacker just keeps guessing.
A successful login clears the counter. Every attempt, successful or not, is logged with its
source address.

Behind a proxy, set `TRUST_PROXY=1`. Without it every request appears to come from the proxy
and the throttle becomes one shared bucket for the whole internet.

### Scripted access

Session cookies are awkward for scripts, so set `API_TOKEN` and send it as a bearer token:

```bash
curl -H "Authorization: Bearer $API_TOKEN" http://localhost:5500/api/targets
```

Leave `API_TOKEN` unset and token authentication is off entirely — the API is then reachable
only from a logged-in session.

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
├── auth.py             # Login, session guard, brute-force throttle
├── monitor.py          # APScheduler background polling engine
├── notifier.py         # SMTP email alert (HTML + plain text)
├── database.py         # SQLite — targets, snapshots, alerts
├── config.py           # Reads .env settings
├── scrapers/
│   ├── base.py         # ScrapeResult dataclass + hash/normalize helper
│   ├── normalize.py    # Noise rules + ignore-pattern parsing
│   ├── generic.py      # requests + BeautifulSoup scraper
│   ├── twitter.py      # Playwright headless Chromium scraper
│   ├── linkedin.py     # Playwright scraper (activity feed)
│   └── facebook.py     # Playwright scraper (timeline)
├── static/
│   ├── style.css       # Dashboard styles
│   └── app.js          # Dashboard logic (vanilla JS)
├── templates/
│   ├── index.html      # Single-page web UI
│   └── login.html      # Login page
├── tests/              # python3 -m unittest discover tests
├── .env.example        # Configuration template
└── requirements.txt
```

---

## REST API

The dashboard talks to a simple JSON API you can also hit directly. Every endpoint requires
authentication — send `Authorization: Bearer $API_TOKEN` (see [Access Control](#access-control)),
or an unauthenticated call gets `401 {"error": "Authentication required"}`.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/targets` | List all targets |
| `POST` | `/api/targets` | Add targets (see below) |
| `DELETE` | `/api/targets/<id>` | Remove a target |
| `POST` | `/api/targets/<id>/toggle` | Pause / resume |
| `POST` | `/api/targets/<id>/check` | Trigger immediate check |
| `PUT` | `/api/targets/<id>/patterns` | Replace a target's ignore patterns |
| `GET` | `/api/noise-rules` | List built-in noise rule group names |
| `GET` | `/api/alerts` | List recent alerts (`?limit=N`) |

**Add targets via curl:**
```bash
curl -X POST http://localhost:5500/api/targets \
  -H 'Content-Type: application/json' \
  -d '{
    "urls_raw": "https://x.com/handle\nhttps://reddit.com/user/handle",
    "label_prefix": "Subject A",
    "check_interval": 10,
    "ignore_patterns": "^Trending now$\n!engagement"
  }'
```

**Update ignore patterns:**
```bash
curl -X PUT http://localhost:5500/api/targets/1/patterns \
  -H 'Content-Type: application/json' \
  -d '{"ignore_patterns": "^Trending now$"}'
```

An invalid spec returns `400` with a `details` array naming each bad line:
```json
{"error": "Invalid ignore patterns",
 "details": ["line 2: invalid regex — unterminated character set at position 0"]}
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
| `FAILURE_ALERT_THRESHOLD` | `3` | Consecutive failed checks before a target is reported as failing; `0` disables |
| `PORT` | `5500` | Port for `python app.py`; hosts such as Render set it themselves |
| `MAX_CONCURRENT_CHECKS` | `3` | Checks running at once; each browser-based check starts a full Chromium |
| `CHECK_JITTER_SECONDS` | `30` | Random delay added to each target's schedule |
| `ALLOW_PRIVATE_URLS` | `0` | Targets must resolve to public addresses; set `1` to monitor internal pages |
| `SECRET_KEY` | random per run | Flask session secret — set it to keep logins across restarts |
| `DASHBOARD_PASSWORD` | generated per run | Dashboard login password |
| `DASHBOARD_PASSWORD_HASH` | — | Password hash from `.venv/bin/python3 auth.py`; preferred over the above |
| `API_TOKEN` | — | Bearer token for scripted API access; unset disables token auth |
| `SESSION_LIFETIME_DAYS` | `7` | How long a login lasts |
| `SECURE_COOKIES` | `0` | Set to `1` when served over HTTPS |
| `TRUST_PROXY` | `0` | Set to `1` behind a reverse proxy |
| `TWITTER_COOKIES_FILE` | — | Path to Twitter/X cookies JSON |

---

## Requirements

- Python 3.10+
- Chromium (installed automatically via `playwright install chromium`)
- An SMTP account (Gmail, Outlook, or any mail server)

---

## License

MIT
