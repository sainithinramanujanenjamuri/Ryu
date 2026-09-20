# RYU AI — Project Memory

## Entry 0009 — Phase 8 CLI Channel and Human Gates

**Date:** 2026-09-20  
**Phase:** 8 — CLI Channel + Human Gates  
**Status:** COMPLETE (PHASE 8 GATE: PASS)  
**Previous Baseline:** `a909821` (Phase 7)

---

### Summary

Phase 8 establishes the authoritative human-in-the-loop authority boundary of RYU AI under the Space-Centric Cognitive Architecture (SCCA). The central governing invariant of Phase 8 is:
> **Human intent defines goals and bounds; Ryu organizes execution. No capability dispatch, high-risk grant, budget expansion, or taint clearance may occur without verified human authorization when required. An authenticated human decision is deterministic, cryptographically signed, immutable, and strictly bound to its Space and plan version.**

The complete human authority flow is:
```text
Human Goal
    ↓
Space Orchestrator (Sequencer & Backpressure Monitor)
    ↓
Space Kernel (Admission Control + Plan Version Authority)
    ↓
Attention Budget (Priority Queuing & Concurrency Bound N<=3)
    ↓
Approval Manager (CAS Lifecycle & Decision Signing Authority)
    ↓
CLI Channel (Interactive TTY vs Tainted Untrusted Relay)
    ↓
Human Operator (token-hmac-v1 Signature Verification)
    ↓
PostgreSQL Approvals / Audit Store (Immutable Trigger Enforced)
    ↓
Atomic Capability Admission & Single-Use Consumption
```

---

### Architectural Invariants & Key Implementations

1. **Separation of Approval Lifecycle vs Attention Queue State (ADR-0022, ADR-0025):**
   - **Approval Lifecycle State:** `PENDING`, `APPROVED`, `DENIED`, `EXPIRED`, `HELD`, `CONSUMED`.
   - **Attention Queue State:** `ACTIVE`, `QUEUED`, `RESOLVED`.
   - Guaranteed single-use CAS transitions (`ApprovalStore.transition_cas`) eliminating race conditions and double-consumption.
2. **Wire Protocol `token-hmac-v1` (ADR-0023):**
   - Canonical 9-field pre-image format.
   - Constant-time HMAC-SHA256 signature verification (`hmac.compare_digest`).
   - Clock-skew window strictly bounded to $\pm 60$ seconds.
   - Nonce replay protection via atomic consumption in durable store.
   - SecretStore / SecretRef integration: raw token secrets never enter PostgreSQL.
3. **Tamper-Evident Decision Integrity Signatures (ADR-0022):**
   - Every resolved approval is cryptographically signed using the Space Kernel internal secret (`kernel_hmac_secret`).
   - Tampered records fail validation during capability admission check.
4. **Attention Budget & Prioritized Queuing (ADR-0025):**
   - Configurable bound $N$ (default $N=3$) of concurrent active approvals per Space.
   - 3 Priority Classes: Class 1 (Budget/Held) > Class 2 (Standard) > Class 3 (Low).
   - Space Orchestrator queries `is_attention_saturated()` to pause dispatch during human cognitive saturation.
5. **Untrusted Relay Boundary & Taint Tagging (ADR-0021):**
   - Real interactive terminals (`isatty() == True`) produce clean inputs (`taint: false`).
   - Piped, redirected, or automated relays produce tainted inputs (`taint: true`).
6. **Audit Immutability (REC-006):**
   - PostgreSQL trigger `trg_pulses_immutable` prohibits `UPDATE` and `DELETE` on the `pulses` table.
7. **Authoritative Pulse Emission:**
   - Only `ApprovalManager` can publish `security.grant.approved`.
   - Enforced by `PulseValidator` at the durable bus boundary.

---

### ADRs Authored

- `adr/0021-cli-channel-and-untrusted-relay-boundary.md`
- `adr/0022-human-gate-lifecycle-and-cas-transitions.md`
- `adr/0023-authenticated-approver-identity-and-delegation.md`
- `adr/0024-approval-and-admission-control-integration.md`
- `adr/0025-human-attention-budget-and-queuing.md`

---

### Executable Evidence Summary

- **Channels Unit Tests:** `channels/tests/` (26 passed, 1 skipped)
- **Adversarial Security Suite (22 vectors):** `harness/cases/cli/test_adversarial_security.py` (21 passed, 1 skipped)
- **Chaos Failures Suite (9 scenarios):** `harness/cases/cli/test_chaos_approvals.py` (9 passed)
- **E2E Contracts (CLI-001..008, HUMAN-001..005, REC-006):** `harness/cases/cli/test_cli_e2e.py` (13 passed, 1 skipped)
- **Core Unit / Integration Tests:** all existing tests pass with zero regressions.
- **Dependency Guard:** `scripts/dep_guard.py` PASS (`core/` strictly isolated from `channels/`).
- **Phase 8 Gate Status:** PASS.

