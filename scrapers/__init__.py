from .generic import GenericScraper
from .twitter import TwitterScraper
from .linkedin import LinkedInScraper
from .facebook import FacebookScraper


def get_scraper(platform: str):
    if platform == "twitter":
        return TwitterScraper()
    if platform == "linkedin":
        return LinkedInScraper()
    if platform == "facebook":
        return FacebookScraper()
    return GenericScraper()
