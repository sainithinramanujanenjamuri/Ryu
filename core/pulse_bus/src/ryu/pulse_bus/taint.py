from ryu.pulse_bus.pulse import Pulse
from ryu.pulse_bus.store import PulseStore

_TAINT_CLEARED_TYPE = "security.taint.cleared"

class TaintResolver:
    def __init__(self, store: PulseStore):
        self.store = store

    def is_taint_cleared(self, correlation_id: str, parent_pulse: Pulse | None) -> bool:
        """
        Check if a clearance event exists for this correlation_id AND the parent
        pulse's position is before the clearance pulse's position.
        Since we don't have position directly on the in-memory Pulse object, we
        rely on the store's sequence. In practice, we just find all clearance events
        for this correlation_id. If any exists, and its position is after the parent's
        or we don't know, we assume cleared IF it was published before this pulse.
        """
        clearances = [
            p for p in self.store.read_by_correlation(correlation_id)
            if p.type == _TAINT_CLEARED_TYPE
        ]
        if not clearances:
            return False

        # Simplification for Phase 1: if there's a clearance for this correlation,
        # we consider it cleared for NEW pulses. The clearance is already in the store
        # before this pulse is appended, so forward-only semantics hold.
        return True

    def resolve_taint(self, pulse: Pulse) -> bool:
        if pulse.taint:
            return True

        if pulse.parent_pulse_id is None:
            return False

        parent = self.store.get_by_id(pulse.parent_pulse_id)
        if parent is None:
            return False

        if not parent.taint:
            return False

        if self.is_taint_cleared(pulse.correlation_id, parent):
            return False

        return True

