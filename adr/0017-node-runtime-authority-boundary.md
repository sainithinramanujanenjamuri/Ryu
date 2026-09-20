# ADR-0017: Node Runtime Authority Boundary

## Context
Phase 7 extends RYU AI's Space-Centric Cognitive Architecture (SCCA) to physical devices and nodes. The execution chain now flows from Agents through Workers into the Node Runtime and underlying hardware devices. Without explicit architectural boundaries, the Node Runtime or Node Registry could inadvertently become a secondary authority capable of minting leases, bypassing Admission Control, or violating Space isolation.

## Decision
1. **Node Runtime as Execution Boundary Only:** The Node Runtime is an execution and isolation boundary, never an authority boundary.
2. **Canonical Authorities Preserved:**
   - Space Kernel remains the primary authority for space boundary, lifecycle, and budget.
   - Admission Control remains the capability permission gate.
   - ResourceManager & LeaseManager remain the sole authority for resource allocation and concurrency leases.
   - Space Orchestrator remains the planner and reconciler.
3. **NodeRegistry Role:** NodeRegistry is the canonical source of node and device presence/state, but possesses zero authority to allocate resources, mint leases, issue grants, or bypass admission control.
4. **Device Access Flow:** A worker may access a node device only when backed by an authoritative `ResourceManager` lease that has been materialized into a cryptographically signed `DeviceGrant`. Possession of a device ID alone confers zero access rights.

## Alternatives Considered
- *Independent Node Allocation:* Allow nodes to manage their own local resource queues and leases. Rejected because it fragments concurrency control and breaks Space budget enforcement.
- *Direct Worker-to-Device Access:* Allow Workers to directly invoke platform APIs without Node Runtime grant checks. Rejected because it bypasses device-side isolation and independent audit logging.

## Consequences
- Every device operation requires a verified `DeviceGrant` tied to an active `ResourceManager` lease.
- NodeRegistry cannot independently create or grant capabilities.
- Space isolation is strictly maintained across all physical nodes.

## Date
2026-09-20
