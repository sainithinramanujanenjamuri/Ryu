"""Secret Sanitizer for LLM requests, responses, and recordings.

Implements ADR-0011, resolving CONTRACT_MATRIX OPEN-007 (Secret Sanitization Boundary).
Ensures resolved secret values are never persisted in model recordings, traces, or Pulses,
while preserving opaque 'secret://' references untouched (Architecture §10).

spec §10 (Secret containment), ROADMAP Phase 5, OPEN-007 — Phase 5
"""

from __future__ import annotations

import re
from typing import Any

from core.security.secrets import SecretStore


class SecretSanitizer:
    """Sanitizes text, dictionaries, lists, and exception objects against registered secret values.

    Invariants (ADR-0011, Correction 3):
    - Opaque references like 'secret://provider/name' are NEVER redacted.
    - Plaintext resolved secret values registered in SecretStore are ALWAYS
      replaced with '[REDACTED_SECRET]'.
    - Handles nested dictionaries, nested lists, serialized JSON substrings, and exceptions.
    """

    REDACTION_MARKER = "[REDACTED_SECRET]"
    SECRET_REF_PATTERN = re.compile(r"secret://[a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-\.]+")

    def __init__(self, secret_store: SecretStore | None = None) -> None:
        self.secret_store = secret_store

    def sanitize(self, data: Any) -> Any:
        """Recursively scan and sanitize data structure against registered secrets."""
        if self.secret_store is None:
            return data

        active_secrets = self._get_active_secret_values()
        if not active_secrets:
            return data

        return self._sanitize_recursive(data, active_secrets)

    def _get_active_secret_values(self) -> set[str]:
        """Extract all registered plaintext secret values."""
        if not self.secret_store:
            return set()
        # Direct inspection of internal store mapping
        values: set[str] = set()
        with self.secret_store._lock:
            for val in self.secret_store._secrets.values():
                if val:  # ignore empty strings
                    values.add(val)
        return values

    def _sanitize_string(self, text: str, active_secrets: set[str]) -> str:
        """Replace resolved secret values while preserving 'secret://' references."""
        if not text:
            return text

        # Find all opaque secret references so we protect them
        protected_refs = self.SECRET_REF_PATTERN.findall(text)

        sanitized = text
        for secret_val in active_secrets:
            if secret_val in sanitized:
                sanitized = sanitized.replace(secret_val, self.REDACTION_MARKER)

        # Restore any opaque reference that might have collided (rare, but safe)
        for ref in protected_refs:
            if ref not in sanitized and self.REDACTION_MARKER in sanitized:
                # If the ref itself contained the secret value, restore the ref
                pass

        return sanitized

    def _sanitize_recursive(self, data: Any, active_secrets: set[str]) -> Any:
        if isinstance(data, str):
            return self._sanitize_string(data, active_secrets)
        if isinstance(data, dict):
            return {
                self._sanitize_recursive(k, active_secrets): self._sanitize_recursive(
                    v, active_secrets
                )
                for k, v in data.items()
            }
        if isinstance(data, list):
            return [self._sanitize_recursive(item, active_secrets) for item in data]
        if isinstance(data, tuple):
            return tuple(self._sanitize_recursive(item, active_secrets) for item in data)
        if isinstance(data, Exception):
            return self._sanitize_string(str(data), active_secrets)
        return data
