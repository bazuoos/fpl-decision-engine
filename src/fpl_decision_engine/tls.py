"""Portable HTTPS opening with certificate verification always enabled."""

from __future__ import annotations

import ssl
from typing import Any
from urllib.request import HTTPRedirectHandler, HTTPSHandler, build_opener, urlopen


class RejectRedirectHandler(HTTPRedirectHandler):
    """Turn every redirect into an HTTP error without following its target."""

    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def create_verified_ssl_context() -> ssl.SSLContext:
    """Use platform trust and augment it with certifi when already installed."""
    context = ssl.create_default_context()
    try:
        import certifi  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        context.load_verify_locations(cafile=certifi.where())
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("TLS certificate verification is not enabled")
    return context


def verified_urlopen(url: str, *, timeout: float) -> Any:
    """Open HTTPS using a verified, hostname-checking TLS context."""
    return urlopen(url, timeout=timeout, context=create_verified_ssl_context())


def verified_no_redirect_urlopen(url: str, *, timeout: float) -> Any:
    """Open verified HTTPS while refusing to follow HTTP redirects."""
    opener = build_opener(
        HTTPSHandler(context=create_verified_ssl_context()), RejectRedirectHandler()
    )
    return opener.open(url, timeout=timeout)


def network_error_reason(error: BaseException) -> str:
    """Return an actionable message while preserving TLS verification."""
    reason = getattr(error, "reason", error)
    if isinstance(reason, ssl.SSLCertVerificationError) or (
        "CERTIFICATE_VERIFY_FAILED" in str(reason)
    ):
        return (
            "TLS certificate verification failed. Configure the operating-system/"
            "Python CA trust store or install/configure a verified CA bundle such as "
            "certifi; certificate verification was not disabled"
        )
    return str(reason)
