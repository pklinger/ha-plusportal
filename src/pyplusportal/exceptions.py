"""Exceptions raised by :mod:`pyplusportal`."""

from __future__ import annotations


class PlusPortalError(Exception):
    """Base class for every error raised by this library."""


class AuthenticationError(PlusPortalError):
    """Credentials were rejected, or the session could not be established."""


class PortalUnavailableError(PlusPortalError):
    """The portal could not be reached, or answered with a server error."""


class ParseError(PlusPortalError):
    """The portal answered with a payload this library cannot interpret.

    Carries the offending field so a bug report can point at the exact spot
    without having to dump (and thereby leak) the whole response.
    """

    def __init__(self, message: str, *, field: str | None = None) -> None:
        """Record the message and the field that could not be interpreted."""
        super().__init__(message if field is None else f"{message} (field: {field!r})")
        self.field = field
