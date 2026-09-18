"""Plan Delta data structures for atomic plan evolution.

spec §16 (TaskGraph & PlanDelta), PLAN-001/002 — Phase 2
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

ALLOWED_OPS = frozenset({"add", "remove", "reassign", "rollback"})


@dataclass(frozen=True)
class DeltaOp:
    """Individual operation within a PlanDelta."""
    op: str  # "add" | "remove" | "reassign" | "rollback"
    target_node_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.op not in ALLOWED_OPS:
            raise ValueError(f"Invalid PlanDelta op '{self.op}'; must be one of {ALLOWED_OPS}")


@dataclass(frozen=True)
class PlanDelta:
    """
    Structured diff applied against base_version of a TaskGraph.

    Never in-place mutation. Applied atomically via single-writer CAS.
    """
    space_id: str
    base_version: int
    resulting_version: int
    ops: list[dict[str, Any]]
    delta_id: str = field(default_factory=lambda: f"delta-{uuid.uuid4().hex[:8]}")

    def __post_init__(self) -> None:
        if self.resulting_version != self.base_version + 1:
            raise ValueError(
                f"Invalid resulting_version {self.resulting_version}; "
                f"must be exactly base_version ({self.base_version}) + 1"
            )
        for op in self.ops:
            op_type = op.get("op")
            if op_type not in ALLOWED_OPS:
                raise ValueError(
                    f"Invalid operation '{op_type}' in PlanDelta; must be one of {ALLOWED_OPS}"
                )
