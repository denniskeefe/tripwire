"""
LinkedIn scraper using Playwright (headless Chromium).

LinkedIn requires login to view profile content. Without cookies the scraper
will attempt the page but will almost always be redirected to an auth wall.

Cookie setup:
  1. Log in to linkedin.com in Chrome.
  2. Export cookies using "Get cookies.txt LOCALLY" extension (JSON format).
  3. Set LINKEDIN_COOKIES_FILE=/path/to/linkedin_cookies.json in your .env file.
"""

import json
import os
from .base import BaseScraper, ScrapeResult, build_result

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from config import LINKEDIN_COOKIES_FILE

PAGE_TIMEOUT    = 35_000  # ms — LinkedIn is slow
WAIT_AFTER_LOAD =  4_000  # ms — let feed render

LOGIN_WALLS = (
    "linkedin.com/login",
    "linkedin.com/authwall",
    "linkedin.com/uas/login",
    "linkedin.com/signup",
)


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
    selectors = [
        ".feed-shared-update-v2__description",
        ".update-components-text",
        "span[dir='ltr']",
    ]
    posts = []
    for sel in selectors:
        for el in page.query_selector_all(sel)[:10]:
            try:
                text = el.inner_text().strip()
                if text and len(text) > 20:
                    posts.append(text)
            except Exception:
                continue
        if posts:
            break
    return posts


def _navigate_to_activity(page, profile_url: str):
    base = profile_url.rstrip("/")
    activity_url = base if "/recent-activity" in base else base + "/recent-activity/all/"
    try:
        page.goto(activity_url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
        page.wait_for_timeout(WAIT_AFTER_LOAD)
    except Exception:
        pass


class LinkedInScraper(BaseScraper):
    """Playwright-based scraper for LinkedIn profile pages."""

    def scrape(self, url: str, ignore_spec: str = "") -> ScrapeResult:
        if not PLAYWRIGHT_AVAILABLE:
            return ScrapeResult(
                success=False,
                error="Playwright not installed. Run: pip install playwright && playwright install chromium",
            )

        cookies = _load_cookies()  # empty list is fine — we try anyway

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    ctx = browser.new_context(
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                        viewport={"width": 1280, "height": 900},
                        locale="en-US",
                    )
                    if cookies:
                        ctx.add_cookies(cookies)

                    page = ctx.new_page()
                    _navigate_to_activity(page, url)

                    # Detect login wall
                    if any(wall in page.url for wall in LOGIN_WALLS):
                        if cookies:
                            return ScrapeResult(
                                success=False,
                                error="LinkedIn redirected to login — cookies may be expired. Re-export and update LINKEDIN_COOKIES_FILE.",
                            )
                        return ScrapeResult(
                            success=False,
                            error="LinkedIn requires login to view profiles. Export your cookies and set LINKEDIN_COOKIES_FILE in .env.",
                        )

                    posts = _extract_posts(page)
                    if posts:
                        return build_result("\n---\n".join(posts), ignore_spec)

                    # Fallback: full visible text
                    return build_result(
                        page.inner_text("body"),
                        ignore_spec,
                        empty_error="No content found on LinkedIn page.",
                    )
                finally:
                    browser.close()

        except PWTimeout:
            return ScrapeResult(success=False, error="LinkedIn page load timed out")
        except Exception as e:
            return ScrapeResult(success=False, error=str(e))
