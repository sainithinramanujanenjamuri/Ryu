"""RYU AI Base Agent State Machine — Phase 0 Scaffold.
Full implementation deferred to Phase 5.
"""

class BaseAgent:
    """Deterministic agent state machine."""

    def __init__(self, agent_id: str, space_id: str):
        self.agent_id = agent_id
        self.space_id = space_id

    def step(self) -> None:
        raise NotImplementedError("spec §7 — Phase 5")

