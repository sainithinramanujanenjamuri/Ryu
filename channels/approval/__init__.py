"""Approval subsystem: human gate lifecycles, approver authentication, and durable storage."""

from channels.approval.auth import (
    ApproverAuthenticator,
    ApproverCredentialRecord,
    ApproverDecisionSubmission,
    AuthenticationError,
    ClockSkewError,
    InMemoryCredentialStore,
    InvalidSignatureError,
    MalformedAuthenticationPayloadError,
    NonceStore,
    ReplayDetectedError,
    TokenExpiredError,
    TokenRevokedError,
    UnknownApproverError,
    compute_token_hmac_v1_signature,
)
from channels.approval.client import ApprovalClient
from channels.approval.store import PostgresApprovalStore

__all__ = [
    "ApprovalClient",
    "ApproverAuthenticator",
    "ApproverCredentialRecord",
    "ApproverDecisionSubmission",
    "AuthenticationError",
    "ClockSkewError",
    "InMemoryCredentialStore",
    "InvalidSignatureError",
    "MalformedAuthenticationPayloadError",
    "NonceStore",
    "PostgresApprovalStore",
    "ReplayDetectedError",
    "TokenExpiredError",
    "TokenRevokedError",
    "UnknownApproverError",
    "compute_token_hmac_v1_signature",
]

