import hashlib
from dataclasses import dataclass
from typing import Optional


@dataclass
class ScrapeResult:
    """Returned by every scraper."""
    success: bool
    content_hash: str = ""
    content_excerpt: str = ""      # Short human-readable snippet for alerts
    error: Optional[str] = None


def hash_content(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


class BaseScraper:
    def scrape(self, url: str) -> ScrapeResult:
        raise NotImplementedError
