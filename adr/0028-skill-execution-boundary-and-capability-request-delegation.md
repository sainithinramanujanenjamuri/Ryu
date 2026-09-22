# ADR-0028: Skill Execution Boundary and Capability Request Delegation

## Status
Accepted

## Context
SCCA establishes a clear execution hierarchy:
Runtime → Spaces → Execution Plans → Teams → Agents → Workers → Skills → Memory → Insights.
In Phase 9, intelligence packages (Skills) are introduced. A critical architectural danger in agent systems is allowing Skills or tools to directly execute privileged actions, allocate physical resources, or hold security credentials.

## Problem
1. Does a Skill own execution authority?
2. Can a Skill bypass Space Admission Control to invoke tools directly?
3. How do Skills interact with Workers, Resource Leases, and Sandboxes?
4. How is authority prevented from leaking into external tool packages?

## Decision
1. **Zero Authority Ownership:** A Skill is strictly a capability interface and higher-level cognitive package; it is NOT an authority boundary. A Skill owns no permissions, holds no execution privileges, and possesses no direct access to the operating system or network.
2. **Capability Request Delegation:** When a Skill needs to perform an operation (e.g. read a file, run a query, or call an MCP tool), it MUST formulate a typed `CapabilityRequest` and submit it through its parent Worker to the Space Kernel's `AdmissionController`.
3. **Pre-Dispatch Evaluation:** `AdmissionController` evaluates the `CapabilityRequest` against Space budgets, spend policies, and risk tiers before any worker or tool touches resources. If the capability is classified as `high-risk`, admission fails closed unless an active `security.grant.approved` human approval exists.
4. **Worker-Mediated Sandbox Execution:** Upon admission, the Worker obtains a resource `Lease` from `ResourceManager`, configures the `SandboxManager` (Windows Job Objects / Linux Seccomp & namespaces), and executes the capability within OS-level isolation. The Skill never spawns unmanaged processes directly.
5. **Passive Data Results:** All outputs returned from capability execution are treated strictly as passive data (`output_data`). Output is stamped `taint: True` by default and cannot directly trigger unverified state transitions or capability grants.

## Alternatives Considered
- **Direct Skill Execution:** Allowing Skills to execute system libraries directly. Rejected because it bypasses Admission Control, budget tracking, and OS sandboxing.
- **Skill-Scoped Capability Ownership:** Giving Skills pre-granted permanent permissions. Rejected because it violates SCCA Law 2 ("Capabilities Are Requested, Never Owned").

## Consequences
- Skills remain completely portable, deterministic, and sandboxed.
- All execution requests are auditable via `worker.tool.called`, `worker.tool.succeeded`, and `worker.tool.failed` Pulses.
- Even if a Skill contains malicious prompt injection or unintended logic, it cannot execute any action without Space Kernel admission and sandbox containment.

## Date
2026-09-22

