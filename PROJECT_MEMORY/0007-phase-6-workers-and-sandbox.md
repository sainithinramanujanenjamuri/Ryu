# RYU AI — Project Memory

## Entry 0007 — Phase 6 Workers, Sandbox Execution, and Seccomp Containment

**Date:** 2026-09-20  
**Phase:** 6 — Workers + Sandbox Execution + Seccomp Containment  
**Status:** COMPLETE (PHASE 6 GATE: PASS)  
**Previous Baseline:** `b84fd1c` (Phase 5)

---

### Summary

Phase 6 establishes the first controlled physical execution layer of RYU AI under the Space-Centric Cognitive Architecture (SCCA). The central governing invariant of Phase 6 is:
> **Workers execute authorized capabilities, but Workers never receive unrestricted host authority.**

The complete deterministic authority chain is enforced without exception:
```text
Human Intent / Goal
        ↓
Space Orchestrator
        ↓
Agent (Cognition)
        ↓
LLM Transition Function
        ↓
AgentProposal
        ↓
Deterministic Proposal Validation (ProposalValidator)
        ↓
Space Kernel / AdmissionControl (Budget & Policy)
        ↓
ResourceManager (Lease Validation & Issuance)
        ↓
Worker Runtime (Assigned Capability)
        ↓
Execution Sandbox (FS / Net / Proc / Limits)
        ↓
OS / Seccomp Boundary (Syscall Filter)
        ↓
Actual Capability Execution
```

Neither the LLM, the Agent, nor the Worker can bypass this chain to access raw host primitives directly.

---

### Approved Corrections Incorporated

1. **Test Count Discipline:** Acceptance criteria evaluated strictly on 0 failures across the test suite and executable proof of contracts, not arbitrary target counts.
2. **Strict Platform Boundary Preservation:**
   - **Linux**: Actual kernel Seccomp enforcement (`PR_SET_SECCOMP` / BPF syscall filtering) blocking forbidden syscalls with structured `SeccompViolation`.
   - **Windows**: Windows does not provide Seccomp. Transparently documented as "not supported (platform limitation)" while enforcing Windows-level containment: process-tree termination, strict path canonicalization and deny-by-default, network socket interception, and environment stripping.
3. **No Browser / Subagent Scope Creep:**
   - `BrowserWorker`: Minimal external content fetcher with injection canary defense: external content is marked `taint: True`, instructions within HTML are inert (`WORKER-002`, `TAINT-001`). No heavy browser automation framework.
   - `SubagentWorker`: Subordinate worker seeded *only* with `HandoffNote` and assigned plan node (`WORKER-005`). Cannot mint leases, mutate plans, or spawn recursive agents.
4. **Real Syscall Enforcement:**
   - Linux: Verified real kernel syscall interception. Simulation rejected.
   - Windows: Tested documented Windows security fallback separately.
   - Precise terminology used throughout: `tested`, `verified`, `blocked`, `contained`, `platform-specific`, `not supported`, `known limitation`.

---

### Architecture & ADR Foundations

- **ADR-0013:** `adr/0013-worker-runtime-and-execution-boundary.md` — Defines `WorkerIdentity`, `WorkerState` lifecycle, `ExecutionRequest`, `ExecutionResult`, typed output payloads, and non-authority boundaries.
- **ADR-0014:** `adr/0014-sandbox-and-execution-isolation.md` — Defines multi-layer `SandboxManager` and `SandboxPolicy` (Filesystem deny-by-default, Network egress policy, Process tree lifecycle, and resource quotas).
- **ADR-0015:** `adr/0015-linux-seccomp-and-platform-containment.md` — Defines Linux Seccomp containment (`PR_SET_SECCOMP` / BPF) and Windows platform containment mechanisms.
- **ADR-0016:** `adr/0016-worker-lifecycle-failure-and-recovery.md` — Formalizes failure taxonomy mapping (`WORKER-004`), orphan-process termination, lease release on failure, and crash recovery.

---

### What Was Built

1. **Worker Contracts & Runtime (`workers/`):**
   - `workers/contract.py`: `WorkerState` enum (12 states), `WorkerIdentity`, `ExecutionRequest`, `ExecutionResult`, `ExecutionError`, `ExecutionLimits`, `FilesystemPolicy`, `NetworkPolicy`, `SandboxPolicy`, `Artifact`, `ExecutionMetrics`, and failure taxonomy mapping (`map_error_to_failure_taxonomy`).
   - `workers/base.py`: `BaseWorker` deterministic state machine (`CREATED` -> `READY` -> `ADMITTED` -> `STARTING` -> `RUNNING` -> `OBSERVING` -> `COMPLETED`/`FAILED`), mandatory lease check, Space isolation check, secret sanitization, and typed Pulse publication (`worker.tool.called`, `worker.tool.succeeded`, `worker.tool.failed`).
2. **Multi-Layer Sandbox Subsystem (`workers/sandbox/`):**
   - `workers/sandbox/filesystem.py`: `FilesystemSandbox` enforcing path canonicalization, deny-by-default, and forbidden patterns (`.git`, `.env`, `core/`, etc.).
   - `workers/sandbox/network.py`: `NetworkSandbox` with `DISABLED`, `RESTRICTED`, and `ALLOWED` egress modes and socket connect interception.
   - `workers/sandbox/process.py`: `ProcessSandbox` with stripped environment variables (`sanitize_environment`), watchdog timeout, and recursive process-tree termination (`terminate_process_tree`).
   - `workers/sandbox/seccomp.py`: `SeccompFilter` (Linux actual Seccomp) and `PlatformSecurityAdapter` (Windows fallback containment).
   - `workers/sandbox/manager.py`: `SandboxManager` unified coordinator.
3. **Specialized Workers (`workers/`):**
   - `workers/python/worker.py`: `PythonWorker` sandboxed Python code execution with network preamble injection.
   - `workers/shell/worker.py`: `ShellWorker` sandboxed command execution with strict allowlist.
   - `workers/file/worker.py`: `FileWorker` workspace/artifact CRUD with `Artifact` generation.
   - `workers/browser/worker.py`: `BrowserWorker` injection canary with `taint: True`.
   - `workers/subagent/worker.py`: `SubagentWorker` seeded only with `HandoffNote` + plan node.
4. **Test Suites & Evidence:**
   - `workers/tests/`: 32 unit tests across state machine, lease enforcement, filesystem, network, process cleanup, secret redaction, and specialized workers.
   - `harness/cases/workers/test_workers_future.py`: Activated `WORKER-001`, `WORKER-002`, `WORKER-003`.
   - `harness/cases/security/test_security_future.py`: Activated `SECRET-004`, `TAINT-006`.
   - `harness/cases/workers/test_worker_security_adversarial.py`: 21 adversarial security attack tests.
   - `harness/cases/workers/test_worker_chaos.py`: 18 chaos and fault injection tests.
   - `harness/cases/workers/test_e2e_execution_pipeline.py`: Full execution pipeline integration.
