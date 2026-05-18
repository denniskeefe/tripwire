"""
Twitter/X scraper using Playwright (headless Chromium).

Twitter requires JavaScript to render content and increasingly requires a login
to view profiles. This scraper supports optional cookie injection so analysts
can authenticate with their own account.

Cookie setup:
  1. Log in to x.com in Chrome.
  2. Export cookies to a JSON file using the "Get cookies.txt LOCALLY" extension
     (choose JSON format, not Netscape).
  3. Set TWITTER_COOKIES_FILE=/path/to/cookies.json in your .env file.
"""

import json
import os
import time
from .base import BaseScraper, ScrapeResult, hash_content

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from config import TWITTER_COOKIES_FILE

PAGE_TIMEOUT = 30_000   # ms
WAIT_AFTER_LOAD = 3_000 # ms — let dynamic content settle


def _load_cookies() -> list:
    if not TWITTER_COOKIES_FILE or not os.path.exists(TWITTER_COOKIES_FILE):
        return []
    with open(TWITTER_COOKIES_FILE) as f:
        raw = json.load(f)
    # Normalise various export formats into Playwright's cookie schema
    cookies = []
    for c in raw:
        cookie = {
            "name":   c.get("name", ""),
            "value":  c.get("value", ""),
            "domain": c.get("domain", ".x.com"),
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
            if ss not in ("Strict", "Lax", "None"):
                ss = "None"
            cookie["sameSite"] = ss
        cookies.append(cookie)
    return cookies


def _extract_tweets(page) -> list[str]:
    """Pull tweet text blobs from the rendered DOM."""
    articles = page.query_selector_all('article[data-testid="tweet"]')
    tweets = []
    for article in articles[:10]:   # cap at 10 most-recent
        try:
            text_el = article.query_selector('[data-testid="tweetText"]')
            time_el = article.query_selector("time")
            tweet_text = text_el.inner_text() if text_el else ""
            tweet_time = time_el.get_attribute("datetime") if time_el else ""
            if tweet_text:
                tweets.append(f"[{tweet_time}] {tweet_text}")
        except Exception:
            continue
    return tweets


class TwitterScraper(BaseScraper):
    """Playwright-based scraper for Twitter/X profile pages."""

    def scrape(self, url: str) -> ScrapeResult:
        if not PLAYWRIGHT_AVAILABLE:
            return ScrapeResult(
                success=False,
                error="Playwright is not installed. Run: pip install playwright && playwright install chromium",
            )

        # Normalise old twitter.com links → x.com
        url = url.replace("twitter.com/", "x.com/", 1)

        cookies = _load_cookies()

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

                if cookies:
                    ctx.add_cookies(cookies)

                page = ctx.new_page()
                page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
                page.wait_for_timeout(WAIT_AFTER_LOAD)

                tweets = _extract_tweets(page)

                if not tweets:
                    # Fallback: hash all visible text (catches login walls, etc.)
                    body_text = page.inner_text("body")
                    lines = [l for l in body_text.splitlines() if l.strip()]
                    clean = "\n".join(lines)
                    if not clean:
                        browser.close()
                        return ScrapeResult(
                            success=False,
                            error="No content found — Twitter may require login. See TWITTER_COOKIES_FILE in .env",
                        )
                    browser.close()
                    return ScrapeResult(
                        success=True,
                        content_hash=hash_content(clean),
                        content_excerpt=clean[:500],
                    )

                combined = "\n".join(tweets)
                browser.close()
                return ScrapeResult(
                    success=True,
                    content_hash=hash_content(combined),
                    content_excerpt=combined[:800],
                )

        except PWTimeout:
            return ScrapeResult(success=False, error="Page load timed out")
        except Exception as e:
            return ScrapeResult(success=False, error=str(e))
