"""
Facebook scraper using Playwright (headless Chromium).

Public Pages may work without cookies. Private profiles and Groups require
a logged-in session via cookie injection.

Cookie setup (if needed):
  1. Log in to facebook.com in Chrome.
  2. Export cookies using "Get cookies.txt LOCALLY" extension (JSON format).
  3. Set FACEBOOK_COOKIES_FILE=/path/to/facebook_cookies.json in your .env file.
"""

import json
import os
from .base import BaseScraper, ScrapeResult, hash_content

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from config import FACEBOOK_COOKIES_FILE

PAGE_TIMEOUT    = 35_000  # ms
WAIT_AFTER_LOAD =  4_000  # ms

LOGIN_WALLS = (
    "facebook.com/login",
    "facebook.com/r.php",
    "facebook.com/checkpoint",
)


def _load_cookies() -> list:
    if not FACEBOOK_COOKIES_FILE or not os.path.exists(FACEBOOK_COOKIES_FILE):
        return []
    with open(FACEBOOK_COOKIES_FILE) as f:
        raw = json.load(f)
    cookies = []
    for c in raw:
        cookie = {
            "name":   c.get("name", ""),
            "value":  c.get("value", ""),
            "domain": c.get("domain", ".facebook.com"),
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
    """Pull recent post text from the timeline."""
    selectors = [
        '[data-ad-comet-preview="message"]',  # newer layout
        '[data-ad-preview="message"]',         # page posts
        '[data-testid="post_message"]',        # older layout
        '.userContent',                        # legacy
    ]
    seen, posts = set(), []
    for sel in selectors:
        for el in page.query_selector_all(sel)[:15]:
            try:
                text = el.inner_text().strip()
                if text and len(text) > 30 and text not in seen:
                    seen.add(text)
                    posts.append(text)
            except Exception:
                continue
        if posts:
            break
    return posts


class FacebookScraper(BaseScraper):
    """Playwright-based scraper for Facebook profiles, pages, and groups."""

    def scrape(self, url: str) -> ScrapeResult:
        if not PLAYWRIGHT_AVAILABLE:
            return ScrapeResult(
                success=False,
                error="Playwright not installed. Run: pip install playwright && playwright install chromium",
            )

        cookies = _load_cookies()  # empty list is fine — we try anyway

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
                    locale="en-US",
                )
                if cookies:
                    ctx.add_cookies(cookies)

                page = ctx.new_page()
                page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
                page.wait_for_timeout(WAIT_AFTER_LOAD)

                # Detect login wall
                if any(wall in page.url for wall in LOGIN_WALLS):
                    browser.close()
                    if cookies:
                        return ScrapeResult(
                            success=False,
                            error="Facebook redirected to login — cookies may be expired. Re-export and update FACEBOOK_COOKIES_FILE.",
                        )
                    return ScrapeResult(
                        success=False,
                        error="Facebook requires login for this content. Export your cookies and set FACEBOOK_COOKIES_FILE in .env.",
                    )

                # Also check for cookie-consent / registration interstitials
                body_text = page.inner_text("body")
                login_hints = ["log in to facebook", "create new account", "you must log in"]
                if any(h in body_text.lower() for h in login_hints) and len(body_text) < 3000:
                    browser.close()
                    return ScrapeResult(
                        success=False,
                        error="Facebook is showing a login prompt. Set FACEBOOK_COOKIES_FILE in .env to monitor this target.",
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

                # Fallback: full visible text
                lines = [l for l in body_text.splitlines() if l.strip()]
                clean = "\n".join(lines)
                browser.close()

                if not clean:
                    return ScrapeResult(success=False, error="No content found on Facebook page.")

                return ScrapeResult(
                    success=True,
                    content_hash=hash_content(clean),
                    content_excerpt=clean[:800],
                )

        except PWTimeout:
            return ScrapeResult(success=False, error="Facebook page load timed out")
        except Exception as e:
            return ScrapeResult(success=False, error=str(e))
