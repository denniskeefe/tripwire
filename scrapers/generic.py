import requests
from bs4 import BeautifulSoup
from .base import BaseScraper, ScrapeResult, hash_content

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

TIMEOUT = 20  # seconds


class GenericScraper(BaseScraper):
    """
    Fetches any URL with requests, strips HTML, and hashes the visible text.
    Works well for pages that don't require JavaScript rendering.
    """

    def scrape(self, url: str) -> ScrapeResult:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
            resp.raise_for_status()
        except requests.RequestException as e:
            return ScrapeResult(success=False, error=str(e))

        soup = BeautifulSoup(resp.text, "lxml")

        # Remove noise: scripts, styles, nav, footer, ads
        for tag in soup(["script", "style", "noscript", "nav", "footer",
                          "header", "aside", "form", "meta", "link"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)

        # Collapse blank lines
        lines = [ln for ln in text.splitlines() if ln.strip()]
        clean = "\n".join(lines)

        if not clean:
            return ScrapeResult(success=False, error="No visible text content found")

        excerpt = clean[:500]
        return ScrapeResult(
            success=True,
            content_hash=hash_content(clean),
            content_excerpt=excerpt,
        )
