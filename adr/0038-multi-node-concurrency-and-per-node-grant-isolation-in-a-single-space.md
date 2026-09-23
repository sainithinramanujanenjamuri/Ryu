# ADR-0038: Multi-Node Concurrency and Per-Node Grant Isolation in a Single Space

## Context
Complex goals frequently demand that a single Space coordinate workloads across multiple heterogeneous execution nodes simultaneously. For instance, a Space may lease a GPU device on `node-win-01` for model acceleration while concurrently executing compile tasks on `node-linux-01`.

In distributed systems, multi-node coordination often risks accidental cross-node authority leakage, cascading failures, or components inventing their own authorization logic. SCCA Laws 1, 2, and 6 dictate that:
1. Everything belongs to a Space.
2. Capabilities are requested, never owned.
3. Failures are contained, escalated, and never silent.

## Problem
1. How does a single Space orchestrate across multiple nodes concurrently without permitting cross-node grant tampering or unauthorized lease sharing?
2. How do we ensure that failure of Node A does not cascade to Node B, nor grant Node B any unauthorized authority or task inheritance?
3. How do we prevent `NodeCoordinator` or `DeviceGrantManager` from becoming alternative authority roots or bypassing the Space Kernel?

## Decision
1. **Downstream Coordination Authority:** `NodeCoordinator` and `DeviceGrantManager` are strictly downstream orchestration and execution boundaries. They possess no independent capability admission, lease creation, or plan mutation authority. All authority flows strictly from Space Kernel → `AdmissionController` → `ResourceManager`.
2. **Independent Per-Node Leases and Grants:**
   - Resource leases are keyed to `ResourceIdentity("node_capability", node_id, device_id)`.
   - `DeviceGrant`s are cryptographically bound via HMAC-SHA256 to the specific target node's shared secret and contain `grant.node_id`. A grant issued for Node A is rejected by Node B on the wire and on the device.
3. **Isolated Fault Containment & Zero Authority Inheritance:**
   - Node disconnections are swept independently. If Node A goes `OFFLINE`, its in-flight tasks are checkpointed into `TaskCheckpoint`. Node B continues operating with uninterrupted heartbeats and active leases.
   - Node B does NOT inherit Node A's grants, leases, or capability scope.
   - Any reassignment of tasks from Node A to Node B must be explicitly proposed and committed through the Space Kernel CAS re-planning path.

## Invariants
```text
Node A authorization ≠ Node B authorization
Node A lease ≠ Node B lease
Node A failure ≠ Node B authority escalation
NodeCoordinator ≠ Authority Root
DeviceGrantManager ≠ Authority Root
```

## Consequences
- Single Spaces can safely scale horizontally across arbitrary numbers of nodes.
- Total failure isolation between distinct nodes.
- Deterministic plan recovery without uncoordinated failover or privilege escalation.

## Date
2026-09-23

