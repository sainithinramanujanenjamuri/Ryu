"""Pulse data model for RYU AI.

Defines the canonical Pulse structure per docs/Architecture §16 Component Contracts.
Every Pulse must have a fixed, typed schema (Law 3).

spec §16 — Phase 0
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """Pulse severity levels as defined in docs/Architecture §6."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Pulse:
    """
    The canonical Pulse contract.

    Every inter-component communication in RYU AI passes through a typed Pulse
    (Law 3: Components Communicate Through Pulses).

    Required fields per docs/Architecture §16:
      id, space_id, type, severity, source, timestamp, payload,
      taint, correlation_id, parent_pulse_id

    The ``type`` field is a dot-namespaced string validated against the
    Pulse Type Registry in contracts/registry/pulse-types.json.

    ``taint`` propagates from parent to child via parent_pulse_id (Law 3 / taint model).
    """

    type: str
    """Dot-namespaced pulse type; must exist in contracts/registry/pulse-types.json."""

    payload: dict[str, Any]
    """Type-specific body; validated against payload-schemas/<type>.json."""

    space_id: str
    """Owning Space identity."""

    source: str
    """Component that published this pulse (e.g. 'test', 'worker.python')."""

    correlation_id: str
    """Ties a Pulse chain back to one originating Command."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Globally unique pulse identifier."""

    severity: Severity = Severity.INFO
    """Severity level for routing and escalation."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """Publication timestamp."""

    taint: bool = False
    """True if payload originated from untrusted external content (see taint model)."""

    parent_pulse_id: str | None = None
    """Immediate causal parent pulse id; walk chain to reconstruct causal ancestry."""

