from ryu.pulse_bus.pulse import Pulse
from ryu.pulse_bus.store import PulseStore

_TAINT_CLEARED_TYPE = "security.taint.cleared"

class TaintResolver:
    def __init__(self, store: PulseStore):
        self.store = store

    def is_taint_cleared(
        self,
        correlation_id: str,
        parent_pulse: Pulse | None,
        space_id: str | None = None,
    ) -> bool:
        """Check if a clearance event exists for this correlation_id."""
        target_space = space_id or (parent_pulse.space_id if parent_pulse else None)
        if target_space and hasattr(self.store, "has_pulse_of_type"):
            return self.store.has_pulse_of_type(target_space, correlation_id, _TAINT_CLEARED_TYPE)

        clearances = [
            p for p in self.store.read_by_correlation(correlation_id)
            if p.type == _TAINT_CLEARED_TYPE
        ]
        if not clearances:
            return False

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

        if self.is_taint_cleared(pulse.correlation_id, parent, space_id=pulse.space_id):
            return False

        return True

