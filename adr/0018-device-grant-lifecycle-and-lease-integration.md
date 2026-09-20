# ADR-0018: Device Grant Lifecycle and Lease Integration

## Context
A mechanism is required to authorize workers to execute capabilities on remote or local node devices (such as GPUs, CPUs, or local storage partitions). The authorization must be tied to the existing `ResourceManager` lease subsystem without creating competing grant or lease authorities.

## Decision
1. **Derived Credential Model:** A `DeviceGrant` is a derived execution credential backed by an authoritative `ResourceManager` lease. It cannot outlive, supersede, or resurrect its backing lease.
   ```text
   Authoritative Lease Invalid → Derived DeviceGrant Invalid → Device Binding Invalid
   ```
2. **Role of DeviceGrantManager:** `DeviceGrantManager` is a credential materializer, not an independent authority. It materializes a signed `DeviceGrant` only after `AdmissionControl` and `ResourceManager` have issued an authoritative lease.
3. **Five Deterministic States:**
   - `GRANTED`: Issued against an active lease, signed, awaiting worker binding.
   - `BOUND`: Actively bound to a worker execution session on the physical node.
   - `RELEASED`: Normal completion, lease released.
   - `REVOKED`: Revoked mid-call or prior to execution; work terminated.
   - `EXPIRED`: Lease or grant TTL elapsed.
4. **Dual Invalidation Flow:**
   - Top-Down: Lease expiration/revocation in `ResourceManager` immediately invalidates the derived `DeviceGrant` and active binding.
   - Bottom-Up: Revocation requests from `DeviceGrantManager` are forwarded to `ResourceManager.revoke()`, which makes the authoritative decision.

## Alternatives Considered
- *Independent Grants:* Granting device access without a `ResourceManager` lease. Rejected because it bypasses concurrency control and creates race conditions over exclusive hardware.
- *Complex Multi-Phase State Machine (REQUESTED, PREPARED, etc.):* Rejected because grant issuance is a synchronous transaction following lease validation.

## Consequences
- Single source of truth for resource ownership (`ResourceManager`).
- Deterministic grant lifecycle with zero zombie or orphaned bindings.
- Revocation mid-call terminates execution on the node immediately (`NODE-004`).

## Date
2026-09-20
