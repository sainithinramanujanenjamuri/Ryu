"""Skill and Tool Registry implementation.

Enforces version identity (REG-001), content-addressing (REG-002),
cryptographic signatures (REG-003), risk tier binding (REG-004),
and strict @version pinning (REG-005).

spec §9 (Registry Service), §16 (Component Contracts),
docs/CONTRACT_MATRIX.md REG-001..REG-006, ROADMAP Phase 9, ADR-0027 — Phase 9
"""

from __future__ import annotations

import hashlib
import hmac
import threading
from typing import Any, Protocol

from skills.model import (
    RiskTier,
    SkillLifecycleState,
    SkillRegistration,
    ToolRegistration,
    compute_sha256_hash,
)


class RegistryError(Exception):
    """Base error for all Skill/Tool registry operations."""


class UnsignedArtifactError(RegistryError):
    """Raised when an artifact has no signature or signature verification fails (REG-003)."""


class ContentHashMismatchError(RegistryError):
    """Raised when payload content hash does not match declared hash (REG-002)."""


class RegistrationConflictError(RegistryError):
    """Raised when registering an existing (id, version) with a different content_hash."""


class InvalidVersionPinningError(RegistryError):
    """Raised when attempting to resolve a Skill or Tool with @latest (REG-005)."""


class SecurityPolicyViolationError(RegistryError):
    """Raised when attempting unauthorized risk tier mutation without human approval (REG-004)."""


class SkillNotFoundError(RegistryError):
    """Raised when requested skill_id@version is not registered."""


class ToolNotFoundError(RegistryError):
    """Raised when requested tool_id@version is not registered."""


class SignatureVerifier(Protocol):
    """Protocol for validating artifact signatures."""

    def verify(
        self,
        identifier: str,
        version: str,
        content_hash: str,
        risk_tier: RiskTier,
        registered_by: str,
        signature: str,
    ) -> bool: ...


class DefaultHmacVerifier:
    """Default HMAC-SHA256 signature verifier for local/trusted registry signing."""

    def __init__(self, secret_key: str | bytes = "ryu-official-signing-key") -> None:
        self._secret = secret_key.encode("utf-8") if isinstance(secret_key, str) else secret_key

    def sign(
        self,
        identifier: str,
        version: str,
        content_hash: str,
        risk_tier: RiskTier,
        registered_by: str,
    ) -> str:
        msg = f"{identifier}:{version}:{content_hash}:{risk_tier.value}:{registered_by}".encode("utf-8")
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def verify(
        self,
        identifier: str,
        version: str,
        content_hash: str,
        risk_tier: RiskTier,
        registered_by: str,
        signature: str,
    ) -> bool:
        expected = self.sign(identifier, version, content_hash, risk_tier, registered_by)
        return hmac.compare_digest(expected, signature)


class SkillRegistry:
    """
    Centralized, content-addressed registry for Skills and Tools.

    Enforces REG-001 through REG-006. Thread-safe.
    """

    def __init__(self, verifier: SignatureVerifier | None = None) -> None:
        self._lock = threading.RLock()
        self.verifier = verifier or DefaultHmacVerifier()
        self._skills: dict[str, dict[str, SkillRegistration]] = {}  # skill_id -> version -> SkillRegistration
        self._tools: dict[str, dict[str, ToolRegistration]] = {}    # tool_id -> version -> ToolRegistration

    # ----------------------------------------------------------------------
    # Skill Operations
    # ----------------------------------------------------------------------

    def register_skill(
        self,
        registration: SkillRegistration,
        payload: bytes | str | dict[str, Any] | None = None,
    ) -> SkillRegistration:
        """
        Register a Skill into the registry.

        Enforces:
        - Content hash integrity if payload is provided (REG-002)
        - Signature verification (REG-003)
        - Idempotent deduplication
        - Rejection of conflicting payload swaps
        """
        with self._lock:
            # 1. Content Hash Verification (REG-002)
            if payload is not None:
                actual_hash = compute_sha256_hash(payload)
                if actual_hash != registration.content_hash:
                    raise ContentHashMismatchError(
                        f"Skill '{registration.versioned_id}' content hash mismatch: "
                        f"expected {registration.content_hash}, calculated {actual_hash}"
                    )

            # 2. Cryptographic Signature Verification (REG-003)
            is_valid = self.verifier.verify(
                identifier=registration.skill_id,
                version=registration.version,
                content_hash=registration.content_hash,
                risk_tier=registration.risk_tier,
                registered_by=registration.registered_by,
                signature=registration.signature,
            )
            if not is_valid:
                raise UnsignedArtifactError(
                    f"Signature verification failed for Skill '{registration.versioned_id}'"
                )

            # 3. Version & Duplicate Check
            if registration.skill_id not in self._skills:
                self._skills[registration.skill_id] = {}

            existing = self._skills[registration.skill_id].get(registration.version)
            if existing is not None:
                if existing.content_hash == registration.content_hash:
                    # Idempotent re-registration of exact same artifact
                    return existing
                raise RegistrationConflictError(
                    f"Conflicting registration for Skill '{registration.versioned_id}': "
                    f"already registered with hash '{existing.content_hash}', "
                    f"attempted registration with hash '{registration.content_hash}'"
                )

            self._skills[registration.skill_id][registration.version] = registration
            return registration

    def get_skill(self, skill_id: str, version: str) -> SkillRegistration:
        """
        Retrieve a registered Skill by ID and explicit version.

        Strictly forbids @latest (REG-005).
        """
        if not version or version.lower() in ("latest", "@latest", "*"):
            raise InvalidVersionPinningError(
                f"Cannot resolve Skill '{skill_id}' using unpinned version '{version}'. "
                "Spaces must explicitly reference a SemVer pinned version (e.g. '1.0.0'). (REG-005)"
            )

        with self._lock:
            versions = self._skills.get(skill_id)
            if not versions or version not in versions:
                raise SkillNotFoundError(f"Skill '{skill_id}@{version}' is not registered.")
            return versions[version]

    def list_skills(self, state: SkillLifecycleState | None = None) -> list[SkillRegistration]:
        """List all registered skills, optionally filtered by state."""
        with self._lock:
            results: list[SkillRegistration] = []
            for ver_map in self._skills.values():
                for skill in ver_map.values():
                    if state is None or skill.lifecycle_state == state:
                        results.append(skill)
            return sorted(results, key=lambda s: s.versioned_id)

    def set_skill_state(
        self,
        skill_id: str,
        version: str,
        new_state: SkillLifecycleState,
    ) -> SkillRegistration:
        """Update the lifecycle state of a registered Skill (ENABLED / DISABLED / REVOKED)."""
        with self._lock:
            skill = self.get_skill(skill_id, version)
            if skill.lifecycle_state == SkillLifecycleState.REVOKED and new_state != SkillLifecycleState.REVOKED:
                raise SecurityPolicyViolationError(
                    f"Cannot re-enable permanently revoked Skill '{skill.versioned_id}'."
                )

            updated = SkillRegistration(
                skill_id=skill.skill_id,
                version=skill.version,
                content_hash=skill.content_hash,
                signature=skill.signature,
                capabilities=skill.capabilities,
                risk_tier=skill.risk_tier,
                registered_by=skill.registered_by,
                description=skill.description,
                input_schema=skill.input_schema,
                output_schema=skill.output_schema,
                lifecycle_state=new_state,
                metadata=skill.metadata,
                created_at=skill.created_at,
            )
            self._skills[skill_id][version] = updated
            return updated

    def reclassify_skill_risk(
        self,
        skill_id: str,
        version: str,
        new_risk_tier: RiskTier,
        grant_approved_pulse: dict[str, Any] | None = None,
    ) -> SkillRegistration:
        """
        Reclassify the risk tier of a registered Skill (REG-004).

        MUST be accompanied by a verified human security.grant.approved pulse.
        """
        with self._lock:
            skill = self.get_skill(skill_id, version)
            if grant_approved_pulse is None:
                raise SecurityPolicyViolationError(
                    f"Risk tier reclassification for Skill '{skill.versioned_id}' requires a "
                    "human-approved 'security.grant.approved' Pulse. (REG-004)"
                )

            # Validate pulse properties
            if grant_approved_pulse.get("type") != "security.grant.approved":
                raise SecurityPolicyViolationError("Invalid approval pulse type for risk reclassification.")
            approver = grant_approved_pulse.get("payload", {}).get("approver_id")
            if not approver:
                raise SecurityPolicyViolationError("Approval pulse missing required 'approver_id'.")

            updated = SkillRegistration(
                skill_id=skill.skill_id,
                version=skill.version,
                content_hash=skill.content_hash,
                signature=skill.signature,
                capabilities=skill.capabilities,
                risk_tier=new_risk_tier,
                registered_by=skill.registered_by,
                description=skill.description,
                input_schema=skill.input_schema,
                output_schema=skill.output_schema,
                lifecycle_state=skill.lifecycle_state,
                metadata={**skill.metadata, "reclassified_by": approver},
                created_at=skill.created_at,
            )
            self._skills[skill_id][version] = updated
            return updated

    # ----------------------------------------------------------------------
    # Tool Operations
    # ----------------------------------------------------------------------

    def register_tool(
        self,
        registration: ToolRegistration,
        payload: bytes | str | dict[str, Any] | None = None,
    ) -> ToolRegistration:
        """
        Register a Tool into the registry.

        Enforces REG-001..REG-004.
        """
        with self._lock:
            if payload is not None:
                actual_hash = compute_sha256_hash(payload)
                if actual_hash != registration.content_hash:
                    raise ContentHashMismatchError(
                        f"Tool '{registration.versioned_id}' content hash mismatch: "
                        f"expected {registration.content_hash}, calculated {actual_hash}"
                    )

            is_valid = self.verifier.verify(
                identifier=registration.tool_id,
                version=registration.version,
                content_hash=registration.content_hash,
                risk_tier=registration.risk_tier,
                registered_by=registration.registered_by,
                signature=registration.signature,
            )
            if not is_valid:
                raise UnsignedArtifactError(
                    f"Signature verification failed for Tool '{registration.versioned_id}'"
                )

            if registration.tool_id not in self._tools:
                self._tools[registration.tool_id] = {}

            existing = self._tools[registration.tool_id].get(registration.version)
            if existing is not None:
                if existing.content_hash == registration.content_hash:
                    return existing
                raise RegistrationConflictError(
                    f"Conflicting registration for Tool '{registration.versioned_id}': "
                    f"already registered with hash '{existing.content_hash}'"
                )

            self._tools[registration.tool_id][registration.version] = registration
            return registration

    def get_tool(self, tool_id: str, version: str) -> ToolRegistration:
        """Retrieve a registered Tool by ID and explicit version."""
        if not version or version.lower() in ("latest", "@latest", "*"):
            raise InvalidVersionPinningError(
                f"Cannot resolve Tool '{tool_id}' using unpinned version '{version}'. (REG-005)"
            )

        with self._lock:
            versions = self._tools.get(tool_id)
            if not versions or version not in versions:
                raise ToolNotFoundError(f"Tool '{tool_id}@{version}' is not registered.")
            return versions[version]

    def list_tools(
        self,
        server_id: str | None = None,
        capability: str | None = None,
    ) -> list[ToolRegistration]:
        """List registered tools, optionally filtered by server_id or capability."""
        with self._lock:
            results: list[ToolRegistration] = []
            for ver_map in self._tools.values():
                for tool in ver_map.values():
                    if server_id is not None and tool.server_id != server_id:
                        continue
                    if capability is not None and tool.capability != capability:
                        continue
                    results.append(tool)
            return sorted(results, key=lambda t: t.versioned_id)

