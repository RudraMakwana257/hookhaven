import pytest

from app.core.signing import (
    build_signature_header,
    compute_signature,
    verify_signature,
)

SECRET = "whsec_test"
BODY = b'{"id":"1","data":{"n":1}}'
T = 1_800_000_000


def test_header_format_and_known_construction() -> None:
    header = build_signature_header(SECRET, T, BODY)
    assert header == f"t={T},v1={compute_signature(SECRET, T, BODY)}"
    assert len(compute_signature(SECRET, T, BODY)) == 64


def test_valid_signature_verifies() -> None:
    header = build_signature_header(SECRET, T, BODY)
    assert verify_signature(SECRET, header, BODY, now=T + 10)


@pytest.mark.parametrize(
    ("secret", "body"),
    [("whsec_other", BODY), (SECRET, BODY + b" "), (SECRET, b"{}")],
)
def test_wrong_secret_or_modified_body_fails(secret: str, body: bytes) -> None:
    header = build_signature_header(SECRET, T, BODY)
    assert not verify_signature(secret, header, body, now=T)


def test_replayed_old_request_is_rejected() -> None:
    header = build_signature_header(SECRET, T, BODY)
    assert verify_signature(SECRET, header, BODY, now=T + 299)
    assert not verify_signature(SECRET, header, BODY, now=T + 301)
    assert not verify_signature(SECRET, header, BODY, now=T - 301)  # from the future


def test_timestamp_cannot_be_swapped_to_dodge_the_window() -> None:
    sig = compute_signature(SECRET, T, BODY)
    forged = f"t={T + 1000},v1={sig}"  # fresh timestamp, signature for the old one
    assert not verify_signature(SECRET, forged, BODY, now=T + 1000)


def test_multiple_signatures_any_match_accepted() -> None:
    good = compute_signature(SECRET, T, BODY)
    header = f"t={T},v1={'0' * 64},v1={good}"
    assert verify_signature(SECRET, header, BODY, now=T)


@pytest.mark.parametrize("header", ["", "garbage", "t=abc,v1=ff", f"t={T}", "v1=ff", f"t={T},v1="])
def test_malformed_headers_fail(header: str) -> None:
    assert not verify_signature(SECRET, header, BODY, now=T)
