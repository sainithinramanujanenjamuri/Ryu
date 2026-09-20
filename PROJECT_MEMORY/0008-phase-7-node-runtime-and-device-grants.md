# RYU AI — Project Memory

## Entry 0008 — Phase 7 Node Runtime, Device Grants, and Rust Runtime Bridge

**Date:** 2026-09-20  
**Phase:** 7 — Node Runtime, Device Grants, and Rust Runtime Bridge  
**Status:** COMPLETE (PHASE 7 GATE: PASS)  
**Previous Baseline:** `22c7f44` (Phase 6)

---

### Summary

Phase 7 establishes the deterministic, secure Node Runtime boundary underneath the existing Worker Runtime under the Space-Centric Cognitive Architecture (SCCA). The central governing invariant of Phase 7 is:
> **Nodes and devices provide capability, but possession of a node or device does not grant authority. Workers execute through Node Runtime only with an authoritative, space-scoped Device Grant backed by a valid Resource Manager Lease.**

The complete authority chain is preserved:
```text
Human Goal
    ↓
Space Orchestrator
    ↓
Team / Agent
    ↓
CapabilityRequest
    ↓
Admission Control (Space Kernel: Budget & Policy Authority)
    ↓
Resource Manager (Authoritative Resource Allocator)
    ↓
Lease (Authoritative Concurrency Token)
    ↓
Device Grant (Derived Execution Credential: Space-, Capability-, Session-Scoped)
    ↓
Node Runtime (Device-Side Grant Verification Boundary)
    ↓
Device Binding (Worker ↔ Device)
    ↓
Rust Platform Adapter (ryu-node: OS/Hardware Inspection & Controlled Execution)
    ↓
Physical Device / OS Hardware
```

Neither the Node Runtime, the Rust binary, nor the Worker can bypass this chain to claim independent authority.

---

### Approved Corrections Incorporated

1. **DeviceGrantManager as Credential Materializer:**
   - `DeviceGrantManager` strictly materializes and manages `DeviceGrant` credentials *only after* authoritative approval from Admission Control and `ResourceManager`.
   - It cannot independently authorize capabilities, bypass budgets, or allocate resources.
   - Dual invalidation flow: lease revocation automatically invalidates the derived grant.
2. **Strengthened HMAC Trust Scope & Disconnected-Node Revocation:**
   - `node_pairing_secret` HMAC-SHA256 constant-time comparison (`hmac.compare_digest` in Python, `hmac::Mac::verify_slice` in Rust).
   - Strict canonical payload format protecting all security-sensitive fields:
     `grant_schema_version | grant_id | space_id | worker_id | node_id | device_id | capability | lease_token | nonce | issued_at | expiry | risk_tier`.
   - Reconnection reconciliation: grants whose backing lease was revoked while the node was offline are deterministically rejected upon reconnection.
3. **Contract Status Traceability:**
   - `NODE-001` through `NODE-008` progressed through `CONTRACTED` to `GATE_VERIFIED` backed by executable test evidence.
4. **Observed System Decisions:**
   - Security and chaos gates require explicit observed system decisions (`REJECT`, `BLOCKED`, `FAIL & ESCALATE`, `PULSE WARNING`), distinguishing test execution status from system decisions.
5. **Rust Bridge Crash Recovery Boundaries:**
   - Bridge subprocess restart is strictly transport/infrastructure recovery, never authority restoration.
   - Idempotent/replay-safe tasks resume only after lease, grant, device, and checkpoint verification.
   - Non-idempotent or indeterminate tasks are marked `INDETERMINATE` and escalated to the Space Orchestrator without auto-replay.

---

### Architecture & ADR Foundations

- **ADR-0017:** `adr/0017-node-runtime-authority-boundary.md` — Defines Node Runtime as execution boundary; canonical authorities preserved; non-authority of NodeRegistry.
- **ADR-0018:** `adr/0018-device-grant-lifecycle-and-lease-integration.md` — Derived credential model, 5 grant states (`GRANTED`, `BOUND`, `RELEASED`, `REVOKED`, `EXPIRED`), dual invalidation flow.
- **ADR-0019:** `adr/0019-rust-runtime-bridge-security-boundary.md` — 6 narrow typed subcommands (`inspect`, `inspect-devices`, `validate-grant`, `bind`, `release`, `health`, `audit-verify`); structural exclusion of arbitrary execution; transport-only crash recovery.
- **ADR-0020:** `adr/0020-platform-adapter-windows-host-and-linux-wsl2.md` — Platform adapter boundary; Windows host MVP physical target vs Linux/WSL2 development profile.

---

### What Was Built

1. **Rust Node Runtime (`node_runtime/`):**
   - `ryu-node-proto`: Canonical wire types (`NodeInfo`, `DeviceInfo`, `NodeCapabilityGrant`, `DeviceBindingRequest`, `DeviceBindingResponse`, `HealthReport`, `AuditEntry`).
   - `ryu-node`: Native binary implementing:
     - Constant-time HMAC-SHA256 signature verification (`grant.rs`).
     - SHA-256 hash-chained append-only tamper-evident audit logger (`audit.rs`).
     - Platform discovery for Windows host (DirectX, CPU, RAM) and Linux/WSL2 (`platform.rs`).
     - 6 narrow typed subcommands via CLI binary (`main.rs`).
2. **Python Node Subsystem (`node/`):**
   - `node/contract.py`: Enums (`NodeState`, `DeviceState`, `GrantState`, `DeviceType`, `RiskTier`), dataclasses, and error hierarchy.
   - `node/registry.py`: `NodeRegistry` storing canonical inventory; syncs capacity to `ResourceManager` without holding authority.
   - `node/grants.py`: `DeviceGrantManager` credential materializer with dual invalidation flow; emits `node.capability.granted` and `node.capability.revoked`.
   - `node/runtime.py`: `NodeRuntime` device-side execution and grant enforcement boundary; in-flight revocation (`NODE-004`), exclusive device binding, and local audit logging.
   - `node/coordinator.py`: `NodeCoordinator` heartbeat tracking, offline task checkpointing (`NODE-006`), anti-flapping throttling, post-reconnect reconciliation, and validated resume (`NODE-007`).
   - `node/bridge.py`: `RustNodeBridge` typed subprocess adapter for the 6 allowed native subcommands.
   - `node/audit.py`: `DeviceAuditLog` append-only SHA-256 hash-chained audit logger (`NODE-008`).
3. **Worker Integration (`workers/node/`):**
   - `workers/node/worker.py`: `NodeWorker` executing device-bound tasks under `BaseWorker` lifecycle with mandatory lease and grant checks.
4. **Executable Evidence:**
   - Unit tests (`node/tests/`): 30 unit tests across all models, registry, grants, bindings, coordinator, and audit.
   - Worker tests (`workers/tests/test_node_worker.py`): verified `NodeWorker`.
   - Harness contracts (`harness/cases/node/test_node_future.py`): activated and verified `NODE-001` through `NODE-008`.
   - Adversarial security suite (`harness/cases/node/test_node_security_adversarial.py`): all 22 attack vectors verified with exact expected decisions (`REJECT`, `BLOCKED`, `FAIL & ESCALATE`, `PULSE WARNING`).
   - Chaos suite (`harness/cases/node/test_node_chaos.py`): all 9 chaos failure scenarios verified.
   - E2E pipeline (`harness/cases/node/test_e2e_node_pipeline.py`): full end-to-end integration verified.
