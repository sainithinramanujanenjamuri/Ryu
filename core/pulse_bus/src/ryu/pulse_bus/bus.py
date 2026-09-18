"""PulseBus — Phase 0 in-memory Pulse Bus implementation.

Implements the minimum Phase 0 Pulse Bus behavior required by ROADMAP Phase 0 §80-88:
  - publish(): validate type → validate payload → resolve taint → append → notify subscribers
  - subscribe(): typed subscriptions
  - walk_causation(): reconstruct parent chain

Rejection contract (ROADMAP Phase 0, PULSE-001/PULSE-002/PULSE-003):
  - Unknown types: PulseRejectedError raised BEFORE append
  - Invalid payloads: PulseRejectedError raised BEFORE append
  - Nothing enters the log if validation fails

Taint contract (TAINT-002, Phase 0):
  - Child inherits taint=True from parent chain via parent_pulse_id
  - security.taint.cleared for correlation_id causes subsequent children
    born after the clear to publish with taint=False (forward-only)
  - Earlier pulses (published before the clear) remain unchanged

Phase 1+ not implemented:
  - Durable persistence (PostgreSQL/Redis Streams)
  - Distributed delivery / transport
  - Replay engine
  - Backpressure infrastructure

spec §6 (Pulse Bus Nervous System), §16 (Component Contracts) — Phase 0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ryu.pulse_bus.pulse import Pulse
from ryu.pulse_bus.validator import PulseValidator

# Pulse type for taint clearance (Architecture §10)
_TAINT_CLEARED_TYPE = "security.taint.cleared"


@dataclass
class Subscription:
    """Represents a typed subscription on the bus."""
    pulse_type: str | None  # None = subscribe to all types
    callback: Callable[[Pulse], None]
    subscription_id: str


class PulseBus:
    """
    Phase 0 in-memory Pulse Bus.

    Provides:
      - Synchronous type and payload validation before append
      - Taint inheritance via parent_pulse_id chain
      - Forward-only taint clearance via security.taint.cleared
      - Typed subscriptions with callback dispatch
      - Causation walk from any pulse back to the root

    NOT implemented (Phase 1+):
      - Durable persistence
      - Distributed delivery
      - Replay
      - Backpressure

    spec §6, §16 — Phase 0
    """

    def __init__(self, validator: PulseValidator | None = None) -> None:
        self._validator = validator or PulseValidator()
        self._log: list[Pulse] = []
        self._subscriptions: list[Subscription] = []
        # Maps correlation_id -> timestamp of security.taint.cleared pulse
        # Used for forward-only clearance resolution
        self._taint_cleared_at: dict[str, str] = {}
        self._sub_counter: int = 0

    # ------------------------------------------------------------------
    # publish
    # ------------------------------------------------------------------

    def publish(self, pulse: Pulse) -> Pulse:
        """
        Validate and publish a Pulse.

        Steps (must be executed in order per ROADMAP Phase 0):
          1. Validate type against registry   -> PulseRejectedError if unknown
          2. Validate payload against schema  -> PulseRejectedError if invalid
          3. Resolve taint from parent chain
          4. Append to internal log
          5. Notify matching subscribers

        A Pulse that fails steps 1 or 2 is NEVER appended (PULSE-003).

        Returns:
            The admitted Pulse (with resolved taint field).

        Raises:
            PulseRejectedError: on unknown type or invalid payload.
        """
        # Step 1 + 2: validate before ANYTHING is appended
        self._validator.validate(pulse.type, pulse.payload)

        # Step 3: resolve taint from parent chain (TAINT-002)
        pulse = self._resolve_taint(pulse)

        # Step 4: append after successful validation
        self._log.append(pulse)

        # Step 4b: if this is a taint-clear pulse, record the clearance timestamp
        if pulse.type == _TAINT_CLEARED_TYPE:
            corr = pulse.payload.get("correlation_id", "")
            if corr:
                self._taint_cleared_at[corr] = pulse.timestamp.isoformat()

        # Step 5: dispatch to subscribers
        self._dispatch(pulse)

        return pulse

    # ------------------------------------------------------------------
    # subscribe
    # ------------------------------------------------------------------

    def subscribe(
        self,
        callback: Callable[[Pulse], None],
        pulse_type: str | None = None,
    ) -> Subscription:
        """
        Register a subscriber callback.

        Args:
            callback:   Function to invoke when a matching Pulse is published.
            pulse_type: If specified, only receive pulses of this exact type.
                        If None, receive all pulses.

        Returns:
            Subscription handle (can be stored for future removal).

        spec §6 — Phase 0
        """
        self._sub_counter += 1
        sub = Subscription(
            pulse_type=pulse_type,
            callback=callback,
            subscription_id=f"sub-{self._sub_counter}",
        )
        self._subscriptions.append(sub)
        return sub

    def unsubscribe(self, subscription: Subscription) -> None:
        """Remove a subscription by reference."""
        self._subscriptions = [
            s for s in self._subscriptions if s.subscription_id != subscription.subscription_id
        ]

    # ------------------------------------------------------------------
    # walk_causation
    # ------------------------------------------------------------------

    def walk_causation(self, pulse_id: str) -> list[Pulse]:
        """
        Reconstruct the causal ancestry chain for a given pulse_id.

        Starting from the pulse identified by pulse_id, walks parent_pulse_id
        links back to the root (a pulse with no parent).

        Per docs/Architecture §6:
          "from any Pulse, walk parent_pulse_id back to root to reconstruct
           the exact causal chain"

        Returns:
            List of Pulse objects from root to the given pulse_id (inclusive).
            The last element is the pulse with the given id.

        Raises:
            KeyError: if pulse_id is not found in the log.
            RuntimeError: if a cycle is detected in the parent chain.

        spec §6, PULSE-004 — Phase 0
        """
        pulse_index: dict[str, Pulse] = {p.id: p for p in self._log}

        if pulse_id not in pulse_index:
            raise KeyError(f"Pulse id {pulse_id!r} not found in bus log.")

        chain: list[Pulse] = []
        visited: set[str] = set()
        current_id: str | None = pulse_id

        while current_id is not None:
            if current_id in visited:
                raise RuntimeError(
                    f"Cycle detected in causation chain at pulse id {current_id!r}"
                )
            visited.add(current_id)
            pulse = pulse_index.get(current_id)
            if pulse is None:
                raise KeyError(
                    f"Parent pulse id {current_id!r} referenced but not found in log."
                )
            chain.append(pulse)
            current_id = pulse.parent_pulse_id

        chain.reverse()  # root first
        return chain

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    def log(self) -> list[Pulse]:
        """Return a copy of the immutable event log (all admitted pulses)."""
        return list(self._log)

    def log_size(self) -> int:
        """Return the number of admitted pulses."""
        return len(self._log)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_taint(self, pulse: Pulse) -> Pulse:
        """
        Determine the correct taint value for a pulse.

        Rules (TAINT-002, Architecture §10 Prompt-Injection & Taint Model):
          - If pulse already has taint=True, keep it.
          - If pulse has a parent_pulse_id, inherit taint from the parent chain.
          - If a security.taint.cleared pulse exists for this correlation_id,
            downstream pulses BORN AFTER the clear get taint=False.
            Earlier pulses are NEVER mutated.
        """
        if pulse.taint:
            # Caller explicitly set taint; accept as-is
            return pulse

        if pulse.parent_pulse_id is None:
            # Root pulse — no inheritance applies
            return pulse

        # Look up parent
        pulse_index: dict[str, Pulse] = {p.id: p for p in self._log}
        parent = pulse_index.get(pulse.parent_pulse_id)
        if parent is None:
            # Parent not in log yet (should not happen in single-bus Phase 0)
            return pulse

        if not parent.taint:
            # Clean parent — no inheritance
            return pulse

        # Parent is tainted — check if cleared for this correlation
        corr = pulse.correlation_id
        if corr in self._taint_cleared_at:
            # Taint was cleared for this correlation; this pulse is born after clear
            # -> taint=False (forward-only clearance)
            return pulse  # pulse.taint is already False

        # Parent tainted, no clearance -> inherit
        return Pulse(
            id=pulse.id,
            space_id=pulse.space_id,
            type=pulse.type,
            severity=pulse.severity,
            source=pulse.source,
            timestamp=pulse.timestamp,
            payload=pulse.payload,
            taint=True,
            correlation_id=pulse.correlation_id,
            parent_pulse_id=pulse.parent_pulse_id,
        )

    def _dispatch(self, pulse: Pulse) -> None:
        """Invoke all matching subscriber callbacks."""
        for sub in list(self._subscriptions):
            if sub.pulse_type is None or sub.pulse_type == pulse.type:
                sub.callback(pulse)

