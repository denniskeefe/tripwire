import re
from urllib.parse import urlsplit

from urlsafety import hostname_matches
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


def detect_platform(url: str) -> str:
    """
    Pick the scraper for a URL from its hostname and path.

    The hostname is parsed rather than pattern-matched against the whole URL:
    otherwise https://evil.example/?next=x.com/a would be treated as a Twitter
    profile and handed to the browser scraper that carries the analyst's
    session cookies.
    """
    try:
        path = urlsplit(url.strip()).path
    except ValueError:
        return "generic"
    if (hostname_matches(url, "twitter.com") or hostname_matches(url, "x.com")) \
            and re.match(r"/\w", path):
        return "twitter"
    if hostname_matches(url, "linkedin.com") and re.match(r"/(in|company)/", path):
        return "linkedin"
    if hostname_matches(url, "facebook.com"):
        return "facebook"
    return "generic"
