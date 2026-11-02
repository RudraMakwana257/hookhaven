"""SSRF protection for user-supplied webhook destinations.

Threat: a user registers a "webhook URL" that points at our own infrastructure
(cloud metadata at 169.254.169.254, localhost admin ports, internal services) and uses
Hookhaven to make requests on their behalf.

Defences implemented here (see docs/security/ssrf.md):
  1. Strict URL syntax: scheme/port allowlist, no credentials, no control chars or backslashes.
  2. We never trust the hostname string. We *resolve* it and judge the resulting IPs, which also
     defeats decimal/hex/octal IP encodings (http://2130706433/).
  3. ALL resolved addresses must be public. One bad record rejects the URL.
  4. Special-purpose ranges are blocked, including IPv4-mapped / NAT64 / 6to4 / Teredo IPv6 forms.
  5. The check is re-run at delivery time and the connection is pinned to the validated IP
     (see safe_http.py), which closes the DNS-rebinding (time-of-check/time-of-use) gap.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.core.config import Settings
from app.core.errors import UnsafeUrlError

Resolver = Callable[[str, int], Awaitable[list[str]]]
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

MAX_URL_LENGTH = 2048
DEFAULT_PORTS = {"http": 80, "https": 443}
DEFAULT_ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
DNS_TIMEOUT_SECONDS = 5.0

# Ranges that `ipaddress.is_global` may not flag, or that embed an IPv4 address inside IPv6.
_EXTRA_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "::/96",  # deprecated IPv4-compatible
        "64:ff9b::/96",  # NAT64 (can embed 127.0.0.1 etc.)
        "64:ff9b:1::/48",  # local-use NAT64
        "2002::/16",  # 6to4
        "2001::/32",  # Teredo
    )
)


@dataclass(frozen=True)
class UrlPolicy:
    allowed_schemes: frozenset[str]
    allowed_ports: frozenset[int] | None  # None = any port
    allow_private: bool = False


@dataclass(frozen=True)
class ValidatedUrl:
    url: str
    scheme: str
    hostname: str
    port: int
    addresses: tuple[str, ...]  # every address is public (unless policy.allow_private)


def policy_from_settings(settings: Settings) -> UrlPolicy:
    return UrlPolicy(
        allowed_schemes=frozenset({"https"})
        if settings.environment == "production"
        else frozenset({"http", "https"}),
        allowed_ports=None if settings.allow_private_destinations else DEFAULT_ALLOWED_PORTS,
        allow_private=settings.allow_private_destinations,
    )


def is_public_ip(ip: IPAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 is just 127.0.0.1
    if any(ip in network for network in _EXTRA_BLOCKED_NETWORKS):
        return False
    return ip.is_global and not (
        ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_private
    )


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=DNS_TIMEOUT_SECONDS
        )
    except (socket.gaierror, TimeoutError, UnicodeError):
        raise UnsafeUrlError("hostname could not be resolved") from None
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _parse_ip(address: str) -> IPAddress:
    return ipaddress.ip_address(address.split("%", 1)[0])  # drop IPv6 zone id


async def validate_destination_url(
    url: str, policy: UrlPolicy, resolver: Resolver = system_resolver
) -> ValidatedUrl:
    """Validate a destination URL and return the addresses it is allowed to connect to."""
    if not url or len(url) > MAX_URL_LENGTH:
        raise UnsafeUrlError("URL is empty or too long")
    if any(ord(c) <= 32 or ord(c) == 127 or c == "\\" for c in url):
        raise UnsafeUrlError("URL contains whitespace, control characters or backslashes")

    try:
        parts = urlsplit(url)
        port_in_url = parts.port
    except ValueError:
        raise UnsafeUrlError("URL is malformed") from None

    scheme = parts.scheme.lower()
    if scheme not in policy.allowed_schemes:
        allowed = ", ".join(sorted(policy.allowed_schemes))
        raise UnsafeUrlError(f"scheme must be one of: {allowed}")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UnsafeUrlError("credentials in URL are not allowed")

    hostname = parts.hostname
    if not hostname:
        raise UnsafeUrlError("URL has no host")
    if not hostname.isascii():
        raise UnsafeUrlError("use the punycode form of internationalised hostnames")

    port = port_in_url or DEFAULT_PORTS[scheme]
    if policy.allowed_ports is not None and port not in policy.allowed_ports:
        raise UnsafeUrlError(f"port {port} is not allowed")

    raw_addresses = await resolver(hostname, port)
    if not raw_addresses:
        raise UnsafeUrlError("hostname could not be resolved")
    try:
        addresses = [_parse_ip(a) for a in raw_addresses]
    except ValueError:
        raise UnsafeUrlError("hostname resolved to an invalid address") from None

    if not policy.allow_private and not all(is_public_ip(ip) for ip in addresses):
        raise UnsafeUrlError("destination resolves to a non-public address")

    return ValidatedUrl(
        url=url,
        scheme=scheme,
        hostname=hostname,
        port=port,
        addresses=tuple(str(ip) for ip in addresses),
    )


class DestinationValidator:
    """Callable bundling a policy and a resolver; swapped for a fake resolver in tests."""

    def __init__(self, policy: UrlPolicy, resolver: Resolver = system_resolver) -> None:
        self.policy = policy
        self.resolver = resolver

    async def __call__(self, url: str) -> ValidatedUrl:
        return await validate_destination_url(url, self.policy, self.resolver)
