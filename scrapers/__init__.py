from .generic import GenericScraper
from .twitter import TwitterScraper
from .linkedin import LinkedInScraper


def get_scraper(platform: str):
    if platform == "twitter":
        return TwitterScraper()
    if platform == "linkedin":
        return LinkedInScraper()
    return GenericScraper()
