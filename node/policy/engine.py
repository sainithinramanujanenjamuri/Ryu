"""Device-side MDM Policy Evaluation Engine.

CONTRACT_MATRIX NODE-012, ADR-0039 — Phase 11
Enforces local capability allow-list policy on RESTRICTED nodes as an
additional local policy constraint.
"""

from __future__ import annotations

import logging
from typing import Any

from node.policy.models import NodeTrustTier, RestrictedNodePolicy

logger = logging.getLogger(__name__)


class DevicePolicyEngine:
    """Evaluates device-local MDM policies on Restricted nodes."""

    @staticmethod
    def evaluate(
        trust_tier: NodeTrustTier,
        policy: RestrictedNodePolicy | None,
        capability: str,
        parameters: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        """Evaluate capability and parameters against node policy.

        Returns (True, None) if permitted.
        Returns (False, reason) if denied.
        """
        if trust_tier == NodeTrustTier.FULL_TRUST:
            return True, None

        if policy is None:
            # A Restricted node without a policy denies all capabilities by default (fail-closed)
            return False, "Restricted node has no configured MDM policy; fail-closed default."

        permitted, reason = policy.is_capability_permitted(capability)
        if not permitted:
            return False, reason

        # Check path restrictions if storage capability
        if parameters and "path" in parameters and policy.allowed_storage_paths:
            req_path = str(parameters["path"]).replace("\\", "/")
            allowed = False
            for p in policy.allowed_storage_paths:
                p_norm = p.replace("\\", "/")
                if req_path.startswith(p_norm):
                    allowed = True
                    break
            if not allowed:
                return False, f"Storage path '{req_path}' is not within MDM allowed paths: {policy.allowed_storage_paths}"

        return True, None

