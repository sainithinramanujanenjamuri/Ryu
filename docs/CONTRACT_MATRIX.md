# RYU AI — Contract Matrix

**Status:** Active
**Version:** 1.0
**Purpose:** Map every architectural contract to its machine-readable specification, implementation boundary, executable verification, and evidence state.

---

## 1. Purpose

`CONTRACT_MATRIX.md` is the verification index for the RYU AI Space-Centric Cognitive Architecture (SCCA).

It connects:

```text
Frozen Architecture
        ↓
Contract / Invariant
        ↓
Machine-Readable Contract
        ↓
Implementation Boundary
        ↓
Harness Case
        ↓
Evidence
        ↓
Roadmap Gate
```

This document does **not** replace `docs/architecture.md`.

`docs/architecture.md` remains the frozen architectural authority.

The matrix answers:

> **What must be true, where is it defined, where is it implemented, and what executable evidence proves it?**

---

# 2. Contract Status Model

A contract must not be considered "done" merely because code exists or compilation succeeds.

Use the following evidence states:

| Status                 | Meaning                                                                           |
| ---------------------- | --------------------------------------------------------------------------------- |
| `SPECIFIED`            | Contract is explicitly defined by the architecture/specification.                 |
| `CONTRACTED`           | Machine-readable contract exists where applicable.                                |
| `IMPLEMENTED`          | Required implementation exists.                                                   |
| `UNIT_VERIFIED`        | Focused unit tests pass.                                                          |
| `INTEGRATION_VERIFIED` | Cross-component behavior is verified.                                             |
| `CHAOS_VERIFIED`       | Failure/recovery/concurrency behavior is verified under injected faults or races. |
| `SECURITY_VERIFIED`    | Security-sensitive invariants have executable regression coverage.                |
| `GATE_VERIFIED`        | The corresponding ROADMAP exit-gate criterion is proven.                          |

A contract may have multiple states simultaneously.

The highest achieved state must be supported by executable evidence.

---

# 3. Evidence Rules

The following rules apply to every contract.

1. Compilation is not proof of architectural correctness.
2. A passing unit test does not automatically prove integration behavior.
3. A feature exists only when its required harness case passes and `harness/spec_map.yaml` maps it to the corresponding architecture acceptance criterion.
4. Every executable acceptance criterion must have a traceable harness case.
5. Every harness case must identify the architecture requirement it proves.
6. Security claims require executable security regression tests.
7. Concurrency and recovery claims require race/chaos testing where specified.
8. "Implemented" must never be reported as "verified" without evidence.
9. Skipped future-phase cases are not passing cases.
10. Contract changes require regeneration and contract-sync verification.
11. Architecture changes require the ADR process.

---

# 4. Global Architecture Contracts

| ID      | Contract                                                        | Architecture Source              | Implementation Boundary               | Harness / Evidence                       | Roadmap     | Status      |
| ------- | --------------------------------------------------------------- | -------------------------------- | ------------------------------------- | ---------------------------------------- | ----------- | ----------- |
| ARC-001 | Everything happens inside a Space.                              | Architecture §4 / Law 1          | Space Kernel / Space boundary         | `test_space_isolation.py`            | Phase 2     | `GATE_VERIFIED` |
| ARC-002 | Capabilities are requested, never owned.                        | Architecture §4 / Law 2          | Admission Control / CapabilityRequest | `test_kernel_admission.py`           | Phase 2     | `GATE_VERIFIED` |
| ARC-003 | Components communicate through Pulses.                          | Architecture / Law 3             | Pulse Bus                             | Pulse contract suite                     | Phase 1     | `SPECIFIED` |
| ARC-004 | Knowledge belongs to the Space first.                           | Architecture §4 / Law 4          | Space Memory / Promotion pipeline     | Knowledge promotion tests                | Phase 10    | `SPECIFIED` |
| ARC-005 | Humans define goals; RYU organizes execution.                   | Architecture / Law 5             | Orchestrator / Human Channel          | Full-loop tests                          | Phase 4 / 8 | `GATE_VERIFIED` |
| ARC-006 | Failures are contained and escalated, never silently swallowed. | Architecture / Law 6             | All runtime boundaries                | Failure taxonomy / chaos suite           | Phase 3+    | `SPECIFIED` |
| ARC-007 | Deterministic core has no dependency on LLM-bearing layers.     | Architecture / dependency rules  | `core/`                               | Dependency guard + core-independence job | Phase 0 / 4 | `GATE_VERIFIED` |
| ARC-008 | Architecture document is frozen except through ADR process.     | Architecture changelog           | `docs/architecture.md`, `adr/`        | ADR review evidence                      | All         | `SPECIFIED` |
| ARC-009 | Replay is a first-class debugging capability.                   | Architecture / Roadmap principle | Pulse Store / LLM Recorder / Replay   | Replay suite                             | Phase 1+    | `SPECIFIED` |
| ARC-010 | No silent failure, denial, timeout, or backpressure path.       | Architecture / Law 6             | Runtime-wide                          | Failure/backpressure harness             | Phase 3+    | `SPECIFIED` |

---

# 5. Space Boundary Contracts

| ID        | Contract                     | Required Invariant                                                                   | Implementation          | Harness                     | Roadmap      | Status      |
| --------- | ---------------------------- | ------------------------------------------------------------------------------------ | ----------------------- | --------------------------- | ------------ | ----------- |
| SPACE-001 | Space isolation              | Space A cannot access Space B's memory without an authorized promotion path.         | Space Kernel / Memory   | `test_space_isolation.py`               | Phase 2 / 10 | `GATE_VERIFIED`        |
| SPACE-002 | Space resource isolation     | Resource grants are scoped to the owning Space.                                      | Resource Manager        | `test_space_future.py` (`test_space_resource_isolation`) | Phase 3 / 7  | `GATE_VERIFIED`        |
| SPACE-003 | Space agent isolation        | Agent state/subscriptions cannot cross Space boundary without defined authorization. | Space / Agent boundary  | Isolation suite                         | Phase 2 / 4  | `GATE_VERIFIED`        |
| SPACE-004 | Space artifact isolation     | Artifacts remain scoped to their Space unless explicitly promoted/exported.          | Artifact / Memory layer | Isolation suite                         | Phase 2+     | `SPECIFIED`            |
| SPACE-005 | Space subscription isolation | Pulse subscriptions cannot observe another Space without authorization.              | Pulse Bus               | `test_misc.py` (`test_space_scoped_retrieval`) | Phase 1 / 2  | `INTEGRATION_VERIFIED` |
| SPACE-006 | Space identity               | Every execution has an unambiguous Space identity.                                   | Space Kernel            | `test_space_isolation.py`, `test_kernel.py` | Phase 2      | `GATE_VERIFIED`        |

---

# 6. Pulse Bus Contracts

| ID        | Contract                     | Required Invariant                                                    | Machine Contract                | Implementation        | Harness                                 | Roadmap     | Status      |
| --------- | ---------------------------- | --------------------------------------------------------------------- | ------------------------------- | --------------------- | --------------------------------------- | ----------- | ----------- |
| PULSE-001 | Typed registry               | Unknown Pulse types are rejected synchronously.                       | `pulse-types.json`              | `PulseBus.publish()`  | `test_registry_rejects_unknown_type.py` | Phase 0     | `UNIT_VERIFIED`        |
| PULSE-002 | Payload validation           | Invalid payloads never enter the log.                                 | `payload-schemas/*.json`        | Pulse Bus validator   | `test_bus_validation.py`                | Phase 0 / 1 | `UNIT_VERIFIED`        |
| PULSE-003 | Pre-publish rejection        | Validation occurs before persistence.                                 | Registry + schemas              | `PulseBus.publish()`  | `test_durable_bus_unit.py`              | Phase 0 / 1 | `UNIT_VERIFIED`        |
| PULSE-004 | Causation                    | `parent_pulse_id` reconstructs causal ancestry.                       | Pulse contract                  | Pulse Store / Replay  | `test_causal_replay.py`                 | Phase 0 / 1 | `INTEGRATION_VERIFIED` |
| PULSE-005 | Correlation                  | Pulses in one execution chain retain `correlation_id`.                | Pulse contract                  | Pulse Bus             | `test_durable_replay.py`                | Phase 0 / 1 | `INTEGRATION_VERIFIED` |
| PULSE-006 | Taint inheritance            | Child Pulse inherits taint from parent chain.                         | Pulse contract                  | `taint.py`            | `test_taint_preservation.py`            | Phase 0 / 1 | `INTEGRATION_VERIFIED` |
| PULSE-007 | Taint clearance              | Clearance applies forward-only and does not mutate historical Pulses. | `security.taint.cleared` schema | Taint manager         | `test_taint_preservation.py`            | Phase 1 / 2 | `INTEGRATION_VERIFIED` |
| PULSE-008 | Durable persistence          | Published Pulses survive process failure.                             | Store interface                 | Postgres-backed store | `test_durable_append.py`                | Phase 1     | `INTEGRATION_VERIFIED` |
| PULSE-009 | Replay                       | A Pulse chain can be reconstructed from persisted state.              | Replay contract                 | `replay.py`           | `test_durable_replay.py`                | Phase 1     | `INTEGRATION_VERIFIED` |
| PULSE-010 | Schema fuzzing               | Invalid mutations are rejected; valid generated Pulses are accepted.  | JSON schemas                    | Generated validators  | `test_schema_fuzz.py`                   | Phase 1     | `UNIT_VERIFIED`        |
| PULSE-011 | Registry/codegen consistency | Generated validators match machine-readable contracts.                | Registry + codegen              | Codegen pipeline      | `test_validator_uses_generated_types.py`| Phase 1     | `UNIT_VERIFIED`        |
| PULSE-012 | Unknown type rejection       | No unregistered type may enter the durable log.                       | Registry                        | Pulse Bus             | `test_registry_rejects_unknown_type.py` | Phase 0     | `UNIT_VERIFIED`        |

---

# 7. Contract Registry

| Contract         | Source of Truth                            | Required Artifact                           | Consumer              |
| ---------------- | ------------------------------------------ | ------------------------------------------- | --------------------- |
| Pulse types      | `contracts/registry/pulse-types.json`      | 38 registered types                         | Pulse Bus / codegen   |
| Pulse payloads   | `contracts/registry/payload-schemas/`      | 38 JSON Schemas                             | Validators / harness  |
| Failure taxonomy | `contracts/registry/failure-taxonomy.json` | transient + terminal classes                | Runtime / Workers     |
| Capability risks | `contracts/registry/capability-risks.json` | Risk tiers                                  | Admission / Registry  |
| Grants           | `contracts/registry/grants.json`           | ResourceGrant / NodeCapabilityGrant / Lease | Kernel / Node Runtime |

Contract consumers must not create conflicting local definitions.

---

# 8. Admission Control Contracts

| ID         | Contract               | Required Invariant                                                             | Implementation       | Harness               | Roadmap     | Status      |
| ---------- | ---------------------- | ------------------------------------------------------------------------------ | -------------------- | --------------------- | ----------- | ----------- |
| KERNEL-001 | Pre-dispatch admission | Every `CapabilityRequest` is checked before dispatch.                          | `admission.py`       | `test_kernel_admission.py`                  | Phase 2     | `GATE_VERIFIED` |
| KERNEL-002 | Hard stop              | Budget exhaustion produces zero downstream dispatches.                         | Admission Control    | `test_kernel_admission.py`                  | Phase 2     | `GATE_VERIFIED` |
| KERNEL-003 | Single escalation      | Exactly one `space.budget.exceeded` occurs per `window_id`.                    | Admission / Windows  | `test_kernel_admission.py`, `test_windows.py` | Phase 2     | `GATE_VERIFIED` |
| KERNEL-004 | Window rotation        | Human acknowledgement or replenishment creates a new `window_id`.              | `windows.py`         | `test_windows.py`                           | Phase 2     | `UNIT_VERIFIED` |
| KERNEL-005 | Approval required      | Soft threshold pauses continuation while allowing declared in-flight behavior. | Admission / Approver | `test_admission.py`                         | Phase 2     | `UNIT_VERIFIED` |
| KERNEL-006 | Degraded mode          | Cheaper models and optional nodes are handled according to policy.             | Admission            | `test_admission.py`                         | Phase 2     | `UNIT_VERIFIED` |
| KERNEL-007 | Attention budget       | N+1 simultaneous approvals pause Team Builder.                                 | `attention.py`       | `test_approvals.py`                         | Phase 2 / 8 | `UNIT_VERIFIED` |

---

# 9. Plan Versioning Contracts

| ID       | Contract                | Required Invariant                                                | Implementation        | Harness           | Roadmap | Status      |
| -------- | ----------------------- | ----------------------------------------------------------------- | --------------------- | ----------------- | ------- | ----------- |
| PLAN-001 | CAS versioning          | Plan updates use authoritative `plan_version`.                    | `plan_store.py`                 | `test_kernel_admission.py`, `test_plan_engine.py` | Phase 2 | `GATE_VERIFIED` |
| PLAN-002 | Deterministic winner    | Concurrent Deltas have one authoritative winner.                  | `plan_store.py`                 | `test_plan_engine.py`                             | Phase 2 | `UNIT_VERIFIED` |
| PLAN-003 | Superseded notification | Losing Delta emits `plan.version.superseded`.                     | `plan_store.py`                 | `test_kernel_admission.py`                        | Phase 2 | `GATE_VERIFIED` |
| PLAN-004 | Rebase                  | Losing valid Delta may rebase according to policy.                | `plan_store.py`                 | `test_plan_engine.py`                             | Phase 2 | `UNIT_VERIFIED` |
| PLAN-005 | In-flight resolution    | Superseded nodes resolve via `finish`, `checkpoint`, or `cancel`. | `inflight_resolve.py`           | `test_plan_engine.py`                             | Phase 2 | `UNIT_VERIFIED` |
| PLAN-006 | Rebase bound            | Replan storms cannot create unbounded CAS livelock.               | `plan_store.py` (ADR-0003)      | `test_plan_engine.py`                             | Phase 2 | `UNIT_VERIFIED` |

---

# 10. Secret Contracts

| ID         | Contract             | Required Invariant                                                | Implementation          | Harness                 | Roadmap     | Status      |
| ---------- | -------------------- | ----------------------------------------------------------------- | ----------------------- | ----------------------- | ----------- | ----------- |
| SECRET-001 | Secret references    | Requests carry `secret://` references, not resolved credentials.  | `secrets.py`            | `test_secrets.py`             | Phase 2     | `UNIT_VERIFIED` |
| SECRET-002 | Late resolution      | Secret resolution occurs at the last possible execution boundary. | Sandbox / `secrets.py`  | `test_secrets.py`, `test_secret_sanitization.py` | Phase 2 / 6 | `GATE_VERIFIED` |
| SECRET-003 | No Pulse leakage     | Resolved secret values never appear in Pulse payloads.            | Validator / persistence | `test_security_containment.py`| Phase 2     | `GATE_VERIFIED` |
| SECRET-004 | No handoff leakage   | Resolved secret values never enter Handoff Notes.                 | Context Manager         | `test_security_future.py`, `test_secret_sanitization.py` | Phase 2 / 5 / 6 | `GATE_VERIFIED` |
| SECRET-005 | No failure leakage   | Secrets cannot appear in failure messages, traces, or logs.       | Runtime / observability / BaseWorker | `test_secret_sanitization.py`, `test_worker_security_adversarial.py` | Phase 2+ / 6 | `GATE_VERIFIED` |
| SECRET-006 | LLM recording safety | Full-call recording must coexist with secret containment.         | LLM Recorder            | Recorder redaction test | Phase 5     | `SPECIFIED`     |

---

# 11. Failure Taxonomy Contracts

| ID       | Contract          | Required Invariant                                                    | Implementation             | Harness                 | Roadmap     | Status      |
| -------- | ----------------- | --------------------------------------------------------------------- | -------------------------- | ----------------------- | ----------- | ----------- |
| FAIL-001 | Timeout           | `transient.timeout` follows retry policy.                             | Runtime / Worker           | `test_fault_matrix.py`  | Phase 3     | `GATE_VERIFIED` |
| FAIL-002 | Rate limit        | `transient.rate_limit` is distinct from `rate.limited` backpressure.  | Runtime / Resource Manager | `test_fault_matrix.py`  | Phase 3     | `GATE_VERIFIED` |
| FAIL-003 | Network           | `transient.network` follows retry policy.                             | Runtime                    | `test_fault_matrix.py`  | Phase 3     | `GATE_VERIFIED` |
| FAIL-004 | Invalid params    | `terminal.invalid_params` does not retry.                             | Runtime                    | `test_fault_matrix.py`  | Phase 3     | `GATE_VERIFIED` |
| FAIL-005 | Permission denied | `terminal.permission_denied` escalates immediately.                   | Admission / Worker         | `test_fault_matrix.py`  | Phase 3 / 4 | `GATE_VERIFIED` |
| FAIL-006 | Budget exceeded   | `terminal.budget_exceeded` follows admission policy.                  | Kernel                     | `test_fault_matrix.py`, `test_admission.py` | Phase 2 | `GATE_VERIFIED` |
| FAIL-007 | Not found         | `terminal.not_found` follows terminal handling.                       | Runtime                    | `test_fault_matrix.py`  | Phase 3     | `GATE_VERIFIED` |
| FAIL-008 | No silent failure | Every failure is surfaced through a typed Pulse or recorded decision. | Runtime-wide               | Failure injection suite | Phase 3+    | `SPECIFIED` |

---

# 12. Idempotency Contracts

| ID       | Contract               | Required Invariant                                                                               | Implementation Boundary     | Harness                   | Roadmap  | Status          |
| -------- | ---------------------- | ------------------------------------------------------------------------------------------------ | --------------------------- | ------------------------- | -------- | --------------- |
| IDEM-001 | Stable idempotency key | Retries reuse the same key.                                                                      | Execution layer             | `test_idempotency_proof.py` | Phase 3  | `GATE_VERIFIED` |
| IDEM-002 | Side-effect uniqueness | A retried side-effecting operation executes once.                                                | Provider/execution boundary | `test_idempotency_proof.py` | Phase 3  | `GATE_VERIFIED` |
| IDEM-003 | Cached response        | Subsequent retries may return stored response without repeating side effect.                     | Provider adapter            | `test_idempotency_proof.py` | Phase 3  | `GATE_VERIFIED` |
| IDEM-004 | Crash recovery         | Process failure after side effect but before response persistence does not duplicate the effect. | Runtime / provider boundary | Crash injection           | Phase 3+ | `SPECIFIED`     |

**Open architectural contract:** Resolved by ADR-0006: the boundary component executing the side effect owns the idempotency guarantee.

---

# 13. Resource Manager Contracts

| ID           | Contract              | Required Invariant                                                 | Implementation   | Harness                                   | Roadmap     | Status          |
| ------------ | --------------------- | ------------------------------------------------------------------ | ---------------- | ----------------------------------------- | ----------- | --------------- |
| RESOURCE-001 | Resource identity     | Resource identity is `(resource_type, provider_id, instance_id)`.  | `identity.py`    | `test_resources_future.py`, `test_identity.py` | Phase 3     | `GATE_VERIFIED` |
| RESOURCE-002 | Lease                 | Resource ownership is represented by a Manager-issued lease.       | `lease.py`       | `test_resources_future.py`, `test_lease.py`    | Phase 3     | `GATE_VERIFIED` |
| RESOURCE-003 | Expiry                | Expired leases cannot continue valid ownership.                    | Lease Manager    | `test_lease.py`, `test_chaos_scenarios.py`| Phase 3     | `GATE_VERIFIED` |
| RESOURCE-004 | Revocation            | Revoked leases cannot be used.                                     | Lease Manager    | `test_lease.py`, `test_manager.py`        | Phase 3 / 7 | `GATE_VERIFIED` |
| RESOURCE-005 | Contention            | Concurrent acquisition cannot double-grant one exclusive resource. | Resource Manager | `test_lease_race.py`                      | Phase 3     | `GATE_VERIFIED` |
| RESOURCE-006 | Queue position        | Losing requests receive accurate `resource.conflict`.              | Queue            | `test_resources_future.py`, `test_lease_race.py` | Phase 3 | `GATE_VERIFIED` |
| RESOURCE-007 | Rate limiting         | Over-limit requests are queued and emit `rate.limited`.            | Rate limiter     | `test_rate_limit.py`                      | Phase 3     | `GATE_VERIFIED` |
| RESOURCE-008 | Backpressure severity | `rate.limited` remains `info`.                                     | Registry         | `test_resources_future.py`                 | Phase 0+    | `GATE_VERIFIED` |

---

# 14. Taint and Prompt-Injection Contracts

| ID        | Contract               | Required Invariant                                               | Implementation       | Harness           | Roadmap     | Status          |
| --------- | ---------------------- | ---------------------------------------------------------------- | -------------------- | ----------------- | ----------- | --------------- |
| TAINT-001 | Boundary taint         | Untrusted external content enters as tainted data.               | Channels / Workers   | `test_workers_future.py`, `test_worker_security_adversarial.py` | Phase 6 / 8 | `GATE_VERIFIED` |
| TAINT-002 | Propagation            | Taint propagates through the causal chain.                       | Pulse Bus            | Taint chain                   | Phase 0 / 1 | `SPECIFIED`     |
| TAINT-003 | Forward-only clearance | Clearance affects future propagation only.                       | Taint manager        | Clearance test                | Phase 1 / 2 | `SPECIFIED`     |
| TAINT-004 | Clearance audit        | Clearance itself is represented by a Pulse.                      | Pulse Bus            | Clearance audit               | Phase 1 / 2 | `SPECIFIED`     |
| TAINT-005 | Grant protection       | Tainted instructions cannot silently produce high-risk grants.   | Admission / Security | `test_security_containment.py`| Phase 2 / 6 | `GATE_VERIFIED` |
| TAINT-006 | Anti-laundering        | Transforming tainted content must not silently erase provenance. | Taint system         | `test_security_future.py`     | Phase 2+    | `GATE_VERIFIED` |

---

# 15. Human Approval Contracts

| ID        | Contract          | Required Invariant                                            | Implementation | Harness             | Roadmap     | Status          |
| --------- | ----------------- | ------------------------------------------------------------- | -------------- | ------------------- | ----------- | --------------- |
| HUMAN-001 | Approver identity | Every approval resolves to one authenticated `approver_id`.   | `approver.py`  | `test_approvals.py` | Phase 2 / 8 | `UNIT_VERIFIED` |
| HUMAN-002 | High-risk timeout | High-risk gates use declared `default_deny` semantics.        | `approver.py`  | `test_approvals.py` | Phase 2     | `UNIT_VERIFIED` |
| HUMAN-003 | Hold timeout      | Budget continuation follows declared `default_hold` behavior. | `approver.py`  | `test_approvals.py` | Phase 2     | `UNIT_VERIFIED` |
| HUMAN-004 | Attention budget  | N+1 concurrent approvals pause Team Builder.                  | `attention.py` | `test_approvals.py` | Phase 2 / 8 | `UNIT_VERIFIED` |
| HUMAN-005 | Auditability      | Approval decision is reconstructable from Pulses.             | Pulse / Audit  | Approval audit      | Phase 8     | `SPECIFIED`     |

---

# 16. Orchestrator Contracts

| ID       | Contract              | Required Invariant                                                | Implementation             | Harness               | Roadmap | Status      |
| -------- | --------------------- | ----------------------------------------------------------------- | -------------------------- | --------------------- | ------- | ----------- |
| ORCH-001 | Thin coordinator      | Orchestrator owns sequencing, not policy/business logic.          | `orchestrator.py`          | Full-loop suite       | Phase 4 | `GATE_VERIFIED` |
| ORCH-002 | Goal Analyzer         | Command becomes a Goal Spec.                                      | `goal_analyzer.py`         | Goal test             | Phase 4 | `GATE_VERIFIED` |
| ORCH-003 | Planner               | Goal Spec becomes TaskGraph.                                      | `planner.py`               | Planning test         | Phase 4 | `GATE_VERIFIED` |
| ORCH-004 | Team Builder          | TaskGraph becomes assignments.                                    | `team_builder.py`          | Assignment test       | Phase 4 | `GATE_VERIFIED` |
| ORCH-005 | Reconciliation        | Desired vs actual state produces policy-defined recovery actions. | `reconciler.py`            | Recovery test         | Phase 4 | `GATE_VERIFIED` |
| ORCH-006 | Zero-LLM control loop | Full deterministic loop works without an LLM.                     | Orchestrator + mock agents | Full-loop harness     | Phase 4 | `GATE_VERIFIED` |
| ORCH-007 | Core independence     | Deterministic core operates with `agents/` removed.               | CI architecture            | Core-independence job | Phase 4 | `GATE_VERIFIED` |

---

# 17. Agent and LLM Contracts

| ID        | Contract            | Required Invariant                                                                     | Implementation    | Harness                | Roadmap  | Status      |
| --------- | ------------------- | -------------------------------------------------------------------------------------- | ----------------- | ---------------------- | -------- | ----------- |
| AGENT-001 | Agent state machine | Agent behavior is represented as deterministic states with stochastic LLM transitions. | `agents/base.py`  | State transition tests | Phase 5  | `GATE_VERIFIED` |
| AGENT-002 | LLM boundary        | LLM code does not enter deterministic `core/`.                                         | Dependency guard  | CI                     | Phase 0+ | `GATE_VERIFIED` |
| AGENT-003 | LLM recording       | Calls are recorded by `correlation_id`.                                                | `llm/recorder.py` | Recorder test          | Phase 5  | `GATE_VERIFIED` |
| AGENT-004 | Replay              | Recorded LLM outputs can reproduce Agent transitions.                                  | Recorder / Agent  | Replay fuzz            | Phase 5  | `GATE_VERIFIED` |
| AGENT-005 | Context scopes      | Task, Agent, and Space scopes remain distinct.                                         | Context Manager   | Context suite          | Phase 5  | `GATE_VERIFIED` |
| AGENT-006 | Compaction          | Pinned information survives compaction.                                                | Context Manager   | 500-turn test          | Phase 5  | `GATE_VERIFIED` |
| AGENT-007 | Handoff             | Recovery uses the defined Handoff Note contract.                                       | Context Manager   | Recovery test          | Phase 5  | `GATE_VERIFIED` |

---

# 18. Worker Contracts

| ID         | Contract                    | Required Invariant                                        | Implementation   | Harness                | Roadmap | Status          |
| ---------- | --------------------------- | --------------------------------------------------------- | ---------------- | ---------------------- | ------- | --------------- |
| WORKER-001 | Uniform execution interface | Workers return artifacts or Pulse-typed failures.         | Worker interface | `test_workers_future.py` | Phase 6 | `GATE_VERIFIED` |
| WORKER-002 | Data/instruction separation | External payload is data, not executable instruction.     | Worker interface | `test_workers_future.py` | Phase 6 | `GATE_VERIFIED` |
| WORKER-003 | Sandbox enforcement         | Disallowed filesystem/network operations are blocked.     | Sandbox          | `test_workers_future.py`, `test_worker_security_adversarial.py` | Phase 6 | `GATE_VERIFIED` |
| WORKER-004 | Failure taxonomy            | Workers use the central failure taxonomy.                 | Worker boundary  | `test_worker_chaos.py`  | Phase 6 | `GATE_VERIFIED` |
| WORKER-005 | Subagent isolation          | Subagent receives only Handoff Note + relevant plan node. | Subagent Worker  | `test_specialized_workers.py` | Phase 6 | `GATE_VERIFIED` |

---

# 19. Node Runtime Contracts

| ID       | Contract           | Required Invariant                                             | Implementation   | Harness              | Roadmap     | Status      |
| -------- | ------------------ | -------------------------------------------------------------- | ---------------- | -------------------- | ----------- | ----------- |
| NODE-001 | Language boundary  | Node Runtime is Rust, not Python.                              | `node_runtime/`  | Cargo build/check    | Phase 0 / 7 | `SPECIFIED` |
| NODE-002 | Device enforcement | Device itself validates grants.                                | `ryu-node`       | Forged-wire test     | Phase 7     | `SPECIFIED` |
| NODE-003 | Grant scope        | Grant is bound to Space, capability, and session.              | Grant enforcer   | Cross-use test       | Phase 7     | `SPECIFIED` |
| NODE-004 | Revocation         | Device enforces revocation during execution.                   | Grant enforcer   | Mid-call revoke      | Phase 7     | `SPECIFIED` |
| NODE-005 | Heartbeat lease    | Node availability is represented through heartbeat state.      | Heartbeat        | Lease tests          | Phase 7     | `SPECIFIED` |
| NODE-006 | Offline recovery   | Node disconnect creates checkpointable offline state.          | Node coordinator | Offline test         | Phase 7     | `SPECIFIED` |
| NODE-007 | Resume             | Reconnection resumes using the same idempotency semantics.     | Node coordinator | Resume/no-dup test   | Phase 7     | `SPECIFIED` |
| NODE-008 | Independent audit  | Device audit remains readable without RYU server availability. | Node audit       | Physical-device test | Phase 7     | `SPECIFIED` |

---

# 20. Registry / Supply-Chain Contracts

| ID      | Contract         | Required Invariant                                           | Implementation | Harness            | Roadmap | Status      |
| ------- | ---------------- | ------------------------------------------------------------ | -------------- | ------------------ | ------- | ----------- |
| REG-001 | Version identity | Tools/Skills have explicit versions.                         | Registry       | Registration test  | Phase 9 | `SPECIFIED` |
| REG-002 | Content hash     | Artifact identity includes content hash.                     | Registry       | Payload swap test  | Phase 9 | `SPECIFIED` |
| REG-003 | Signature        | Unsigned artifacts are rejected where required.              | Registry       | Unsigned test      | Phase 9 | `SPECIFIED` |
| REG-004 | Risk binding     | Risk tier is bound to verified artifact identity.            | Registry       | Hash mutation test | Phase 9 | `SPECIFIED` |
| REG-005 | Version pinning  | `@latest` cannot silently change execution.                  | Registry       | Version-pin test   | Phase 9 | `SPECIFIED` |
| REG-006 | MCP namespace    | MCP tools are mapped into the Tools Layer under a namespace. | MCP ingestion  | MCP integration    | Phase 9 | `SPECIFIED` |

---

# 21. Memory and Learning Contracts

| ID      | Contract               | Required Invariant                                            | Implementation        | Harness                 | Roadmap  | Status      |
| ------- | ---------------------- | ------------------------------------------------------------- | --------------------- | ----------------------- | -------- | ----------- |
| MEM-001 | Space-local memory     | Knowledge begins in the owning Space.                         | Memory adapters       | Isolation test          | Phase 10 | `SPECIFIED` |
| MEM-002 | Experience schema      | Experience contains required fields including counterfactual. | Contracts / Reflector | Schema test             | Phase 10 | `SPECIFIED` |
| MEM-003 | Experience persistence | Experience survives storage round-trip.                       | Memory adapter        | Round-trip test         | Phase 10 | `SPECIFIED` |
| MEM-004 | Actionable learning    | Experience changes behavior measurably on repeated tasks.     | Adaptation loop       | Frozen-trace evaluation | Phase 10 | `SPECIFIED` |
| MEM-005 | Promotion gate         | Global knowledge requires explicit promotion.                 | Promotion pipeline    | Promotion forgery test  | Phase 10 | `SPECIFIED` |
| MEM-006 | Human approval         | Promotion approval carries authenticated `approver_id`.       | Promotion gate        | Approval test           | Phase 10 | `SPECIFIED` |

---

# 22. Recovery Contracts

These contracts are mandatory architectural verification areas because distributed state and event persistence can diverge during process failure.

| ID      | Contract                | Required Invariant                                                                           | Implementation        | Harness                | Roadmap     | Status      |
| ------- | ----------------------- | -------------------------------------------------------------------------------------------- | --------------------- | ---------------------- | ----------- | ----------- |
| REC-001 | Runtime restart         | Restart does not silently lose authoritative state.                                          | Runtime lifecycle     | Runtime crash test     | Phase 1+    | `SPECIFIED` |
| REC-002 | Pulse/state consistency | State mutations and corresponding Pulses have defined atomicity or reconciliation semantics. | Runtime / Pulse Store | Crash consistency test | Phase 1+    | `SPECIFIED` |
| REC-003 | Plan recovery           | Authoritative plan version survives restart.                                                 | Plan Store            | Plan recovery          | Phase 2     | `SPECIFIED` |
| REC-004 | Budget recovery         | Budget window state remains authoritative after restart.                                     | Admission / Windows   | Budget recovery        | Phase 2     | `SPECIFIED` |
| REC-005 | Lease recovery          | Lease ownership and expiry recover deterministically.                                        | Resource Manager      | `test_manager.py`, `test_chaos_scenarios.py` | Phase 3     | `GATE_VERIFIED` |
| REC-006 | Approval recovery       | Pending approvals have defined restart semantics.                                            | Approver              | Approval recovery      | Phase 2 / 8 | `SPECIFIED` |
| REC-007 | Node recovery           | Offline/reconnected nodes do not duplicate side effects.                                     | Node Runtime          | Offline/resume         | Phase 7     | `SPECIFIED` |

---

# 23. State vs Event Source-of-Truth Contracts

Each stateful subsystem must identify its authoritative source.

| Domain         | Authoritative Source                  | Event Role            | Required Verification    | Status      |
| -------------- | ------------------------------------- | --------------------- | ------------------------ | ----------- |
| Pulse history  | Durable Pulse Store                   | Primary event history | Replay consistency       | `SPECIFIED` |
| Plan           | Plan Store / authoritative plan state | Audit/reconstruction  | CAS/replay test          | `SPECIFIED` |
| Resource lease | Resource Manager                      | Audit trail           | Lease race/recovery      | `SPECIFIED` |
| Node grant     | Node Runtime enforcement state        | Audit trail           | Device-side verification | `SPECIFIED` |
| Human approval | Approval state                        | Approval Pulse        | Restart/audit test       | `SPECIFIED` |
| Space Memory   | Space Memory implementation           | Memory Pulses         | Promotion/isolation test | `SPECIFIED` |
| LLM call       | LLM Recorder                          | Replay evidence       | Recorder/replay test     | `SPECIFIED` |

**Required follow-up:** exact authority and reconciliation semantics must match `docs/architecture.md`.

---

# 24. Atomicity Contract

## ATOMIC-001 — State Mutation + Pulse Publication

The architecture must define the relationship between:

```text
State Mutation
      +
Pulse Publication
```

Required behavior must be explicitly established for:

1. state mutation succeeds + Pulse succeeds
2. state mutation succeeds + Pulse fails
3. state mutation fails + Pulse succeeds
4. process crash between mutation and publication
5. process crash after publication but before acknowledgement

No implementation may silently choose semantics for these cases.

**Status:** `SPECIFIED` as a required architectural contract; exact mechanism requires verification against the frozen architecture.

---

# 25. Security Contract Matrix

| Security Area          | Required Proof                                       | Harness             | Phase     | Status      |
| ---------------------- | ---------------------------------------------------- | ------------------- | --------- | ----------- |
| Space isolation        | Cross-Space access denied                            | Isolation suite     | 2         | `SPECIFIED` |
| Capability boundary    | Direct execution bypass denied                       | Capability suite    | 2         | `SPECIFIED` |
| Budget enforcement     | Zero calls at $0                                     | Budget suite        | 2         | `SPECIFIED` |
| Secret containment     | Secret absent from persistence surfaces              | Secret suite        | 2 / 5     | `SPECIFIED` |
| Taint propagation      | Taint survives causal chain                          | Taint suite         | 1         | `SPECIFIED` |
| Taint clearance        | Clearance is forward-only + audited                  | Taint-clear suite   | 1 / 2     | `SPECIFIED` |
| Injection defense      | Tainted instruction cannot create unauthorized grant | Injection canary    | 2 / 6 / 8 | `SPECIFIED` |
| Grant forgery          | Forged grants rejected                               | Node/security suite | 7         | `SPECIFIED` |
| Signature verification | Unsigned artifact rejected                           | Registry suite      | 9         | `SPECIFIED` |
| Version pinning        | Artifact mutation cannot inherit old trust           | Registry suite      | 9         | `SPECIFIED` |

---

# 26. Harness Traceability

Every harness case must have a corresponding entry in:

```text
harness/spec_map.yaml
```

Required mapping:

```yaml
case:
  doc: "architecture section"
  criterion: "exact acceptance criterion"
```

The following foundational cases are required from Phase 0:

| Harness Case                            | Contract              | Architecture Criterion                  | Phase |
| --------------------------------------- | --------------------- | --------------------------------------- | ----- |
| `test_registry_rejects_unknown_type.py` | PULSE-001             | Unknown Pulse types rejected            | 0     |
| `test_causation_walk.py`                | PULSE-004 / PULSE-005 | Causal chain reconstructable            | 0     |
| `test_taint_chain.py`                   | PULSE-006 / PULSE-007 | Taint propagation and forward clearance | 0 / 1 |

Future cases must be added as their roadmap phase becomes active.

---

# 27. Phase Gate Matrix

| Phase | Primary Contract Area         | Required Evidence                                         | Exit State      |
| ----- | ----------------------------- | --------------------------------------------------------- | --------------- |
| 0     | Scaffold + initial Pulse Bus  | CI + 3 real harness cases                                 | `GATE_VERIFIED` |
| 1     | Durable Pulse Bus + Contracts | Durable replay + fuzz + codegen freshness                 | `GATE_VERIFIED` |
| 2     | Space Kernel                  | Budget + CAS + secrets + approval + injection             | `GATE_VERIFIED` |
| 3     | Resources + Chaos             | Lease race + fault matrix + idempotency + chaos replay    | `GATE_VERIFIED` |
| 4     | Deterministic Orchestrator    | Full zero-LLM loop + replay + core independence           | `GATE_VERIFIED` |
| 5     | Real Agent + Context          | 500-turn compaction + LLM replay + transition determinism | `GATE_VERIFIED` |
| 6     | Workers + Sandbox             | Injection + escape + taxonomy enforcement                 | `GATE_VERIFIED` |
| 7     | Node Runtime                  | Device enforcement + revoke + offline/resume + audit      | `GATE_VERIFIED` |
| 8     | CLI + Human Gates             | Real approval loop + attention budget + channel injection | `GATE_VERIFIED` |
| 9     | Skills + MCP                  | Signature + hash + pinning + MCP integration              | `GATE_VERIFIED` |
| 10    | Memory + Adaptation           | Measurable learning + promotion integrity                 | `GATE_VERIFIED` |
| 11    | Multi-Platform Nodes          | Second platform full Phase 7 gate                         | `GATE_VERIFIED` |

---

# 28. Current State — Clean Rebuild

Because RYU AI is being rebuilt from square one, no implementation claim is inherited from previous repositories or previous sessions.

Initial state:

```text
Architecture specification     SPECIFIED
Contract matrix                SPECIFIED
Machine contracts              NOT YET IMPLEMENTED
Repository scaffold             NOT YET IMPLEMENTED
Pulse Bus                       NOT YET IMPLEMENTED
Harness                         NOT YET IMPLEMENTED
Phase 0                         NOT STARTED
```

Previous implementation status must not be used as evidence for the clean rebuild.

---

# 29. Open Contract Decisions

These are not invitations for an agent to invent behavior.

They identify areas that require explicit architectural resolution before implementation reaches the affected gate.

| ID       | Open Question                                                                          | Required Before |
| -------- | -------------------------------------------------------------------------------------- | --------------- |
| OPEN-001 | Exact state/Pulse atomicity or reconciliation mechanism                                | Phase 1         | Resolved: ADR-0002 |
| OPEN-002 | Authoritative source when state and event history disagree                             | Phase 1         | Resolved: ADR-0002 |
| OPEN-003 | Exact owner of idempotency guarantee                                                   | Phase 3         | Resolved: ADR-0006 |
| OPEN-004 | Exact PlanDelta `ops[]` payload shapes                                                 | Phase 2         | Resolved: ADR-0003 |
| OPEN-005 | Exact budget accounting semantics: reservation, actual cost, streaming, reconciliation | Phase 2         | Architecture §4 / admission.py |
| OPEN-006 | Exact taint clearance scope semantics                                                  | Phase 1 / 2     | taint.py / Architecture §10 |
| OPEN-007 | Secret sanitization boundary for LLM recording                                         | Phase 5         | Resolved: ADR-0011 |
| OPEN-008 | Exact Reconciler responsibility relative to Monitor and Adapter/Reflector              | Phase 4         | Resolved: ADR-0008 |
| OPEN-009 | Recovery semantics for approval state                                                  | Phase 2         | approver.py |
| OPEN-010 | Resource queue fairness/starvation policy                                              | Phase 3         | Resolved: ADR-0005 |

If the frozen architecture already answers an open question, the architecture is authoritative and the row must be updated to point to the relevant section.

If the architecture does not answer it, an ADR is required before implementation makes the decision.

---

# 30. Definition of Done for This Matrix

`CONTRACT_MATRIX.md` itself is complete when:

* [ ] Every architectural law has a mapped contract.
* [ ] Every major security boundary has a mapped contract.
* [ ] Every machine-readable contract has a mapped consumer.
* [ ] Every roadmap phase has mapped contracts.
* [ ] Every executable acceptance criterion has a planned harness case.
* [ ] Open architectural decisions are explicitly identified.
* [ ] No implementation status is claimed without evidence.
* [ ] The matrix does not override `docs/architecture.md`.
* [ ] Changes follow the project's ADR discipline.

---

# 31. Governing Principle

> **If we cannot point from an architectural requirement to a contract, from that contract to an implementation boundary, and from that implementation to executable evidence, RYU AI is not yet proven.**

The matrix is a verification map, not a second architecture.

---

**Document status:** `FOUNDATION — CLEAN REBUILD`

**Next artifact:** `contracts/registry/pulse-types.json`

**Next implementation gate:** Phase 0 — Repository Scaffold
