# ADR-0024: Approval and Admission Control Integration

## Context
Phase 2 established Admission Control as the pre-dispatch gatekeeper for budget enforcement. Phase 8 integrates human approval gates with Admission Control, ensuring no capability dispatch occurs without verified human authorization when required.

## Decision
1. **Pre-Dispatch Verification:**
   - Prior to dispatching capability requests, the Space Kernel checks both budget and human approval requirements.
   - High-risk operations (device access, filesystem writes, execution, network binds), tainted contexts, or explicitly gated operations require a valid `ApprovalRequest`.
2. **Context and Lineage Binding:**
   - An approval is strictly bound to:
     - `plan_version`: If the Space plan version has bumped since the approval was issued, the approval is invalidated.
     - `capability_request_hash`: The SHA256 digest of the canonical capability request must match.
     - `status == "approved"`.
     - `consumed_at is None`.
3. **Atomic Consumption:**
   - When admission passes, the approval is atomically transitioned to `CONSUMED` with a timestamp, preventing reuse.
4. **Authoritative Event Emission:**
   - Only the `ApprovalManager` (`core/space/approver.py`) is authorized to publish `security.grant.approved` and `security.grant.denied` pulses. The PulseValidator enforces this restriction at the bus level.

## Alternatives Considered
- *Allowing workers to verify approvals directly:* Rejected because workers operate outside the Space Kernel trust boundary and cannot be trusted with authorization enforcement.
- *Permitting plan version drifting:* Rejected because human approval of a step in Plan v1 cannot be safely applied to an altered Plan v2.

## Consequences
- Single authoritative point of admission in Space Kernel.
- Full provenance and lineage linking goals, plans, human approvals, and execution.

## Date
2026-09-20

