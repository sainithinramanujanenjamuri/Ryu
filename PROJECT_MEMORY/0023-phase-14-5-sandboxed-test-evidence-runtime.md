# Project Memory: 0023 — Phase 14.5 Sandboxed Test Evidence Runtime

**Date:** 2026-10-01  
**Author:** Ryu Autonomous Core Agent  
**Baseline:** Phase 14.4 Evidence Hardened (`acc236b`)  
**Status:** COMPLETE (GATE-14.5: PASS)  
**Governing ADR:** ADR-0044  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  

---

## 1. Context & Purpose

Phase 14.4 established controlled, atomic, reversible code modification for unified diffs (`REPO-002..005`).  
Phase 14.5 establishes the sandboxed test-execution and structured evidence collection capability (`EVIDENCE-001..003`).

Phase 14.5 answers the architectural question:  
*"After RYU modifies a repository, can RYU execute the appropriate tests inside a controlled environment and independently determine what actually happened?"*

### Implemented Contracts
- **`EVIDENCE-001` (Sandboxed Test Runner Evidence Verification):** "Tests passed" strictly requires verified exit code 0 and structured `TestExecutionReport` confirmation from `TestOutputParser`; model claims and unparsed outputs are rejected.
- **`EVIDENCE-002` (Evidence Hierarchy & Model Assertion Subordination):** Model assertions cannot override failing test results, invalid process exits, or tampered artifact hashes. Contradictory runner output yields `INCONCLUSIVE`.
- **`EVIDENCE-003` (Artifact Graph Lineage & Relationship Tracking):** All produced artifacts record explicit `derived_from` and `validates` relations in metadata, linking them to target repositories and patches.

### Strict Scope Invariants
- **Strictly No Repair Loop:** Phase 14.5 terminates upon collecting and recording test evidence. It does not invoke `ConvergenceEngine`, does not generate patches, and does not alter Plan CAS state upon test failure. `REPAIR-001..004` remain deferred to Phase 14.6.
- **Zero Arbitrary Shell Execution:** No shell subprocesses (`shell=True`, `bash -c`, `cmd.exe /c`) are executed. All commands are validated against an allowlist of test runners (`pytest`, `python -m pytest`, `unittest`).
- **Zero Package Management:** All package installation commands (`pip`, `npm`, `cargo install`) are strictly blocked.

---

## 2. What Changed

1. **Domain-Neutral Core Test Protocol (`core/space/test_execution_protocol.py`):**
   - Core typed enums: `TestExecutionStatus`, `TestCaseStatus`, `TestRunnerType`.
   - Core data models: `TestExecutionLimits`, `TestCommand`, `TestExecutionRequest`, `TestFailure`, `TestCaseResult`, `TestSuiteResult`, `TestExecutionReport`, `TestEvidence`.
   - Protocol definition: `@runtime_checkable` `TestExecutionProtocol` defining `execute_tests(space_id, request)`.
   - Zero higher-layer or external imports (strictly verified by `scripts/dep_guard.py`).

2. **Deterministic Command Allowlist & Validator (`workers/test_runner/command_validator.py`):**
   - `validate_and_resolve_test_command(command, repo_root)`: enforces runner allowlist (`pytest`, `python -m pytest`, `unittest`), blocks shell metacharacters, blocks dangerous flags (`--pdb`, `--trace`, `--override-ini`), and prevents directory traversal outside `repo_root`.

3. **Deterministic Test Output Parser (`workers/test_runner/parser.py`):**
   - `TestOutputParser`: extracts test counts (passed, failed, skipped, errors), duration, and individual failure details (`TestFailure`).
   - Detects contradictions (e.g. exit code 0 with parsed failures, or non-zero exit code with no parsed failures) and marks evidence as `INCONCLUSIVE`.

4. **Sandboxed Executor (`workers/test_runner/executor.py`):**
   - `SandboxedtestExecutor`: executes tests using `SandboxManager` and `ProcessSandbox`. Enforces timeouts, memory limits, and process limits. Captures raw stdout and stderr.

5. **Autonomous Test Runner Worker (`workers/test_runner/worker.py`):**
   - Capability worker registered for `test.*` and `test_runner.*`.
   - Emits `test.executed` pulse validated against JSON schema in `contracts/registry/payload-schemas/test.executed.json`.
   - Produces content-addressed artifacts:
     - `{task_id}_test_report.json`
     - `{task_id}_test_stdout.log`
     - `{task_id}_test_failures.json` (if failures occurred)
   - Generates `ProvenanceRecord` cryptographically binding diff/report, task, space, and worker.
   - Enforces SCCA Law 1 (Space isolation) and Law 4 (taint propagation: `taint: True`).

6. **Worker Invoker Integration (`workers/invoker.py`):**
   - Auto-registers `TestRunnerWorker` for `test.` capabilities.

7. **Contract & Matrix Synchronization (`docs/CONTRACT_MATRIX.md`):**
   - Updated `EVIDENCE-001`, `EVIDENCE-002`, `EVIDENCE-003` to `INTEGRATION_VERIFIED`.

---

## 3. What Was Verified

### Test Counts & Execution Metrics (Zero Double Counting)

- **Dedicated Phase 14.5 Tests:**
  - `core/space/tests/test_test_execution_protocol.py`: 23 passed, 0 skipped, 0 failed.
  - `workers/tests/test_phase14_5_test_runner.py`: 43 passed, 0 skipped, 0 failed.
  - **Total Dedicated Phase 14.5 Tests:** **66 passed**, 0 skipped, 0 failed.

- **Full Regression Test Suites:**
  - `core` & `workers` suites: 617 passed, 1 skipped, 0 failed (contains the 66 dedicated tests).
  - `harness` suite: 325 passed, 12 skipped, 0 failed.
  - **Combined Regression Suite Total:** **942 passed**, 13 skipped, 0 failed.

### Vertical Slices Verified
1. **Vertical Slice 1 (Genuine Success):** Real pytest suite runs in sandbox, 2/2 passed, exit code 0, emits `test.executed` pulse, generates `*_test_report.json` and `*_test_stdout.log` with cryptographic provenance.
2. **Vertical Slice 2 (Genuine Failure with Strict Stop):** Failing test (`assert 1 == 999`), exit code 1, extracts structured `TestFailure`, emits warning pulse, generates `*_test_failures.json`. Verifies strict stop: 0 patches applied, 0 repair pulses (`repair.loop_iterated`), 0 replan deltas.
3. **Vertical Slice 3 (Timeout Enforcement):** Bounded execution terminates hung test process (`time.sleep(10)`) at 500ms quota, exit code 124, status `timed_out`.

### Static Analysis & Governance Audits
- `scripts/dep_guard.py`: PASS (0 forbidden imports in `core/`).
- `scripts/contract_sync.py`: PASS (all 38 architecture types in registry; all 50 registered types accounted for).
- `ruff check`: PASS (0 errors across entire repository).
- `mypy`: PASS (0 type errors).
- `cargo check`: PASS (Rust node_runtime clean compile).
- `scripts/v1_audit_governance.py`: PASS (V1-005 PASS).
- `scripts/v1_audit_spec_coverage.py`: PASS (V1-001 PASS).

---

## 4. What Remains / Next Steps (Phase 14.6)

Phase 14.5 provides the verified evidence foundation for Phase 14.6:
- Autonomous Test-Repair Loop (`REPAIR-001..004`)
- Diagnostic-to-patch convergence
- Bounded repair iteration ceilings (`MAX_REPAIR_ITERATIONS = 3`)
- Failure fingerprinting and infinite loop prevention
- Repair memory counterfactual guidance (`ExperienceHint`)
