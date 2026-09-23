# ADR-0039: Restricted Node Tier and Device-Side MDM Allow-Lists

## Context
Spec §11 defines two primary node trust tiers:
1. `Full Trust`: Dedicated, personal paired hardware under the user's complete control.
2. `Restricted`: Shared, corporate, or managed hardware subject to Mobile Device Management (MDM) or external security constraints.

On `Restricted` nodes, the local device policy restricts allowable capabilities, filesystem paths, and network interactions regardless of what an individual Space Kernel, agent, or human user requests.

## Problem
1. How does the Node Runtime enforce device-side MDM constraints without allowing MDM to usurp or replace the core authorization and authentication architecture?
2. What is the exact relationship between `DeviceGrant` cryptographic verification, Space isolation, ResourceManager leases, and MDM allow-list evaluation?
3. How do we ensure that a misconfigured or malicious MDM policy cannot grant unauthorized privileges?

## Decision
1. **MDM as a Local Policy Constraint, NOT an Authentication Root:**
   - MDM policy enforcement is strictly an *additional local constraint*. It does not authenticate a `DeviceGrant`, establish Space membership, establish node identity, create a lease, or authorize a capability by itself.
2. **Conjunctive Authorization Invariant:**
   ```text
   MDM_ALLOW ∧ valid_DeviceGrant ∧ valid_Space ∧ valid_Node ∧ valid_Lease → binding permitted
   ```
   - `MDM_ALLOW alone` → **NOT sufficient**.
   - `valid_DeviceGrant alone` → **NOT sufficient** on a Restricted node if MDM denies the capability.
   - `MDM_DENY` → **binding denied**.
3. **Execution Order of Operations (`bind_device`):**
   ```text
   1. Structural validation
           ↓
   2. Target Node validation (grant.node_id == self.node_id)
           ↓
   3. Space membership validation (grant.space_id == caller_space_id)
           ↓
   4. DeviceGrant cryptographic verification (HMAC-SHA256 constant time)
           ↓
   5. MDM / Restricted-node policy evaluation (local allow-list / deny-list)
           ↓
   6. Resource lease / binding validation (ResourceManager lease token active)
           ↓
   7. Hardware device binding (State -> BUSY)
           ↓
   8. Local append-only audit logging (SHA-256 chained record)
   ```
4. **Denial Semantics:**
   - When an un-allowlisted capability is requested on a `Restricted` node, `NodeRuntime` raises `GrantInvalidError`, emits an audited `BIND_DENIED: MDM policy restriction` record in the local SHA-256 hash-chained log, and propagates a `terminal.permission_denied` failure to the caller.

## Invariants
```text
MDM_ALLOW ≠ Authentication
MDM_DENY → Binding Denied
Device sovereignty: Space Kernel cannot bypass device-local MDM restrictions
Authority containment: MDM policy cannot create capabilities without a valid DeviceGrant and Space lease
```

## Consequences
- Managed and enterprise nodes can be paired securely into RYU Spaces while respecting enterprise security constraints.
- Complete defense against rogue Space Kernels attempting unauthorized device actions.
- Audited, tamper-evident record of all policy violations on the device.

## Date
2026-09-23

