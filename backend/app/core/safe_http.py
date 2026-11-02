"""HTTP client for delivering webhooks to untrusted destinations.

Re-validates the URL at send time and connects to the *validated IP*, not the hostname, so a
DNS record that changes between registration and delivery (DNS rebinding) cannot redirect us.
TLS still verifies the certificate against the original hostname (SNI), and the Host header is
preserved.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.core.ssrf import Resolver, UrlPolicy, system_resolver, validate_destination_url


@dataclass(frozen=True)
class SafeResponse:
    status_code: int
    body: str  # truncated snippet, for the delivery log


def _format_host(address: str) -> str:
    return f"[{address}]" if ":" in address else address


async def _read_capped(response: httpx.Response, max_bytes: int) -> str:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        piece = chunk[: max_bytes - total]
        chunks.append(piece)
        total += len(piece)
        if total >= max_bytes:
            break
    return b"".join(chunks).decode("utf-8", errors="replace")


async def _post_once(
    pinned_url: str,
    *,
    hostname: str,
    host_header: str,
    content: bytes,
    headers: Mapping[str, str],
    timeout_seconds: float,
    max_response_bytes: int,
    transport: httpx.AsyncBaseTransport | None,
) -> SafeResponse:
    request_headers = {k: v for k, v in headers.items() if k.lower() != "host"}
    request_headers["Host"] = host_header
    # trust_env=False: ignore HTTP(S)_PROXY, a proxy would bypass IP pinning.
    # follow_redirects=False: a 302 to http://169.254.169.254 must never be followed.
    async with httpx.AsyncClient(
        transport=transport, timeout=timeout_seconds, follow_redirects=False, trust_env=False
    ) as client:
        request = client.build_request(
            "POST",
            pinned_url,
            content=content,
            headers=request_headers,
            extensions={"sni_hostname": hostname},
        )
        response = await client.send(request, stream=True)
        try:
            body = await _read_capped(response, max_response_bytes)
        finally:
            await response.aclose()
    return SafeResponse(status_code=response.status_code, body=body)


async def send_webhook(
    url: str,
    *,
    content: bytes,
    headers: Mapping[str, str],
    policy: UrlPolicy,
    resolver: Resolver = system_resolver,
    timeout_seconds: float = 10.0,
    max_response_bytes: int = 4096,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SafeResponse:
    """POST `content` to `url` safely.

    Raises `UnsafeUrlError` if the destination is not allowed, `httpx.HTTPError` /
    `TimeoutError` on network failure. Non-2xx responses are returned, not raised.
    """
    validated = await validate_destination_url(url, policy, resolver)
    parts = urlsplit(url)
    path = parts.path or "/"
    target = f"{path}?{parts.query}" if parts.query else path

    last_error: httpx.TransportError = httpx.ConnectError("no usable address")
    async with asyncio.timeout(
        timeout_seconds * 2
    ):  # overall deadline, httpx timeouts are per-phase
        for address in validated.addresses:
            pinned_url = f"{validated.scheme}://{_format_host(address)}:{validated.port}{target}"
            try:
                return await _post_once(
                    pinned_url,
                    hostname=validated.hostname,
                    host_header=parts.netloc,
                    content=content,
                    headers=headers,
                    timeout_seconds=timeout_seconds,
                    max_response_bytes=max_response_bytes,
                    transport=transport,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc  # try the next validated address
    raise last_error
