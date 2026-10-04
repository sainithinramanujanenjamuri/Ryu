"""RYU AI Space Kernel Package — Phase 2 Space Kernel.

Enforces Space isolation, admission, plan versioning, and human gates (docs/Architecture §4).
"""

from __future__ import annotations

from typing import Any

from core.space.approver import ApprovalManager, ApprovalRequest
from core.space.artifact_paths import (
    CANONICAL_WORKER_NAMESPACES,
    get_space_artifact_dir,
    is_safe_artifact_path,
    resolve_artifact_path,
    validate_artifact_filename,
    validate_space_id,
    validate_worker_namespace,
)
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
    "CANONICAL_WORKER_NAMESPACES",
    "SpaceKernel",
    "create_space",
    "get_space_artifact_dir",
    "is_safe_artifact_path",
    "resolve_artifact_path",
    "validate_artifact_filename",
    "validate_space_id",
    "validate_worker_namespace",
]
