from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import urlsafety
from .base import BaseScraper, ScrapeResult, build_result

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

TIMEOUT = 20  # seconds
MAX_REDIRECTS = 5
MAX_BODY_BYTES = 5 * 1024 * 1024


class _FetchError(Exception):
    pass


def _fetch(url: str) -> str:
    """
    GET a page, validating every hop. Redirects are followed by hand because
    an automatic redirect from a public host to http://169.254.169.254/ would
    otherwise slip past the check made on the original URL.
    """
    for _ in range(MAX_REDIRECTS + 1):
        problem = urlsafety.check_url(url)
        if problem:
            raise _FetchError(f"Refusing to fetch {problem}")

        with requests.get(url, headers=HEADERS, timeout=TIMEOUT,
                          allow_redirects=False, stream=True) as resp:
            if resp.is_redirect:
                location = resp.headers.get("Location")
                if not location:
                    raise _FetchError("Redirect without a Location header")
                url = urljoin(url, location)
                continue

            resp.raise_for_status()

            body = bytearray()
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    raise _FetchError(
                        f"Response larger than {MAX_BODY_BYTES // (1024 * 1024)} MB")
            encoding = resp.encoding or resp.apparent_encoding or "utf-8"
            return bytes(body).decode(encoding, errors="replace")

    raise _FetchError(f"Too many redirects (more than {MAX_REDIRECTS})")


class GenericScraper(BaseScraper):
    """
    Fetches any URL with requests, strips HTML, and hashes the visible text.
    Works well for pages that don't require JavaScript rendering.
    """

    def scrape(self, url: str, ignore_spec: str = "") -> ScrapeResult:
        try:
            html = _fetch(url)
        except (_FetchError, requests.RequestException) as e:
            return ScrapeResult(success=False, error=str(e))

        soup = BeautifulSoup(html, "lxml")

        # Remove noise: scripts, styles, nav, footer, ads
        for tag in soup(["script", "style", "noscript", "nav", "footer",
                          "header", "aside", "form", "meta", "link"]):
            tag.decompose()

        return build_result(soup.get_text(separator="\n", strip=True), ignore_spec)
