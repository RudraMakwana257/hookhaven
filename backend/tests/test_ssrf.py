import ipaddress

import pytest

from app.core.errors import UnsafeUrlError
from app.core.ssrf import (
    DEFAULT_ALLOWED_PORTS,
    UrlPolicy,
    is_public_ip,
    system_resolver,
    validate_destination_url,
)

from .conftest import PUBLIC_IP, fake_resolver, policy_for_tests

BLOCKED_ADDRESS_URLS = [
    "http://127.0.0.1/",
    "http://localhost/",
    "http://internal.example/",
    "http://metadata.example/",
    "http://mixed.example/",  # one public + one private record: reject (rebinding trick)
    "http://10.0.0.1/",
    "http://172.16.5.4/",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",  # IPv4-mapped loopback
    "http://[::ffff:169.254.169.254]/",  # IPv4-mapped metadata
    "http://[fd00:ec2::254]/",  # AWS IPv6 metadata
    "http://[fe80::1]/",
    "http://[64:ff9b::7f00:1]/",  # NAT64 -> 127.0.0.1
    "http://[2002:7f00:1::]/",  # 6to4 -> 127.0.0.1
    "http://[::7f00:1]/",  # IPv4-compatible
    "http://0.0.0.0/",
    "http://100.64.0.1/",  # carrier-grade NAT
    "http://224.0.0.1/",  # multicast
    "http://240.0.0.1/",  # reserved
]

MALFORMED_URLS = [
    "",
    "hooks.example.com/no-scheme",
    "//hooks.example.com/",
    "ftp://hooks.example.com/",
    "file:///etc/passwd",
    "gopher://hooks.example.com/",
    "javascript:alert(1)",
    "https://",
    "https:///path",
    "https://user:pw@hooks.example.com/",
    "https://:pw@hooks.example.com/",
    "https://hooks.example.com\\@127.0.0.1/",
    "https://hooks.example.com/with space",
    "https://hooks.example.com/\r\nHost: evil",
    "https://hooks.example.com:22/",
    "https://hooks.example.com:6379/",
    "https://hooks.example.com:99999/",
    "https://hooks.example.com:abc/",
    "https://h\u00f3oks.example.com/",
    "https://" + "a" * 3000 + ".example.com/",
]


@pytest.mark.parametrize("url", BLOCKED_ADDRESS_URLS)
async def test_non_public_destinations_are_blocked(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        await validate_destination_url(url, policy_for_tests(), fake_resolver)


@pytest.mark.parametrize("url", MALFORMED_URLS)
async def test_malformed_or_disallowed_urls_are_blocked(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        await validate_destination_url(url, policy_for_tests(), fake_resolver)


async def test_unresolvable_host_is_rejected() -> None:
    with pytest.raises(UnsafeUrlError, match="resolved"):
        await validate_destination_url(
            "https://nxdomain.example/", policy_for_tests(), fake_resolver
        )


@pytest.mark.parametrize("host", ["2130706433", "0x7f.1", "127.1", "0177.0.0.1"])
async def test_obfuscated_loopback_forms_are_blocked_with_the_real_resolver(host: str) -> None:
    """The OS resolver turns these into 127.0.0.1; we judge the result, not the string."""
    with pytest.raises(UnsafeUrlError):
        await validate_destination_url(f"http://{host}/", policy_for_tests(), system_resolver)


async def test_localhost_blocked_with_the_real_resolver() -> None:
    with pytest.raises(UnsafeUrlError):
        await validate_destination_url("http://localhost/", policy_for_tests(), system_resolver)


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.com/webhooks",
        "https://hooks.example.com:8443/a?b=c",
        "http://hooks.example.com:8080/",
        "https://8.8.8.8/hook",
        "https://[2606:4700:4700::1111]/hook",
    ],
)
async def test_public_destinations_are_allowed(url: str) -> None:
    result = await validate_destination_url(url, policy_for_tests(), fake_resolver)
    assert result.addresses


async def test_validated_result_carries_pinned_addresses() -> None:
    result = await validate_destination_url(
        "https://hooks.example.com/x", policy_for_tests(), fake_resolver
    )
    assert result.addresses == (PUBLIC_IP,)
    assert (result.scheme, result.hostname, result.port) == ("https", "hooks.example.com", 443)


async def test_production_policy_requires_https() -> None:
    policy = UrlPolicy(frozenset({"https"}), DEFAULT_ALLOWED_PORTS)
    with pytest.raises(UnsafeUrlError, match="scheme"):
        await validate_destination_url("http://hooks.example.com/", policy, fake_resolver)


async def test_private_destinations_allowed_only_when_policy_says_so() -> None:
    dev = UrlPolicy(frozenset({"http", "https"}), None, allow_private=True)
    result = await validate_destination_url("http://127.0.0.1:9000/hook", dev, fake_resolver)
    assert result.addresses == ("127.0.0.1",)


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("8.8.8.8", True),
        ("1.1.1.1", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("169.254.169.254", False),
        ("::1", False),
        ("::ffff:10.0.0.1", False),
        ("::ffff:8.8.8.8", True),
    ],
)
def test_is_public_ip(address: str, expected: bool) -> None:
    assert is_public_ip(ipaddress.ip_address(address)) is expected
