# RYU AI — Phase 12.4 Implementation Report: Worker Invocation & Sandbox Integration

**Project:** RYU AI  
**Architecture:** Space-Centric Cognitive Architecture (SCCA)  
**Phase:** 12.4 — Worker Invocation & Sandbox Integration  
**Authoritative Specification:** `docs/PHASE_12_EXECUTION_ENGINE_SPEC.md` (§7, §8)  
**Architectural Decision:** `adr/0041-autonomous-task-dispatcher-dag-traversal-and-plan-convergence-engine.md`, `adr/0014-sandbox-execution-environment.md`, `adr/0023-dynamic-taint-tracking.md`, `adr/0005-fractional-resource-leases.md`  
**Baseline Commit:** `3783f70` (Phase 12.3)  
**Phase 12.4 Gate Status:** **PASS**  

---

## 1. Executive Summary & Objective

Phase 12.4 crosses the fundamental architectural boundary from the **Control Plane** (admitted, leased tasks) into **Actual Execution** (sandboxed workers executing tools, code, and node devices).

The objective is to implement deterministic worker invocation and execution observation while preserving:
1. SCCA Law 1 (Everything happens inside a Space)
2. SCCA Law 2 (Capabilities are requested, never owned)
3. SCCA Law 3 (Components communicate through typed pulses)
4. SCCA Law 6 (Failures are contained, escalated, and never silent)
5. The Core Boundary Rule (`AGENTS.md §7` — Zero imports of higher cognitive/worker layers in `core/`)

### Target State Lifecycle Transition
$$\text{LEASED} \longrightarrow \text{DISPATCHED} \longrightarrow \text{RUNNING} \longrightarrow \text{OBSERVING} \quad (\text{or terminal: } \text{TIMED\_OUT} \mid \text{FAILED} \mid \text{CANCELLED})$$

Execution strictly stops at `OBSERVING`. Worker execution is an operational step, **not goal completion**. Semantic evaluation, plan convergence, and dependency unblocking belong strictly to Phase 12.5 and beyond.

---

## 2. Protocol Boundary & Strict Core Independence

To strictly uphold **`AGENTS.md §7` (Deterministic Core Independence)**, the Dispatcher inside `core/orchestrator/dispatch_model.py` must NEVER import `workers/` or instantiate concrete workers directly.

```text
┌────────────────────────────────────────────────────────┐
│                        core/                           │
│  DeterministicDispatcher                               │
│  - Verifies lease & space identity                     │
│  - Deduplicates via idempotency key                    │
│  - CAS: LEASED -> DISPATCHED -> RUNNING -> OBSERVING   │
│  - Publishes task.started / task.failed pulses         │
│  - Releases resource lease on ALL exit paths           │
│                                                        │
│  WorkerInvokerProtocol (typing.Protocol)               │
│    └─ invoke(request: TaskExecutionRequest)            │
│         -> TaskExecutionResult                         │
└───────────────────────────┬────────────────────────────┘
                            │ (Inversion of Control)
┌───────────────────────────▼────────────────────────────┐
│                      workers/                          │
│  RuntimeWorkerInvoker (implements protocol)            │
│  - Maps capability -> concrete worker:                 │
│      code.python      -> PythonWorker                  │
│      system.shell     -> ShellWorker                   │
│      filesystem.io    -> FileWorker                    │
│      browser.navigate -> BrowserWorker                 │
│      agent.subagent   -> SubagentWorker                │
│      node.hardware    -> NodeWorker                    │
│  - Applies SandboxPolicy & ExecutionLimits             │
│  - Enforces forward-only taint inheritance             │
│  - Sanitizes secrets and formats structured outputs    │
└────────────────────────────────────────────────────────┘
```

The AST dependency checker (`scripts/dep_guard.py`) and runtime isolation harness (`scripts/v1_verify_core_independence.py`) both verify 0 forbidden imports.

---

## 3. Concrete Implementations Added

### 3.1 `core/orchestrator/dispatch_model.py`
* **`TaskExecutionRequest`**: Dataclass specifying task context (`task_id`, `space_id`, `plan_id`, `plan_version`, `capability`, `input_data`, `lease`, `is_tainted`, `attempt`, `timeout_seconds`).
* **`TaskExecutionResult`**: Structured execution outcome (`success`, `status`, `output_data`, `error_message`, `exit_code`, `telemetry`, `tainted`, `artifacts`).
* **`DispatchExecutionResult`**: Dispatch pipeline outcome containing the updated task node, execution result, cached status, and error info.
* **`DeterministicDispatcher.dispatch_task(space_id, task_id, invoker, clock)`**:
  1. Validates task exists and belongs to the specified `space_id`.
  2. Evaluates idempotency deduplication: if `(space_id, plan_version, task_id, attempt)` is already tracked, returns `cached=True` immediately without re-execution.
  3. Verifies `node.state == TaskState.LEASED.value` via CAS.
  4. Validates active resource lease: verifies `lease.is_valid(now)`, `lease.space_id == space_id`, `lease.requester_id == task_id`. If invalid or expired, sets task to `FAILED` and releases lease.
  5. CAS transition: `LEASED` $\rightarrow$ `DISPATCHED`.
  6. CAS transition: `DISPATCHED` $\rightarrow$ `RUNNING`.
  7. Publishes `task.started` pulse via `SpaceKernel.pulse_bus`.
  8. Invokes `invoker.invoke(request)`.
  9. Based on worker result status:
     - On `"success"` / `"observing"`: CAS transition `RUNNING` $\rightarrow$ `OBSERVING`.
     - On `"timeout"` / `"timed_out"`: CAS transition `RUNNING` $\rightarrow$ `TIMED_OUT`, publishes `task.failed` pulse.
     - On `"cancelled"`: CAS transition `RUNNING` $\rightarrow$ `CANCELLED`, publishes `task.failed` pulse.
     - On `"failed"` / error: CAS transition `RUNNING` $\rightarrow$ `FAILED`, publishes `task.failed` pulse.
  10. **Guaranteed Cleanup**: Releases the acquired lease via `resource_manager.release_lease()` under a `finally` block on all exit paths.
* **`DeterministicDispatcher.execute_task_pipeline(space_id, task_id, invoker, clock)`**:
  - Chains admission control, fractional lease acquisition, and worker invocation end-to-end.

### 3.2 `workers/invoker.py` & `workers/contract.py`
* **`RuntimeWorkerInvoker`**: Concrete worker invoker implementing `WorkerInvokerProtocol`.
* **Capability Routing**: Routes requested capability to appropriate sandboxed worker instance:
  - `code.*`, `compute.*`, `python.*` $\rightarrow$ `PythonWorker`
  - `system.shell`, `shell.*`, `bash.*` $\rightarrow$ `ShellWorker`
  - `file.*`, `filesystem.*`, `artifact.*` $\rightarrow$ `FileWorker`
  - `browser.*`, `web.*` $\rightarrow$ `BrowserWorker`
  - `subagent.*`, `delegate.*` $\rightarrow$ `SubagentWorker`
  - `node.*`, `hardware.*`, `device.*` $\rightarrow$ `NodeWorker`
* **Sandbox Enforcement**: Wraps worker execution in `SandboxPolicy` and `ExecutionLimits` (max memory, max CPU, timeout).
* **Taint Invariants**: Tainted execution requests propagate forward taint into execution results.
* **Secret Sanitization**: Automatically scrubs Bearer tokens, private keys, and authorization headers from `output_data` and error messages.

---

## 4. Mandatory 20-Point Security Battery Results

All 20 mandatory security tests in `workers/tests/test_phase12_worker_invocation.py` pass cleanly without mock bypasses:

| Test ID | Scenario | Verification Result |
|:---|:---|:---|
| `SEC-01` | Missing Lease Rejection | Task rejected before worker dispatch; state marked `FAILED`; 0 workers invoked. |
| `SEC-02` | Expired Lease Rejection | Lease expired via simulated clock; rejected before execution; lease cleaned up. |
| `SEC-03` | Cross-Space Lease Theft | Lease for `space-A` presented in `space-B`; immediate rejection; 0 execution. |
| `SEC-04` | Cross-Task Lease Theft | Lease for `task-1` presented for `task-2`; immediate rejection; 0 execution. |
| `SEC-05` | Capability Mismatch | Task capability does not match admitted capability; execution blocked. |
| `SEC-06` | Unadmitted Bypass | Unadmitted task (`READY` instead of `LEASED`) cannot jump directly to execution. |
| `SEC-07` | Subprocess Isolation | Python and Shell workers run in strict sandboxed subprocesses with memory bounds. |
| `SEC-08` | Path Traversal Prevention | File worker rejects access attempts escaping space working directory. |
| `SEC-09` | Forward-Only Taint Inheritance | Tainted task request forces worker execution result to be tagged `tainted=True`. |
| `SEC-10` | Secret Sanitization | Secrets and authorization tokens scrubbed from execution output telemetry. |
| `SEC-11` | Node Device Grant Scope | Node device capabilities require active, valid `DeviceGrant` matching worker ID. |
| `SEC-12` | Illegal State Machine Jump | Dispatcher rejects jumping directly to `RUNNING` or `OBSERVING` from non-`LEASED` states. |
| `SEC-13` | Duplicate Dispatch Idempotency | Duplicate dispatch requests with identical idempotency keys return `cached=True`. |
| `SEC-14` | Post-Execution Lease Release | Leases are durably released in `ResourceManager` immediately upon task completion. |
| `SEC-15` | Failure Lease Release | When worker execution fails, lease is guaranteed released in `ResourceManager`. |
| `SEC-16` | Timeout Lease Release | When worker execution times out, lease is guaranteed released in `ResourceManager`. |
| `SEC-17` | Cancellation Lease Release | When execution is cancelled, lease is guaranteed released in `ResourceManager`. |
| `SEC-18` | Task Started Pulse Emitted | `task.started` pulse with correlation ID and attempt count durably published. |
| `SEC-19` | Task Failed Pulse Emitted | `task.failed` pulse with failure taxonomy and exit code durably published on error. |
| `SEC-20` | Worker Timeout Trapping | Worker timeouts trapped deterministically; transitions to `TIMED_OUT` without hanging. |

---

## 5. Genuine Execution Evidence

Phase 12.4 was verified with **genuine code and command execution**, not stubs:

1. **Python Sandbox Execution:**
   - Script: `print('RYU_PHASE_12_4_EXECUTION_VERIFIED')`
   - Verified Output: `stdout` captured exactly, exit code 0, telemetry recorded.
2. **Shell Sandbox Execution:**
   - Command: `echo SHELL_PHASE_12_4_VERIFIED`
   - Verified Output: Process spawned, stdout captured, exit code 0.
3. **Filesystem Worker Operations:**
   - Verified writing, reading, and SHA-256 digesting of genuine artifacts in space sandbox directory.
4. **Node Runtime Device Grant Execution:**
   - Verified `NodeRuntime` and `DeviceGrantManager` binding device hardware leases under `task_id` requester scoping.

---

## 6. Comprehensive Verification Metrics

| Check / Test Suite | Command / Target | Result | Details |
|:---|:---|:---|:---|
| **Core Boundary Guard** | `python scripts/dep_guard.py` | **PASS** | 0 forbidden imports found in `core/` |
| **Contract Sync** | `python scripts/contract_sync.py` | **PASS** | All 38 registered pulse types in sync |
| **Core Independence Proof** | `python scripts/v1_verify_core_independence.py` | **PASS** | AST + Runtime Isolation + Zero-LLM Control Loop |
| **Spec Coverage Audit** | `python scripts/v1_audit_spec_coverage.py` | **PASS** | 0 orphaned criteria, 0 missing tests, 0 stale evidence |
| **Python Linter** | `ruff check core workers` | **PASS** | All checks passed cleanly |
| **Static Type Checker** | `mypy core workers` | **PASS** | Success: no issues found in 122 source files |
| **Rust Node Runtime** | `cargo check --manifest-path node_runtime/Cargo.toml` | **PASS** | Finished `dev` profile in 0.07s |
| **Protocol Unit Tests** | `pytest core/orchestrator/tests/test_phase12_dispatcher_protocol.py` | **PASS** | 13 passed (zero worker imports in core) |
| **Worker Execution Tests** | `pytest workers/tests/test_phase12_worker_invocation.py` | **PASS** | 24 passed (including genuine execution & 20 SEC tests) |
| **Core & Worker Suite** | `pytest core workers/tests node/tests` | **PASS** | 283 passed in 5.28s |
| **Full Harness Suite** | `pytest harness/cases` | **PASS** | 319 passed, 10 skipped in 34.2s |

---

## 7. Deferred Capabilities (Strict Phase Boundary)

The following capabilities are explicitly deferred and **NOT** implemented in Phase 12.4:
* **Semantic Goal Evaluation:** Evaluating whether the task output meets the human goal (deferred to Phase 12.5 / 12.7).
* **Dynamic Plan Convergence / Replanning:** Re-evaluating DAG topology or adding new tasks dynamically (deferred to Phase 12.6).
* **Automatic Dependency Unblocking:** Cascading transitions from `OBSERVING` to downstream task readiness (deferred to Phase 12.5).
* **Autonomous Coding & Research Agents:** Open-ended cognitive loops (deferred to subsequent phases).

Phase 12.4 strictly delivers **Worker Invocation & Sandbox Integration**, completing the pipeline from control plane to running observation.

---

## 8. Git Working Tree & Changes

```text
Modified:
  core/orchestrator/__init__.py
  core/orchestrator/dispatch_model.py
  docs/CONTRACT_MATRIX.md
  harness/spec_map.yaml
  workers/__init__.py
  workers/contract.py
  workers/tests/test_mcp_worker.py

Created:
  core/orchestrator/tests/test_phase12_dispatcher_protocol.py
  docs/PHASE_12_4_IMPLEMENTATION_REPORT.md
  workers/invoker.py
  workers/tests/test_phase12_worker_invocation.py
```

---

## 9. Phase 12.4 Gate Status

$$\mathbf{PHASE\ 12.4\ GATE:\ PASS}$$

All contracts (`DISPATCH-001` through `DISPATCH-005`) are integration verified. Core boundaries remain inviolate. SCCA laws remain frozen.
