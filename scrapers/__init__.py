from .generic import GenericScraper
from .twitter import TwitterScraper


def get_scraper(platform: str):
    if platform == "twitter":
        return TwitterScraper()
    return GenericScraper()
