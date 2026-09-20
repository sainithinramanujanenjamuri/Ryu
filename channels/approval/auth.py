"""Approver Authentication: token-hmac-v1 wire protocol implementation.

Authoritative protocol specification for human approver authentication in Phase 8.
Enforces constant-time HMAC-SHA256 verification, clock-skew bounding, nonce replay protection,
and credential revocation checks (ADR-0023).

spec §4, §16, ROADMAP Phase 8, CONTRACT_MATRIX HUMAN-001, CLI-002 — Phase 8
"""

from __future__ import annotations

import hashlib
import hmac
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

NONCE_HEX_REGEX = re.compile(r"^[0-9a-f]{32}$")
HASH_HEX_REGEX = re.compile(r"^[0-9a-f]{64}$")
SIG_HEX_REGEX = re.compile(r"^[0-9a-f]{64}$")
UUID_REGEX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")


class AuthenticationError(Exception):
    """Base error for all approver authentication failures."""


class MalformedAuthenticationPayloadError(AuthenticationError):
    """Raised when authentication payload fields are missing, unexpected, or malformed."""


class ClockSkewError(AuthenticationError):
    """Raised when authentication timestamp exceeds the allowed +/- 60s clock skew window."""


class UnknownApproverError(AuthenticationError):
    """Raised when approver identity is not registered in credential store."""


class TokenRevokedError(AuthenticationError):
    """Raised when approver credential has been revoked."""


class TokenExpiredError(AuthenticationError):
    """Raised when approver credential has passed its expiration deadline."""


class ReplayDetectedError(AuthenticationError):
    """Raised when an authentication nonce has already been consumed."""


class AuthenticationServiceUnavailableError(AuthenticationError):
    """Raised when secret key material cannot be resolved (fail-closed)."""


class InvalidSignatureError(AuthenticationError):
    """Raised when constant-time HMAC signature verification fails."""


@dataclass(frozen=True)
class ApproverCredentialRecord:
    """Represents registered credential metadata in PostgreSQL (ZERO raw secrets)."""

    approver_id: str
    token_id: str
    secret_ref: str
    created_at: datetime
    expires_at: datetime
    is_revoked: bool = False
    revoked_at: datetime | None = None
    revocation_reason: str | None = None
    version: int = 1


@dataclass(frozen=True)
class ApproverDecisionSubmission:
    """Wire protocol payload submitted by CLI client for an approval decision."""

    approver_id: str
    timestamp: int
    nonce: str
    space_id: str
    approval_id: str
    decision: str  # Strictly "APPROVE" or "REJECT"
    plan_version: int
    capability_request_hash: str
    signature: str
    protocol: str = "token-hmac-v1"


def compute_token_hmac_v1_signature(
    secret_key_bytes: bytes,
    approver_id: str,
    timestamp: int,
    nonce: str,
    space_id: str,
    approval_id: str,
    decision: str,
    plan_version: int,
    capability_request_hash: str,
) -> str:
    """
    Compute canonical HMAC-SHA256 signature for token-hmac-v1 wire protocol.

    Pre-image specification: exactly 9 lines delimited by single newline:
    token-hmac-v1\\n
    <approver_id>\\n
    <timestamp>\\n
    <nonce>\\n
    <space_id>\\n
    <approval_id>\\n
    <decision>\\n
    <plan_version>\\n
    <capability_request_hash>
    """
    if decision not in ("APPROVE", "REJECT"):
        raise MalformedAuthenticationPayloadError(
            f"Decision must be 'APPROVE' or 'REJECT', got '{decision}'"
        )

    for field_name, val in (
        ("approver_id", approver_id),
        ("nonce", nonce),
        ("space_id", space_id),
        ("approval_id", approval_id),
        ("decision", decision),
        ("capability_request_hash", capability_request_hash),
    ):
        if any(c in val for c in ("\n", "\r", "\t", "\x00")):
            raise MalformedAuthenticationPayloadError(
                f"Field '{field_name}' contains illegal newline or control characters"
            )

    pre_image = (
        f"token-hmac-v1\n"
        f"{approver_id}\n"
        f"{timestamp}\n"
        f"{nonce.lower()}\n"
        f"{space_id}\n"
        f"{approval_id.lower()}\n"
        f"{decision}\n"
        f"{plan_version}\n"
        f"{capability_request_hash.lower()}"
    )

    return hmac.new(
        key=secret_key_bytes,
        msg=pre_image.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest().lower()


class NonceStore(Protocol):
    """Protocol for recording and checking consumed nonces."""

    def consume_nonce(self, nonce: str, approver_id: str, timestamp: float) -> bool:
        """Atomically record nonce. Returns False if already exists (replay)."""
        ...


class CredentialStore(Protocol):
    """Protocol for querying registered approver credentials."""

    def get_credential(self, approver_id: str) -> ApproverCredentialRecord | None: ...


class InMemoryCredentialStore:
    """Thread-safe in-memory store for approver credentials and nonces (testing)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._credentials: dict[str, ApproverCredentialRecord] = {}
        self._nonces: set[str] = set()

    def register_credential(self, cred: ApproverCredentialRecord) -> None:
        with self._lock:
            self._credentials[cred.approver_id] = cred

    def get_credential(self, approver_id: str) -> ApproverCredentialRecord | None:
        with self._lock:
            return self._credentials.get(approver_id)

    def revoke_credential(self, approver_id: str, reason: str = "") -> bool:
        with self._lock:
            cred = self._credentials.get(approver_id)
            if cred is None:
                return False
            revoked = ApproverCredentialRecord(
                approver_id=cred.approver_id,
                token_id=cred.token_id,
                secret_ref=cred.secret_ref,
                created_at=cred.created_at,
                expires_at=cred.expires_at,
                is_revoked=True,
                revoked_at=datetime.now(timezone.utc),
                revocation_reason=reason,
                version=cred.version,
            )
            self._credentials[approver_id] = revoked
            return True

    def consume_nonce(self, nonce: str, approver_id: str, timestamp: float) -> bool:
        with self._lock:
            if nonce in self._nonces:
                return False
            self._nonces.add(nonce)
            return True


class ApproverAuthenticator:
    """
    Authoritative verifier for the token-hmac-v1 authentication protocol.
    Coordinates credential checks, clock-skew verification, nonce consumption, and constant-time HMAC.
    """

    def __init__(
        self,
        cred_store: CredentialStore,
        nonce_store: NonceStore,
        secret_store: Any,
        clock_skew_seconds: float = 60.0,
    ) -> None:
        self.cred_store = cred_store
        self.nonce_store = nonce_store
        self.secret_store = secret_store
        self.clock_skew_seconds = clock_skew_seconds

    def verify_submission(
        self,
        submission: ApproverDecisionSubmission,
        expected_space_approver_id: str | None = None,
        current_time: float | None = None,
    ) -> bool:
        """
        Execute full verification sequence for a decision submission.

        Steps:
          1. Structural syntax and field format validation
          2. Clock-skew bound check (|current_time - timestamp| <= 60s)
          3. Credential lookup, revocation, and expiration check
          4. Nonce replay check (atomic persistence)
          5. Secret key resolution via SecretStore
          6. Constant-time HMAC-SHA256 signature verification
          7. Space authorization check
        """
        now = current_time if current_time is not None else time.time()

        # Step 1: Structural syntax validation
        if submission.protocol != "token-hmac-v1":
            raise MalformedAuthenticationPayloadError(
                f"Unsupported protocol '{submission.protocol}', expected 'token-hmac-v1'"
            )
        if submission.decision not in ("APPROVE", "REJECT"):
            raise MalformedAuthenticationPayloadError(
                f"Invalid decision '{submission.decision}', must be 'APPROVE' or 'REJECT'"
            )
        if not NONCE_HEX_REGEX.match(submission.nonce.lower()):
            raise MalformedAuthenticationPayloadError(
                f"Invalid nonce format '{submission.nonce}'; must be 32 lowercase hex characters"
            )
        if not HASH_HEX_REGEX.match(submission.capability_request_hash.lower()):
            raise MalformedAuthenticationPayloadError(
                "Invalid capability_request_hash format; must be 64 lowercase hex characters"
            )
        if not SIG_HEX_REGEX.match(submission.signature.lower()):
            raise MalformedAuthenticationPayloadError(
                "Invalid signature format; must be 64 lowercase hex characters"
            )
        if not UUID_REGEX.match(submission.approval_id.lower()):
            raise MalformedAuthenticationPayloadError(
                f"Invalid approval_id format '{submission.approval_id}'; must be UUIDv4"
            )
        if not ID_REGEX.match(submission.approver_id):
            raise MalformedAuthenticationPayloadError(
                f"Invalid approver_id format '{submission.approver_id}'"
            )

        # Step 2: Clock-skew bound check
        skew = abs(now - submission.timestamp)
        if skew > self.clock_skew_seconds:
            raise ClockSkewError(
                f"Timestamp skew {skew:.1f}s exceeds allowed window of +/- {self.clock_skew_seconds}s"
            )

        # Step 3: Credential lookup, revocation, and expiration check
        cred = self.cred_store.get_credential(submission.approver_id)
        if cred is None:
            raise UnknownApproverError(f"Approver '{submission.approver_id}' is not registered")
        if cred.is_revoked:
            raise TokenRevokedError(
                f"Approver credential for '{submission.approver_id}' was revoked: {cred.revocation_reason}"
            )
        if datetime.now(timezone.utc) >= cred.expires_at:
            raise TokenExpiredError(f"Approver credential for '{submission.approver_id}' has expired")

        # Step 4: Nonce replay check
        consumed = self.nonce_store.consume_nonce(
            nonce=submission.nonce.lower(),
            approver_id=submission.approver_id,
            timestamp=submission.timestamp,
        )
        if not consumed:
            raise ReplayDetectedError(
                f"Authentication nonce '{submission.nonce}' has already been consumed (replay rejected)"
            )

        # Step 5: Secret key resolution via SecretStore
        secret_val: str | None = None
        if hasattr(self.secret_store, "get"):
            secret_val = self.secret_store.get(cred.secret_ref)
        elif isinstance(self.secret_store, dict):
            secret_val = self.secret_store.get(cred.secret_ref)

        if not secret_val:
            raise AuthenticationServiceUnavailableError(
                f"Secret key material for '{cred.secret_ref}' unavailable in SecretStore (fail-closed)"
            )

        key_bytes = secret_val.encode("utf-8")

        # Step 6: Constant-time signature verification
        expected_sig = compute_token_hmac_v1_signature(
            secret_key_bytes=key_bytes,
            approver_id=submission.approver_id,
            timestamp=submission.timestamp,
            nonce=submission.nonce,
            space_id=submission.space_id,
            approval_id=submission.approval_id,
            decision=submission.decision,
            plan_version=submission.plan_version,
            capability_request_hash=submission.capability_request_hash,
        )

        if not hmac.compare_digest(expected_sig, submission.signature.lower()):
            raise InvalidSignatureError("HMAC signature verification failed (invalid signature)")

        # Step 7: Space authorization check
        if expected_space_approver_id is not None and submission.approver_id != expected_space_approver_id:
            raise PermissionError(
                f"Approver '{submission.approver_id}' is not authorized for space '{submission.space_id}' "
                f"(expected authorized approver: '{expected_space_approver_id}')"
            )

        return True

