"""PulseRejectedError — synchronous rejection for invalid Pulse submissions.

Per docs/Architecture §6 and ROADMAP.md Phase 0:
  Unknown types and malformed payloads are rejected BEFORE append.
  Rejection is surfaced as a protocol-level error (not a Pulse about a Pulse).

spec §16 (Pulse Bus) — Phase 0
"""

from __future__ import annotations


class PulseRejectedError(Exception):
    """
    Raised synchronously when a Pulse cannot be admitted to the bus.

    The Bus raises this BEFORE appending anything to the event log.
    A PulseRejectedError must never be silently swallowed (Law 6).

    Attributes:
        reason:         Short human-readable reason code (e.g. 'unknown_type').
        offending_type: The pulse type string that triggered the rejection.
        details:        Additional diagnostic information.
    """

    def __init__(self, reason: str, offending_type: str, details: str = "") -> None:
        self.reason = reason
        self.offending_type = offending_type
        self.details = details
        super().__init__(
            f"PulseRejected(reason={reason!r}, type={offending_type!r}, details={details!r})"
        )

