import hashlib
from dataclasses import dataclass
from typing import Optional

from .normalize import normalize

# Full normalized text is stored so diffs cover the whole page rather than a
# truncated prefix. The cap is a safety valve against pathological pages.
MAX_CONTENT = 200_000
EXCERPT_LEN = 800


@dataclass
class ScrapeResult:
    """Returned by every scraper."""
    success: bool
    content_hash: str = ""
    content: str = ""              # Full normalized text — what diffs are built from
    content_excerpt: str = ""      # Short human-readable snippet for list views
    error: Optional[str] = None


def hash_content(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def build_result(raw_text: str, ignore_spec: str = "",
                 empty_error: str = "No visible text content found") -> ScrapeResult:
    """
    Normalize scraped text and package it for storage.

    Both the hash and the stored content come from the normalized text, so a
    change is only recorded when the substance of the page changes rather than
    when a counter ticks or a timestamp ages.
    """
    clean = normalize(raw_text, ignore_spec)
    if not clean:
        return ScrapeResult(success=False, error=empty_error)

    clean = clean[:MAX_CONTENT]
    return ScrapeResult(
        success=True,
        content_hash=hash_content(clean),
        content=clean,
        content_excerpt=clean[:EXCERPT_LEN],
    )


class BaseScraper:
    def scrape(self, url: str, ignore_spec: str = "") -> ScrapeResult:
        raise NotImplementedError
