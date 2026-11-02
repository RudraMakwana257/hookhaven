import httpx
import pytest

from app.core.errors import UnsafeUrlError
from app.core.safe_http import send_webhook

from .conftest import PUBLIC_IP, fake_resolver, policy_for_tests

HEADERS = {"Content-Type": "application/json"}


async def _send(
    handler: httpx.MockTransport, url: str = "https://hooks.example.com/in", **kwargs: object
):
    return await send_webhook(
        url,
        content=b'{"a":1}',
        headers=HEADERS,
        policy=policy_for_tests(),
        resolver=fake_resolver,
        transport=handler,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_connection_is_pinned_to_validated_ip_but_keeps_host_and_sni() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    response = await _send(httpx.MockTransport(handler), "https://hooks.example.com:8443/in?x=1")

    assert (response.status_code, response.body) == (200, "ok")
    request = seen[0]
    assert request.url.host == PUBLIC_IP  # connect to the IP we validated...
    assert request.url.port == 8443
    assert request.url.query == b"x=1"
    assert request.headers["host"] == "hooks.example.com:8443"  # ...but speak as the hostname
    assert request.extensions["sni_hostname"] == "hooks.example.com"  # TLS verified vs hostname


async def test_unsafe_destination_never_reaches_the_network() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    with pytest.raises(UnsafeUrlError):
        await _send(httpx.MockTransport(handler), "https://internal.example/in")
    assert called is False


async def test_redirects_are_not_followed() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

    response = await _send(httpx.MockTransport(handler))
    assert response.status_code == 302
    assert calls == 1


async def test_response_body_is_capped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"x" * 10_000)

    response = await _send(httpx.MockTransport(handler), max_response_bytes=100)
    assert response.status_code == 500
    assert len(response.body) == 100


async def test_falls_over_to_next_validated_address_on_connect_error() -> None:
    hosts: list[str] = []

    async def resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "93.184.216.35"]

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "93.184.216.34":
            raise httpx.ConnectError("refused")
        return httpx.Response(204)

    response = await send_webhook(
        "https://hooks.example.com/in",
        content=b"{}",
        headers=HEADERS,
        policy=policy_for_tests(),
        resolver=resolver,
        transport=httpx.MockTransport(handler),
    )
    assert response.status_code == 204
    assert hosts == ["93.184.216.34", "93.184.216.35"]


async def test_non_connect_errors_propagate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(httpx.ReadTimeout):
        await _send(httpx.MockTransport(handler))


async def test_caller_cannot_override_host_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    await send_webhook(
        "https://hooks.example.com/in",
        content=b"{}",
        headers={"host": "evil.internal"},
        policy=policy_for_tests(),
        resolver=fake_resolver,
        transport=httpx.MockTransport(handler),
    )
    assert seen[0].headers["host"] == "hooks.example.com"
