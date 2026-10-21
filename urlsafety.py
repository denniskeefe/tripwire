"""
URL validation for monitored targets.

Tripwire fetches whatever URL an analyst submits, from a server that usually
sits inside a private network or a cloud account. Without a check, a target
such as http://169.254.169.254/ (a cloud metadata service) or http://localhost
turns the monitor into a request proxy, and the fetched text is then stored
and shown in the dashboard. This module is the single place that decides
whether a URL may be fetched.

Two things are enforced:

  * the URL is plain http(s) with a hostname and no embedded credentials;
  * every address the hostname resolves to is publicly routable.

Setting ALLOW_PRIVATE_URLS=1 disables the address check, for monitoring pages
on an internal network on purpose.

Note: the address check resolves the name separately from the HTTP client's
own lookup, so a hostile DNS server could answer differently the second time
(DNS rebinding). The check closes the direct and redirect-based routes, not
that one.
"""

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlsplit

import config

ALLOWED_SCHEMES = ("http", "https")
MAX_URL_LENGTH = 2048


def _is_public(ip: str) -> bool:
    # Strip an IPv6 zone id ("fe80::1%en0") before parsing.
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    # An IPv4-mapped IPv6 address is judged by the IPv4 address inside it.
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return addr.is_global


def check_url(url: str, resolve: bool = True) -> Optional[str]:
    """
    Return a human-readable reason the URL must not be fetched, or None if it
    is acceptable. `resolve=False` skips the DNS lookup and only checks shape.
    """
    if not isinstance(url, str) or not url.strip():
        return "empty URL"
    if len(url) > MAX_URL_LENGTH:
        return "URL is too long"

    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        parts.port  # raises ValueError on a malformed port
    except ValueError:
        return "malformed URL"

    if parts.scheme not in ALLOWED_SCHEMES:
        return "only http:// and https:// URLs are allowed"
    if not host:
        return "URL has no hostname"
    if parts.username or parts.password:
        return "URLs with embedded credentials are not allowed"

    if not resolve or config.ALLOW_PRIVATE_URLS:
        return None

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return f"cannot resolve host '{host}'"

    addresses = {info[4][0] for info in infos}
    if not addresses:
        return f"cannot resolve host '{host}'"
    for address in addresses:
        try:
            public = _is_public(address)
        except ValueError:
            return f"host '{host}' resolved to an unparseable address"
        if not public:
            return (f"host '{host}' resolves to a non-public address ({address}); "
                    "set ALLOW_PRIVATE_URLS=1 to monitor internal pages")
    return None


def hostname_matches(url: str, domain: str) -> bool:
    """True when the URL's hostname is `domain` or one of its subdomains."""
    try:
        host = (urlsplit(url.strip()).hostname or "").lower()
    except ValueError:
        return False
    return host == domain or host.endswith("." + domain)
