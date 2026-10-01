# RYU AI — Phase 14.5 Verification Report
## Sandboxed Test Runner & Structured Evidence Extractor

**Milestone:** Phase 14.5 — Sandboxed Test Evidence Runtime  
**Status:** GATE-14.5: PASS  
**Date:** 2026-10-01  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  
**Governing ADR:** ADR-0044 (`adr/0044-autonomous-research-and-software-engineering-runtime-architecture.md`)  
**Previous Baseline:** Phase 14.4 Evidence Hardened (`acc236b`)  

---

## 1. Executive Summary

Phase 14.5 establishes a deterministic, sandboxed, capability-controlled test execution and evidence collection capability for the RYU AI Framework. Operating under the governing principles of ADR-0044 and SCCA Law 1 (Space Isolation), Law 2 (Capabilities Requested, Never Owned), Law 3 (Typed Pulse Communication), and Law 6 (Deterministic Escalation), Phase 14.5 enables RYU to execute test suites in isolated sandboxes and convert their runtime outcomes into structured, cryptographically provable evidence.

### Verified Contracts
- **EVIDENCE-001 (Sandboxed Test Runner Evidence Verification):** Exit code 0 alone is rejected as proof of success; success requires structured test report confirmation from `TestOutputParser`.
- **EVIDENCE-002 (Evidence Hierarchy & Model Assertion Subordination):** Model assertions cannot override failing exit codes, unparsed crashes, or failed test counts.
- **EVIDENCE-003 (Artifact Graph Lineage & Relationship Tracking):** All produced artifacts (`*_test_report.json`, `*_test_stdout.log`, `*_test_failures.json`) record cryptographic provenance and directional lineage (`derived_from`, `validates`).

### Strict Architectural Boundaries & Deferred Capabilities
- **Strictly No Repair Loop:** Phase 14.5 cleanly terminates upon collecting and recording test evidence. It does not invoke `ConvergenceEngine`, does not generate patches, and does not alter Plan CAS state upon test failure. `REPAIR-001..004` remain deferred to Phase 14.6.
- **Zero Shell Execution:** No shell subprocesses (`shell=True`, `bash -c`, `cmd.exe /c`) are executed. All commands are strictly validated against an allowlist of test runners (`pytest`, `python -m pytest`, `unittest`).
- **Zero Package Management:** All package installation commands (`pip`, `poetry`, `npm`, `cargo install`) are strictly blocked.

---

## 2. Test Execution Metrics & Regression Suite Results

Test metrics are reported with strict separation between dedicated phase tests and full regression runs to ensure complete transparency without double-counting.

| Test Suite | Passed | Skipped | Failed | Total Items |
| :--- | :--- | :--- | :--- | :--- |
| **Dedicated Phase 14.5 Protocol Tests** (`core/space/tests/test_test_execution_protocol.py`) | 23 | 0 | 0 | 23 |
| **Dedicated Phase 14.5 Worker Tests** (`workers/tests/test_phase14_5_test_runner.py`) | 43 | 0 | 0 | 43 |
| **Total Dedicated Phase 14.5 Tests** | **66** | **0** | **0** | **66** |
| *Core & Workers Regression Suite* (`core`, `workers`) | 617 | 1* | 0 | 618 |
| *Harness Regression Suite* (`harness`) | 325 | 12** | 0 | 337 |
| **Combined Full Regression Total** | **942** | **13** | **0** | **955** |

*\* The 1 skipped test in `workers/` is a Windows symlink permission skip in `test_phase14_3_repository_worker.py`.*  
*\*\* The 12 skipped tests in `harness/` are integration tests requiring live PostgreSQL/Redis or elevated symlink privileges.*  
*Note: Dedicated Phase 14.5 tests (66) are fully included within the Core & Workers regression count (617).*

---

## 3. Verified Vertical Slices

### Vertical Slice 1: Genuine Test Success
- **Scenario:** Valid repository containing passing pytest tests (`test_arithmetic`, `test_string`).
- **Execution:** Dispatched through `TestRunnerWorker`, executed inside isolated process sandbox.
- **Outcome:** Exit code 0, 2/2 passed, 0 failed.
- **Evidence Produced:**
  - `*_test_report.json` with SHA-256 hash and lineage metadata (`validates: repo:...`).
  - `*_test_stdout.log` capturing stdout.
  - `ProvenanceRecord` with canonical hash and `TransformationStage.RAW`.
  - Emitted `test.executed` pulse with `Severity.INFO` and `taint=True`.

### Vertical Slice 2: Genuine Test Failure with Strict Stop
- **Scenario:** Repository containing failing test (`assert 1 == 999`).
- **Execution:** Dispatched through `TestRunnerWorker`, executed inside sandbox.
- **Outcome:** Exit code 1, 0 passed, 1 failed.
- **Evidence Produced:**
  - `*_test_report.json` indicating `process_failed`.
  - `*_test_failures.json` containing structured failure trace and message `"Expected 1 to equal 999"`.
  - Emitted `test.executed` pulse with `Severity.WARNING`.
- **Boundary Verification:** Strict stop verified; zero patches generated, zero repair pulses (`repair.loop_iterated`), and no calls to convergence engine.

### Vertical Slice 3: Bounded Timeout Enforcement
- **Scenario:** Test suite containing hung test process (`time.sleep(10)`).
- **Execution:** Dispatched with quota `timeout=0.5s`.
- **Outcome:** Sandbox forcefully terminates process, returns exit code 124, status `timed_out`.
- **Evidence Produced:** Handled as recoverable timeout error with `transient.timeout` classification and `test.executed` pulse with `exit_code: 124`.

---

## 4. Security & Isolation Verification

1. **Deterministic Command Allowlist:** Only `pytest`, `python -m pytest`, and `unittest` are accepted. Prohibited tools (`pip`, `curl`, `git`, `bash`, `powershell`) and dangerous flags (`--pdb`, `--trace`, `--override-ini`) are rejected with `TestCommandValidationError`.
2. **Path Traversal Defenses:** Target paths or working directories attempting directory traversal (`..`, `../../`) are rejected immediately during `TestCommand` construction and validation.
3. **Shell Injection Prevention:** Metacharacters (`;`, `&&`, `||`, `|`, `>`, `<`, `$()`, `` ` ``, `\n`, `\x00`) in runner, arguments, or target paths trigger instant rejection.
4. **Repository State Continuity:** Verifies pre-execution repository hash before running tests to prevent stale or race-condition testing.
5. **Cross-Space Isolation:** Requests originating from outside the worker's bound Space are rejected with `terminal.permission_denied` (SCCA Law 1).
6. **Model Assertion Subordination:** Output parser deterministically enforces exit codes; adversarial attempts to claim success in stdout while exit code is non-zero result in `INCONCLUSIVE` status.
7. **Taint Propagation:** All emitted `test.executed` pulses and resulting evidence carry `taint=True` (SCCA Law 4).

---

## 5. Implementation Files

| File | Purpose |
| :--- | :--- |
| `core/space/test_execution_protocol.py` | Core data models, limits, command definitions, reports, and `TestExecutionProtocol` |
| `core/space/tests/test_test_execution_protocol.py` | 23 unit tests verifying protocol models, validation, and immutability |
| `workers/test_runner/command_validator.py` | Runner allowlist, argument sanitization, path traversal checks |
| `workers/test_runner/parser.py` | Deterministic pytest and unittest output parser with contradiction detection |
| `workers/test_runner/executor.py` | Sandboxed execution using `SandboxManager` and `ProcessSandbox` |
| `workers/test_runner/worker.py` | `TestRunnerWorker` implementation, artifact creation, provenance, pulse emission |
| `workers/test_runner/__init__.py` | Module export declarations |
| `workers/invoker.py` | Registration of `TestRunnerWorker` for `test.*` and `test_runner.*` capabilities |
| `workers/tests/test_phase14_5_test_runner.py` | 43 unit, security, and full-pipeline integration tests |

---

## 6. Static Analysis & Verification Audits

- **AST Core Boundary (`scripts/dep_guard.py`):** PASS (0 forbidden imports in `core/`).
- **Contract Synchronization (`scripts/contract_sync.py`):** PASS (all 38 architecture types in registry; all 50 registered types accounted for).
- **Ruff Linter (`ruff check`):** PASS (0 errors across entire repository).
- **Type Checker (`mypy`):** PASS (0 issues found).
- **Rust Node Runtime (`cargo check`):** PASS (clean compile in 0.15s).
- **Governance Audit (`scripts/v1_audit_governance.py`):** PASS (V1-005 PASS).
- **Spec Coverage Audit (`scripts/v1_audit_spec_coverage.py`):** PASS (V1-001 PASS).

---

## 7. Next Steps (Phase 14.6)

Phase 14.5 provides the verified evidence generation foundation required for Phase 14.6:
- Autonomous Test-Repair Loop (`REPAIR-001..004`)
- Diagnostic-to-patch convergence
- Bounded repair iteration ceilings (`MAX_REPAIR_ITERATIONS = 3`)
- Failure trace fingerprinting and loop detection
