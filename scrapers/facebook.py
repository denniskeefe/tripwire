"""
Facebook scraper using Playwright (headless Chromium).

Facebook requires JavaScript AND login to view most content.
Without cookies the scraper will be blocked or redirected to login.

Cookie setup:
  1. Log in to facebook.com in Chrome.
  2. Export cookies to a JSON file using the "Get cookies.txt LOCALLY" extension
     (choose JSON format, not Netscape).
  3. Set FACEBOOK_COOKIES_FILE=/path/to/facebook_cookies.json in your .env file.

What is extracted (when logged in):
  - Recent post text from profile/page/group timelines
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

from config import FACEBOOK_COOKIES_FILE

PAGE_TIMEOUT   = 35_000  # ms
WAIT_AFTER_LOAD = 4_000  # ms


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
    posts = []
    selectors = [
        '[data-ad-preview="message"]',       # post body (profiles/pages)
        '[data-testid="post_message"]',       # older layout
        '.userContent',                       # legacy
        'div[dir="auto"]',                    # generic text containers
    ]
    seen = set()
    for sel in selectors:
        els = page.query_selector_all(sel)
        for el in els[:15]:
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

        cookies = _load_cookies()
        if not cookies:
            return ScrapeResult(
                success=False,
                error=(
                    "Facebook requires login to view content. "
                    "Export your Facebook cookies and set FACEBOOK_COOKIES_FILE in .env — "
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

                page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
                page.wait_for_timeout(WAIT_AFTER_LOAD)

                # Detect login wall
                if "facebook.com/login" in page.url or "facebook.com/r.php" in page.url:
                    browser.close()
                    return ScrapeResult(
                        success=False,
                        error="Facebook redirected to login — cookies may be expired. Re-export and update FACEBOOK_COOKIES_FILE.",
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
                        error="No content found on Facebook page.",
                    )

                return ScrapeResult(
                    success=True,
                    content_hash=hash_content(clean),
                    content_excerpt=clean[:800],
                )

        except PWTimeout:
            return ScrapeResult(success=False, error="Facebook page load timed out")
        except Exception as e:
            return ScrapeResult(success=False, error=str(e))
