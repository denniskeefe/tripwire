"""
LinkedIn scraper using Playwright (headless Chromium).

LinkedIn requires JavaScript AND login to view profile content.
Without cookies the scraper will only see the login wall.

Cookie setup:
  1. Log in to linkedin.com in Chrome.
  2. Export cookies to a JSON file using the "Get cookies.txt LOCALLY" extension
     (choose JSON format, not Netscape).
  3. Set LINKEDIN_COOKIES_FILE=/path/to/linkedin_cookies.json in your .env file.

What is extracted (when logged in):
  - Recent post text and timestamps from the Activity feed
  - Falls back to full visible page text if posts can't be isolated
"""

import json
import os
from .base import BaseScraper, ScrapeResult, hash_content

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from config import LINKEDIN_COOKIES_FILE

PAGE_TIMEOUT  = 35_000   # ms — LinkedIn is slow
WAIT_AFTER_LOAD = 4_000  # ms — let feed render


def _load_cookies() -> list:
    if not LINKEDIN_COOKIES_FILE or not os.path.exists(LINKEDIN_COOKIES_FILE):
        return []
    with open(LINKEDIN_COOKIES_FILE) as f:
        raw = json.load(f)
    cookies = []
    for c in raw:
        cookie = {
            "name":   c.get("name", ""),
            "value":  c.get("value", ""),
            "domain": c.get("domain", ".linkedin.com"),
            "path":   c.get("path", "/"),
        }
        if "expires" in c and c["expires"] not in (-1, None):
            cookie["expires"] = int(c["expires"])
        if "httpOnly" in c:
            cookie["httpOnly"] = bool(c["httpOnly"])
        if "secure" in c:
            cookie["secure"] = bool(c["secure"])
        if "sameSite" in c:
            ss = c["sameSite"]
            cookie["sameSite"] = ss if ss in ("Strict", "Lax", "None") else "None"
        cookies.append(cookie)
    return cookies


def _extract_posts(page) -> list[str]:
    """Pull recent post text from the profile's Activity feed."""
    posts = []

    # Try the activity feed URL variant first (most reliable for posts)
    selectors = [
        ".feed-shared-update-v2__description",   # post body
        ".update-components-text",               # newer layout
        "span[dir='ltr']",                       # fallback text spans
    ]

    for sel in selectors:
        els = page.query_selector_all(sel)
        if els:
            for el in els[:10]:
                try:
                    text = el.inner_text().strip()
                    if text and len(text) > 20:
                        posts.append(text)
                except Exception:
                    continue
        if posts:
            break

    return posts


def _navigate_to_activity(page, profile_url: str) -> bool:
    """Try navigating to the profile's recent activity page."""
    base = profile_url.rstrip("/")
    if "/recent-activity" not in base:
        activity_url = base + "/recent-activity/all/"
    else:
        activity_url = base
    try:
        page.goto(activity_url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_timeout(WAIT_AFTER_LOAD)
        return True
    except Exception:
        return False


class LinkedInScraper(BaseScraper):
    """Playwright-based scraper for LinkedIn profile pages."""

    def scrape(self, url: str) -> ScrapeResult:
        if not PLAYWRIGHT_AVAILABLE:
            return ScrapeResult(
                success=False,
                error="Playwright not installed. Run: pip install playwright && playwright install chromium",
            )

        cookies = _load_cookies()
        if not cookies:
            return ScrapeResult(
                success=False,
                error=(
                    "LinkedIn requires login to view profiles. "
                    "Export your LinkedIn cookies and set LINKEDIN_COOKIES_FILE in .env — "
                    "see the README for instructions."
                ),
            )

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 900},
                )
                ctx.add_cookies(cookies)
                page = ctx.new_page()

                # Navigate to the activity feed for this profile
                _navigate_to_activity(page, url)

                # Check if we got redirected to login wall
                if "linkedin.com/login" in page.url or "linkedin.com/authwall" in page.url:
                    browser.close()
                    return ScrapeResult(
                        success=False,
                        error="LinkedIn redirected to login — cookies may be expired. Re-export and update LINKEDIN_COOKIES_FILE.",
                    )

                posts = _extract_posts(page)

                if posts:
                    combined = "\n---\n".join(posts)
                    browser.close()
                    return ScrapeResult(
                        success=True,
                        content_hash=hash_content(combined),
                        content_excerpt=combined[:800],
                    )

                # Fallback: hash all visible body text
                body_text = page.inner_text("body")
                lines = [l for l in body_text.splitlines() if l.strip()]
                clean = "\n".join(lines)

                browser.close()

                if not clean:
                    return ScrapeResult(
                        success=False,
                        error="No content found on LinkedIn profile page.",
                    )

                return ScrapeResult(
                    success=True,
                    content_hash=hash_content(clean),
                    content_excerpt=clean[:800],
                )

        except PWTimeout:
            return ScrapeResult(success=False, error="LinkedIn page load timed out")
        except Exception as e:
            return ScrapeResult(success=False, error=str(e))
