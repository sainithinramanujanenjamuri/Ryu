"""RYU AI Space Kernel Package — Phase 2 Space Kernel.

Enforces Space isolation, admission, plan versioning, and human gates (docs/Architecture §4).
"""

from __future__ import annotations

from typing import Any

from core.space.approver import ApprovalManager, ApprovalRequest
from core.space.attention import AttentionBudget
from core.space.kernel import SpaceKernel


def create_space(
    space_id: str,
    owner_id: str,
    bus: Any | None = None,
    budget: float = 0.0,
    budget_policy: str = "hard_stop",
) -> SpaceKernel:
    """Create a new Space isolation boundary and return its authoritative SpaceKernel."""
    return SpaceKernel(
        space_id=space_id,
        owner_id=owner_id,
        bus=bus,
        budget=budget,
        budget_policy=budget_policy,
    )


__all__ = [
    "ApprovalManager",
    "ApprovalRequest",
    "AttentionBudget",
    "SpaceKernel",
    "create_space",
]
