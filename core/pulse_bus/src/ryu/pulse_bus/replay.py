from typing import Iterator

from ryu.pulse_bus.pulse import Pulse
from ryu.pulse_bus.store import PulseStore


class PulseReplayer:
    def __init__(self, store: PulseStore):
        self.store = store

    def replay_from(self, from_position: int = 0) -> Iterator[Pulse]:
        if hasattr(self.store, "read_page"):
            cursor = from_position
            while True:
                page = self.store.read_page(after_position=cursor, limit=100)
                for entry in page.entries:
                    yield entry.pulse
                    cursor = entry.position
                if not page.has_more:
                    break
        else:
            for p in self.store.read(from_position):
                yield p

    def replay_by_correlation(self, correlation_id: str) -> list[Pulse]:
        return self.store.read_by_correlation(correlation_id)

    def replay_by_space(self, space_id: str) -> list[Pulse]:
        if hasattr(self.store, "iter_space"):
            return [sp.pulse for sp in self.store.iter_space(space_id)]
        return self.store.read_by_space(space_id)

    def replay_causal_chain(self, leaf_pulse_id: str) -> list[Pulse]:
        chain: list[Pulse] = []
        visited = set()
        current_id: str | None = leaf_pulse_id

        while current_id is not None:
            if current_id in visited:
                raise RuntimeError(f"Cycle detected at {current_id}")
            visited.add(current_id)
            pulse = self.store.get_by_id(current_id)
            if pulse is None:
                raise KeyError(f"Parent {current_id} not found")
            chain.append(pulse)
            current_id = pulse.parent_pulse_id

        chain.reverse()
        return chain

