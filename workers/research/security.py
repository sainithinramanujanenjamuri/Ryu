"""Network security and SSRF protection for Research Worker (ADR-0044, RESEARCH-001).

Enforces:
- Scheme validation: strictly http:// and https:// (rejects file://, gopher://, etc.).
- Credential leak rejection: rejects URLs with embedded basic auth or query credentials.
- Multi-layer SSRF defenses:
  - Rejects loopback addresses (127.0.0.0/8, ::1, localhost).
  - Rejects private IPv4 networks (RFC 1918: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16).
  - Rejects link-local / APIPA addresses (169.254.0.0/16, fe80::/10).
  - Rejects cloud metadata endpoints (169.254.169.254, metadata.google.internal).
  - Rejects multicast / broadcast (224.0.0.0/4, 255.255.255.255, ff00::/8).
  - Rejects unspecified / 0.0.0.0.
- Supports explicit test fixture authorization for deterministic offline testing.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

from core.space.research_protocol import (
    ResearchError,
    canonicalize_locator,
)

# Known cloud metadata hostnames / addresses
_CLOUD_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.internal",
    "100.100.100.200",  # Alibaba Cloud metadata
}

# Regex to detect embedded credentials in URL userinfo or query string
_CREDENTIAL_USERINFO_PATTERN = re.compile(r"://[^/\s:]+:[^/@\s]+@")
_CREDENTIAL_QUERY_PATTERN = re.compile(
    r"(?i)(?:bearer|token|key|password|secret|api[_-]?key)[\s:=]+['\"]?([A-Za-z0-9_\-\.]{8,})['\"]?"
)


class NetworkSecurityError(ResearchError):
    """Base exception for all research network security violations."""


class UnsupportedSchemeError(NetworkSecurityError):
    """Raised when an unsupported URL scheme is provided."""


class CredentialBearingURLError(NetworkSecurityError):
    """Raised when a URL embeds credentials or sensitive tokens."""


class SSRFSecurityViolation(NetworkSecurityError):
    """Raised when destination IP/host targets private, loopback, or metadata services."""


class RedirectLimitExceeded(NetworkSecurityError):
    """Raised when HTTP redirect limit is exceeded."""


class RedirectSecurityViolation(NetworkSecurityError):
    """Raised when an HTTP redirect targets an unsafe, private, or unauthorized destination."""


class ContentTooLargeError(NetworkSecurityError):
    """Raised when response body exceeds maximum allowed bytes."""


class ContentTypeRejectedError(NetworkSecurityError):
    """Raised when response Content-Type is not in the allowed policy set."""


def is_ip_disallowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> tuple[bool, str]:
    """Check if an IP address belongs to a prohibited SSRF range."""
    if ip.is_loopback:
        return True, f"Loopback address rejected: {ip}"
    if ip.is_private:
        return True, f"Private network address (RFC 1918) rejected: {ip}"
    if ip.is_link_local:
        return True, f"Link-local address (APIPA) rejected: {ip}"
    if ip.is_multicast:
        return True, f"Multicast address rejected: {ip}"
    if ip.is_unspecified:
        return True, f"Unspecified address rejected: {ip}"
    if str(ip) in _CLOUD_METADATA_HOSTS:
        return True, f"Cloud metadata IP rejected: {ip}"
    # IPv4 mapped IPv6 addresses (::ffff:127.0.0.1, etc.)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return is_ip_disallowed(ip.ipv4_mapped)
    return False, ""


def validate_research_url(
    raw_url: str,
    allow_test_loopback: bool = False,
    allowed_test_hosts: set[str] | None = None,
) -> tuple[str, str, int]:
    """Validate a research URL against scheme, credential, and SSRF restrictions.

    Args:
        raw_url: The unvalidated target URL.
        allow_test_loopback: If True, permits loopback/localhost specifically for test fixtures.
        allowed_test_hosts: Optional explicit set of allowed test hostnames or IPs.

    Returns:
        tuple of (canonical_url, resolved_ip, port)

    Raises:
        UnsupportedSchemeError: If scheme is not http or https.
        CredentialBearingURLError: If credentials or tokens are embedded.
        SSRFSecurityViolation: If host resolves to a private, loopback, or metadata address.
    """
    if not raw_url or not raw_url.strip():
        raise NetworkSecurityError("Target URL must not be empty")

    clean_url = raw_url.strip()

    # 1. Scheme check
    parsed = urlparse(clean_url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise UnsupportedSchemeError(
            f"Unsupported URL scheme '{parsed.scheme}'. Only http:// and https:// are permitted."
        )

    # 2. Credential check in userinfo
    if parsed.username or parsed.password or _CREDENTIAL_USERINFO_PATTERN.search(clean_url):
        raise CredentialBearingURLError("URLs with embedded basic auth credentials are strictly rejected.")
    if _CREDENTIAL_QUERY_PATTERN.search(clean_url):
        raise CredentialBearingURLError("URLs embedding credential-like query parameters are rejected.")

    # 3. Host check
    host = parsed.hostname
    if not host:
        raise NetworkSecurityError(f"Invalid URL without hostname: '{clean_url}'")
    host_lower = host.lower()

    # Check known metadata hostnames
    if host_lower in _CLOUD_METADATA_HOSTS:
        raise SSRFSecurityViolation(f"Access to cloud metadata endpoint '{host}' is strictly forbidden.")

    port = parsed.port or (443 if scheme == "https" else 80)

    # Test mode bypass check
    allowed_hosts = allowed_test_hosts or set()
    if allow_test_loopback and (host_lower in ("localhost", "127.0.0.1", "::1") or host_lower in allowed_hosts):
        canonical = canonicalize_locator("http_endpoint", clean_url)
        return canonical, "127.0.0.1", port

    # 4. Resolve IP address and verify SSRF
    try:
        addr_info = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise NetworkSecurityError(f"Failed to resolve host '{host}': {exc}") from exc

    if not addr_info:
        raise NetworkSecurityError(f"No address records found for host '{host}'")

    # Inspect all resolved IPs
    resolved_ip_str = ""
    for entry in addr_info:
        sockaddr = entry[4]
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        disallowed, reason = is_ip_disallowed(ip_obj)
        if disallowed:
            raise SSRFSecurityViolation(
                f"SSRF violation: Host '{host}' resolves to prohibited IP '{ip_str}' ({reason})"
            )
        if not resolved_ip_str:
            resolved_ip_str = str(ip_str)

    canonical = canonicalize_locator("http_endpoint", clean_url)
    return canonical, resolved_ip_str, port
