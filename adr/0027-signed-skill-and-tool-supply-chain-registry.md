# ADR-0027: Signed Skill and Tool Supply Chain Registry

## Status
Accepted

## Context
Phase 9 introduces the Skills layer and external capability extensions (including Model Context Protocol tools) into RYU AI. In the Space-Centric Cognitive Architecture (SCCA), extensions must not become an uncontrolled attack surface. Unsigned code, payload swapping after approval, version confusion, and self-service risk elevation represent critical supply chain vulnerabilities.

SCCA Law 1 (Space Authority) and Law 2 (Capabilities Are Requested, Never Owned) require that all capability providers be explicitly registered, content-addressed, cryptographically signed, and bound to immutable risk classifications before being made available to any Space.

## Problem
1. How does the system guarantee that a Skill or Tool has not been tampered with or modified after registration?
2. How does the system prevent payload swapping (replacing benign code with malicious code while retaining the registered identity)?
3. How are risk tiers (`low` vs `high`) bound to artifact identities to prevent unauthorized privilege escalation?
4. How does the system prevent version confusion attacks (e.g. resolving unpinned `@latest` tags)?

## Decision
1. **Explicit SemVer Identity (`REG-001`):** Every registered Skill and Tool must declare an explicit, valid Semantic Version string (`major.minor.patch`). Floating tags such as `@latest`, `@dev`, or unbounded version ranges are strictly prohibited in Space dependencies (`REG-005`). Any Space configuration attempting to resolve `@latest` will be rejected by the Space Kernel.
2. **SHA-256 Content Addressing (`REG-002`):** Every registration computes a SHA-256 `content_hash` over the canonical serialization of the manifest and artifact payload. The registered identity is `(id, version, content_hash)`. Any byte modification produces a distinct, unclassified hash that does not inherit any prior approvals.
3. **Cryptographic Signature Verification (`REG-003`):** Every Skill and Tool registration must carry a valid cryptographic signature (Ed25519 or HMAC-SHA256) over `(id, version, content_hash, risk_tier, registered_by)` verified against trusted author/platform credentials. Unsigned or signature-mismatched registrations are rejected synchronously.
4. **Immutable Risk Tier Binding (`REG-004`):** The `risk_tier` (`low` | `high`) is set at registration and cryptographically bound to `content_hash`. Reclassifying a registered artifact's risk tier requires an authenticated human action recorded as a `security.grant.approved` Pulse; self-service mutation by agents or tools is strictly forbidden.
5. **Idempotent Registry Operations:** Registering an identical `(id, version, content_hash, signature)` tuple is idempotent. Registering an existing `(id, version)` with a differing `content_hash` raises a `RegistrationConflictError`.

## Alternatives Considered
- **Mutable Version Tags (`@latest`):** Rejected because dynamic tag updates allow silent modification of execution behavior, violating deterministic replay and causality tracking.
- **Trust-on-First-Use (TOFU):** Rejected because it permits first-time ingestion of malicious unverified artifacts without administrative provenance.
- **Dynamic In-Memory Skill Injection:** Rejected because Skills must belong to the registered supply chain and be auditable via the Pulse Bus.

## Consequences
- All Skills and Tools must be signed and hashed prior to Space registration.
- Spaces must explicitly pin exact version numbers in plan templates.
- Any change to code requires publishing a new version with a new cryptographic hash and signature.

## Date
2026-09-22

