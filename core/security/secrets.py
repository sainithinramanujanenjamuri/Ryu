"""Secret Store and Secret Resolver for authorized sandbox execution.

Secrets are referenced by URI (secret://<provider>/<name>) and resolved only
at the execution boundary at the last possible moment (docs/Architecture §16, ADR-0004).

spec §10 (Secret containment), §16 (SecretRef), SECRET-003 — Phase 2
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

SECRET_URI_REGEX = re.compile(r"^secret://[a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+$")
MIN_SECRET_LENGTH = 6


@dataclass(frozen=True)
class SecretRef:
    """Canonical secret reference URI."""
    uri: str

    def __post_init__(self) -> None:
        if not SECRET_URI_REGEX.match(self.uri):
            raise ValueError(
                f"Invalid SecretRef URI '{self.uri}'; must conform to 'secret://<provider>/<name>'"
            )


class SecretStore:
    """
    In-memory registry of secret references and sensitive values.

    Enforces minimum secret length and exposes active values for containment validation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._secrets: dict[str, str] = {}

    def register(self, uri: str, value: str) -> None:
        """Register a secret under a canonical secret:// URI."""
        if not SECRET_URI_REGEX.match(uri):
            raise ValueError(
                f"Invalid SecretRef URI '{uri}'; must conform to 'secret://<provider>/<name>'"
            )
        if len(value) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"Secret value too short ({len(value)} chars); must be at least "
                f"{MIN_SECRET_LENGTH} characters to prevent false positives (ADR-0004)."
            )
        with self._lock:
            self._secrets[uri] = value

    def get(self, uri: str) -> str | None:
        """Retrieve a secret value by URI."""
        with self._lock:
            return self._secrets.get(uri)

    def get_active_secret_values(self) -> frozenset[str]:
        """Return the set of all active registered secret values for containment checks."""
        with self._lock:
            return frozenset(self._secrets.values())


class SecretResolver:
    """
    Resolves SecretRef URIs strictly within the execution sandbox boundary.
    """

    def __init__(self, store: SecretStore) -> None:
        self.store = store

    def resolve(self, uri: str) -> str:
        """Resolve a secret URI to its sensitive string value."""
        val = self.store.get(uri)
        if val is None:
            raise KeyError(f"Secret URI '{uri}' not found in SecretStore.")
        return val
