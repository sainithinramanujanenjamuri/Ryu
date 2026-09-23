# ADR-0035: Knowledge Promotion Authorization and Forgery Resistance

## Status
Accepted

## Context
Under SCCA Law 4, "Knowledge belongs to the Space first." Cross-Space knowledge promotion is a critical security and authority boundary. Global knowledge influences all future Spaces; therefore, promoting Space-local experience into global knowledge must satisfy rigorous evaluation and explicit, authenticated human approval (Law 5).

Any mechanism that allows unverified, forged, replayed, or cross-space knowledge writes invalidates the isolation guarantees of SCCA.

## Problem
1. How is cross-space knowledge promotion secured against forgery and replay?
2. How is human approval authenticated without inventing a redundant, un-integrated approval system?
3. How is the promotion pipeline strictly bound to the requesting Space?
4. How is the interface between `PromotionPipeline` and `SpaceMemoryProtocol` decoupled without leaky adapter methods (such as `_issue_token`)?

## Decision
1. **Immutable Requesting Space Identity:**
   - Every `PromotionPipeline` instance is bound to an immutable `requesting_space_id` at instantiation, verified via `SpaceKernel.verify_space_identity(requesting_space_id)`.
   - Both `request()` and `approve()` verify that the promotion's `source_space_id` matches `requesting_space_id`. A pipeline in Space B cannot approve promotions originating from Space A (`SPACE-001`, `ARC-004`).
2. **Reuse of Existing Human Gate Authority (`ApprovalManager`):**
   - The promotion pipeline uses the existing `core/space/approver.py::ApprovalManager` attached to `SpaceKernel`.
   - `PromotionPipeline.request()` creates a formal `ApprovalRequest` with `capability="knowledge.promotion"`.
   - `PromotionPipeline.approve()` verifies that `approver_id` matches the authoritative approver returned by `kernel.approval_mgr.get_approver_id(space_id)`.
   - Mere string non-emptiness is NOT authentication: unknown, unauthenticated, or unauthorized approver IDs are rejected.
   - The decision is atomically resolved and consumed via `kernel.approval_mgr.consume_approval()`, verifying the cryptographic `decision_signature`.
3. **Encapsulated Capability Token (`PromotionAuthorization`):**
   - Upon successful approval consumption, `PromotionPipeline` constructs a `PromotionAuthorization` capability token:
     `(promotion_id, knowledge_id, source_space_id, approver_id, approval_request_id, signature, issued_at)`.
   - The signature is generated using the Kernel's decision signing key (`kernel.approval_mgr.get_decision_signing_key(space_id)`).
   - `SpaceMemoryProtocol.store_knowledge(entry, auth)` requires this token.
   - The memory adapter verifies the HMAC signature, asserts that `auth.knowledge_id == entry.knowledge_id` and `auth.source_space_id == entry.source_space_id`, and enforces single-use execution via consumed ID tracking (`_consumed_promotions`).
   - `PromotionPipeline` has zero references to private adapter methods; `_issue_token()` is completely eliminated.
4. **Anti-Tampering and Forgery Defense:**
   - A blake2b hash of the `ExperienceRecord` is computed at `request()` and verified at `approve()` to detect post-evaluation tampering.
   - An out-of-band `knowledge.promotion.approved` Pulse injected on the bus is audited and rejected by `PromotionPipeline.handle_unauthorized_approved_pulse()`, emitting a `knowledge.promotion.rejected` audit Pulse.

## Alternatives Considered
- **Private adapter token issuance (`_issue_token`):** Rejected. Leaked private implementation details across the protocol boundary.
- **Treating `approver_id != ""` as authorization:** Rejected. An identifier is identity data, not cryptographic proof of authorization.
- **Global / Unbound PromotionPipeline:** Rejected. Allowed cross-space confusion attacks.

## Consequences
- Global knowledge cannot be written directly by any actor; only an authorized `PromotionPipeline` lifecycle can generate a valid `PromotionAuthorization`.
- Promotion approvals are single-use, non-repayable, and bound to the originating Space.
- Human gate governance is unified under `SpaceKernel.approval_mgr`.

## Date
2026-09-23

