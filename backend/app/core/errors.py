class DomainError(Exception):
    """Base class for expected, business-level failures."""


class EmailAlreadyRegisteredError(DomainError):
    pass


class InvalidCredentialsError(DomainError):
    pass


class InvalidTokenError(DomainError):
    pass


class UnsafeUrlError(DomainError):
    """A destination URL was rejected by the SSRF policy."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class EndpointLimitReachedError(DomainError):
    pass
