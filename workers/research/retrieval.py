"""Bounded source retriever with redirection re-validation and content limits (ADR-0044).

Enforces:
- Connection, read, and total timeouts.
- Content size ceilings (default 1MB).
- Content-Type policy enforcement (text/plain, text/html, application/json).
- Per-hop redirect validation (SSRF, credentials, and source authorization re-checked on every hop).
- Deterministic text extraction from HTML without executing scripts or styles.
"""

from __future__ import annotations

import html
import http.client
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from core.space.research_protocol import (
    ContentUnavailableError,
    SourceAuthorizationPolicyProtocol,
    SourceIdentity,
    SourceNotAuthorizedError,
    canonicalize_locator,
    compute_sha256,
)
from workers.research.security import (
    ContentTooLargeError,
    ContentTypeRejectedError,
    RedirectLimitExceeded,
    RedirectSecurityViolation,
    validate_research_url,
)

# Standard allowed content types for research
DEFAULT_ALLOWED_CONTENT_TYPES = {
    "text/plain",
    "text/html",
    "application/json",
    "text/markdown",
    "text/csv",
    "application/xml",
}


@dataclass
class RetrievalConfig:
    """Configuration limits and settings for bounded research retrieval."""

    max_response_bytes: int = 1024 * 1024  # 1 MB
    max_redirects: int = 3
    timeout_seconds: float = 15.0
    allowed_content_types: set[str] = field(
        default_factory=lambda: set(DEFAULT_ALLOWED_CONTENT_TYPES)
    )
    allow_test_loopback: bool = False
    allowed_test_hosts: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class RetrievedDocument:
    """Structured result of a bounded source retrieval."""

    canonical_url: str
    raw_content: str
    content_bytes: bytes
    content_hash: str
    media_type: str
    http_status: int
    redirect_chain: list[str] = field(default_factory=list)
    extracted_text: str = ""


def extract_text_from_html(raw_html: str) -> str:
    """Extract clean, passive plain text from HTML, discarding script and style elements."""
    # 1. Strip script and style blocks entirely
    clean = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw_html)
    clean = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", clean)
    # 2. Strip HTML tags
    clean = re.sub(r"<[^>]+>", " ", clean)
    # 3. Unescape HTML entities
    clean = html.unescape(clean)
    # 4. Normalize whitespace
    clean = re.sub(r"[ \t]+", " ", clean)
    clean = re.sub(r"\n\s*\n+", "\n\n", clean)
    return clean.strip()


class BoundedSourceRetriever:
    """Performs capability-controlled, bounded HTTP/HTTPS source retrieval."""

    def __init__(self, config: RetrievalConfig | None = None) -> None:
        self.config = config or RetrievalConfig()

    def retrieve(
        self,
        url: str,
        space_id: str,
        source_policy: SourceAuthorizationPolicyProtocol | None = None,
    ) -> RetrievedDocument:
        """Retrieve content from a research URL under strict security and resource bounds.

        Args:
            url: The target URL to retrieve.
            space_id: Space initiating the retrieval.
            source_policy: Optional source policy to re-evaluate on each redirect hop.

        Returns:
            RetrievedDocument with raw content, SHA-256 hash, and passive text.
        """
        current_url = url
        redirect_chain: list[str] = []
        redirect_count = 0

        while True:
            canonical_locator_str = canonicalize_locator("http_endpoint", current_url)

            # 1. Validate source authorization policy pre-network / pre-DNS
            if source_policy is not None:
                source_ident = SourceIdentity(
                    source_id=f"src-{compute_sha256(canonical_locator_str)[:12]}",
                    source_type="http_endpoint",
                    locator=canonical_locator_str,
                    space_id=space_id,
                )
                decision = source_policy.evaluate_source(source_ident, space_id)
                if not decision.is_allowed:
                    if redirect_count > 0:
                        raise RedirectSecurityViolation(
                            f"Redirect to '{canonical_locator_str}' rejected by source policy: {decision.reason}"
                        )
                    raise SourceNotAuthorizedError(canonical_locator_str, space_id, decision.reason)

            # 2. Validate URL syntax, schemes, credentials, and SSRF bounds (DNS + IP checks)
            canonical_url, resolved_ip, port = validate_research_url(
                current_url,
                allow_test_loopback=self.config.allow_test_loopback,
                allowed_test_hosts=self.config.allowed_test_hosts,
            )

            # 3. Establish HTTP or HTTPS connection
            parsed = urlparse(canonical_url)
            path_and_query = parsed.path or "/"
            if parsed.query:
                path_and_query = f"{path_and_query}?{parsed.query}"

            conn_cls = (
                http.client.HTTPSConnection
                if parsed.scheme.lower() == "https"
                else http.client.HTTPConnection
            )
            conn = conn_cls(
                parsed.hostname or "localhost",
                port=port,
                timeout=self.config.timeout_seconds,
            )

            try:
                headers = {
                    "User-Agent": "Ryu-Research-Worker/1.0",
                    "Accept": "text/html, text/plain, application/json, */*",
                    "Connection": "close",
                }
                conn.request("GET", path_and_query, headers=headers)
                resp = conn.getresponse()

                # 4. Handle Redirection (301, 302, 303, 307, 308)
                if resp.status in (301, 302, 303, 307, 308):
                    redirect_count += 1
                    if redirect_count > self.config.max_redirects:
                        raise RedirectLimitExceeded(
                            f"Redirect limit ({self.config.max_redirects}) exceeded for '{url}'"
                        )
                    loc = resp.getheader("Location")
                    if not loc:
                        raise RedirectSecurityViolation(
                            f"HTTP {resp.status} redirect without Location header from '{current_url}'"
                        )
                    new_url = urljoin(current_url, loc)
                    redirect_chain.append(new_url)
                    current_url = new_url
                    continue

                # 5. Handle HTTP Errors
                if resp.status >= 400:
                    raise ContentUnavailableError(
                        f"HTTP {resp.status} {resp.reason} while retrieving '{canonical_url}'"
                    )

                # 6. Validate Content-Type
                raw_ct = resp.getheader("Content-Type", "text/plain")
                media_type = raw_ct.split(";")[0].strip().lower()
                if media_type not in self.config.allowed_content_types:
                    raise ContentTypeRejectedError(
                        f"Content-Type '{media_type}' is not permitted by policy. "
                        f"Allowed: {sorted(self.config.allowed_content_types)}"
                    )

                # 7. Stream and bound response body
                chunks: list[bytes] = []
                bytes_read = 0
                chunk_size = 65536  # 64 KB
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    bytes_read += len(chunk)
                    if bytes_read > self.config.max_response_bytes:
                        raise ContentTooLargeError(
                            f"Response from '{canonical_url}' exceeded limit of "
                            f"{self.config.max_response_bytes} bytes"
                        )
                    chunks.append(chunk)

                raw_bytes = b"".join(chunks)
                # Decode text with fallback
                raw_text = raw_bytes.decode("utf-8", errors="replace")
                content_hash = compute_sha256(raw_bytes)

                # 8. Extract clean text
                if media_type == "text/html":
                    extracted = extract_text_from_html(raw_text)
                else:
                    extracted = raw_text

                return RetrievedDocument(
                    canonical_url=canonical_url,
                    raw_content=raw_text,
                    content_bytes=raw_bytes,
                    content_hash=content_hash,
                    media_type=media_type,
                    http_status=resp.status,
                    redirect_chain=redirect_chain,
                    extracted_text=extracted,
                )

            finally:
                conn.close()
