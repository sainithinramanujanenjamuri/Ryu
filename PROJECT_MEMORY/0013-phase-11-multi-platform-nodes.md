# RYU AI — Project Memory

## Entry 0013 — Phase 11 Multi-Platform Nodes & Concurrency

**Date:** 2026-09-23  
**Phase:** 11 — Multi-Platform Nodes  
**Status:** COMPLETE (PHASE 11 GATE: PASS)  
**Previous Baseline:** Phase 10 (Space Memory & Adaptation Loop) — commit `8c5fe3a`

---

### Summary

Phase 11 generalizes the RYU AI Node Runtime beyond the initial Windows physical-device MVP without weakening any shared SCCA boundaries, authority hierarchies, or cryptographic guarantees.

1. **Linux Platform Profiles & WSL2 Separation (ADR-0037, NODE-009):**
   - Introduced `NodePlatformProfile` protocol with concrete implementations:
     - `WindowsHostProfile`: native Windows host execution profile.
     - `LinuxHostProfile` (`"linux_native"`): native bare-metal / dedicated Linux host profile (`evidence_type="Native Linux host"`).
     - `WSL2Profile` (`"wsl2"`): Windows-hosted Linux virtualized environment (`evidence_type="Linux compatibility validation via WSL2"`).
   - **Critical Invariant:** `WSL2 != Native Linux Device`. WSL2 provides Linux compatibility validation; it is never claimed as proof of native Linux hardware independence.
   - Second platform target (Linux profile) passes the entire Phase 7 contract suite (`NODE-001` through `NODE-008`).

2. **Multi-Node Concurrency in a Single Space (ADR-0038, NODE-010, NODE-011):**
   - Single Space (`space-multi`) successfully coordinates across multiple distinct nodes (`node-win-01` and `node-linux-01`) simultaneously.
   - Independent per-node leases in `ResourceManager`: `ResourceIdentity("gpu", "node-win-01", "gpu-0")` and `ResourceIdentity("cpu", "node-linux-01", "cpu-0")`.
   - Independent cryptographic grants: grants minted for Node A are rejected by Node B on the wire and on the device (`Grant target node mismatch`).
   - Fault containment: revoking a grant or disconnecting Node A checkpoints Node A's tasks while Node B continues uninterrupted execution with healthy heartbeats.
   - **Zero Authority Transfer Invariant:** Node B does NOT inherit Node A's grants, leases, or capability scope upon Node A failure. Reassignment requires Kernel plan proposals.

3. **Restricted Node Tier & MDM Policy Enforcement (ADR-0039, NODE-012):**
   - Introduced `NodeTrustTier` (`FULL_TRUST`, `RESTRICTED`) and `RestrictedNodePolicy`.
   - **Critical Invariant:** `MDM_ALLOW != Authentication`. MDM enforcement is strictly an additional local policy constraint, NOT an authority or admission root.
   - Conjunctive Authorization Invariant:
     ```text
     MDM_ALLOW ∧ valid_DeviceGrant ∧ valid_Space ∧ valid_Node ∧ valid_Lease → binding permitted
     ```
   - Proved that `MDM_ALLOW` alone fails, forged grant with `MDM_ALLOW` fails, cross-space/cross-node with `MDM_ALLOW` fails, and valid grant with `MDM_DENY` fails.
   - Native Rust CLI binary (`ryu-node bind --trust-tier restricted --policy ...`) directly enforces local allow-lists and appends tamper-evident SHA-256 hash-chained `BIND_DENIED` audit records.

4. **Future Platform Placeholders & Cargo Feature Flags (NODE-013):**
   - Added Cargo feature flags in `node_runtime/crates/ryu-node/Cargo.toml`: `macos`, `android`, `ios`, `rpi_gpio`.
   - Typed Python stubs in `node/platforms/stubs.py` (`MacOSProfile`, `AndroidProfile`, `IOSProfile`, `RaspberryPiProfile`) declare `is_supported = False` and raise `NotImplementedError("spec §11 — Post-v1 platform")`.
   - **Critical Invariant:** `feature compilation != platform support`. Compilation proves syntactic and interface conformance without claiming platform support.

5. **Downstream Authority Invariants:**
   - Positioned `NodeCoordinator` and `DeviceGrantManager` strictly downstream of `Space Kernel`, `AdmissionController`, and `ResourceManager`.
   - Proved that node coordination components cannot grant capabilities, create authoritative leases, or modify Kernel plans.

---

### Verification Summary

- **Phase 11 Unit Tests (`node/tests/`):** 44 passed (30 baseline + 14 new Phase 11 tests).
- **Node Harness Suite (`harness/cases/node/`):** 52 passed (all Phase 7 + Phase 11 contracts green).
- **Rust Node Workspace:** `cargo check --manifest-path node_runtime/Cargo.toml --all-features` PASS.
- **Cargo Build:** `cargo build --manifest-path node_runtime/Cargo.toml` PASS.
- **Full Repository Test Suite:** 626 passed, 11 skipped, 0 failed.
- **Static Analysis & Guards:** `contract_sync.py`, `dep_guard.py`, `ruff`, and `mypy` all PASS cleanly.

