# Project Memory: 0028 — Phase 15.2 Space-Safe Artifact Namespace Isolation

**Date:** 2026-10-04  
**Author:** Ryu Autonomous Core Agent  
**Baseline:** Phase 15.1 Hardened (`75afd6b`)  
**Status:** COMPLETE (GATE-15.2: PASS)  
**Governing ADR:** ADR-0046 (Space-Safe Artifact Namespace Isolation)  
**Governing Contract:** SPACE-ART-001 (Space-Safe Artifact Namespace Isolation)  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  

---

## 1. Context & Purpose

The Phase 15 Architecture Audit (`docs/PHASE_15_ARCHITECTURE_AUDIT.md`) identified **Finding F-02 (Unpartitioned Artifact Namespace — P0)** as a critical blocker for concurrent multi-space execution.

Prior to Phase 15.2:
- Capability workers wrote filesystem artifacts to `<base_working_dir>/artifacts/<worker_namespace>/<task_id>_*`, omitting `space_id`.
- This violated SCCA Law 1 (*Everything Happens Inside a Space*) and SCCA Law 4 (*Knowledge Belongs to the Space First*).
- Concurrent execution across multiple Spaces with identical or common task IDs caused namespace collisions, overwrites, and race conditions.
- Dispatcher evidence verification checked only against `<base_working_dir>`, permitting cross-space artifact injection.

Phase 15.2 strictly resolves Finding F-02 by enforcing Space-isolated artifact directory partitioning and fail-closed Dispatcher verification.

---

## 2. What Changed

1. **Authoritative Core Path Resolver (`core/space/artifact_paths.py`):**
   - Implemented `validate_space_id`, `validate_worker_namespace`, and `validate_artifact_filename`.
   - Implemented `get_space_artifact_dir`, `resolve_artifact_path`, and `is_safe_artifact_path`.
   - Enforces strict canonical partitioning: `<base_working_dir>/artifacts/<space_id>/<worker_namespace>/<filename>`.
   - Prevents traversal tokens (`..`), separators, colons, null bytes, and Windows reserved device names.
   - Strictly Core-independent (zero imports from `workers/`, `agents/`, `skills/`, `workflows/`, `channels/`, `memory/`, or `llm/`).
   - Exported symbols in `core/space/__init__.py`.

2. **Dispatcher Evidence Verification Hardening (`core/orchestrator/dispatch_model.py`):**
   - Updated `_is_safe_artifact_path` to delegate to `core.space.artifact_paths.is_safe_artifact_path`.
   - Requires `space_id` matching the execution context and verifies strict physical containment within `<base_working_dir>/artifacts/<space_id>`.
   - Rejects legacy unpartitioned artifact paths and path traversal attempts.

3. **Worker Contract Alignment (`workers/contract.py`):**
   - Added `space_id: str = ""` field to `Artifact` dataclass.
   - Included `space_id` in `Artifact.to_dict()` serialization.

4. **Worker Invoker (`workers/invoker.py`):**
   - Preserved full artifact dictionary including `space_id` in `TaskExecutionResult.artifacts`.

5. **Capability Workers Partitioning:**
   - `workers/repository/worker.py`: Inspection manifests, patch diffs, patch manifests, and rollback manifests use `resolve_artifact_path`. Switched to `write_bytes` to prevent Windows CRLF newline conversions from skewing SHA-256 hashes.
   - `workers/test_runner/worker.py`: Test reports, stdout logs, and failure traces use `resolve_artifact_path` and `write_bytes`.
   - `workers/research/worker.py`: Raw retrievals, extracted facts, synthesis reports, and summaries use `resolve_artifact_path` and `write_bytes`.
   - `workers/python/worker.py`: Execution sandboxes partition working directory by `space_id`.
   - `workers/shell/worker.py`: Execution sandboxes partition working directory by `space_id`.
   - `workers/file/worker.py`: Emitted file artifacts set `space_id`.

6. **Contract Matrix & Spec Mapping:**
   - Added contract `SPACE-ART-001` and transitioned `SPACE-004` to `INTEGRATION_VERIFIED` in `docs/CONTRACT_MATRIX.md`.
   - Registered `SPACE-ART-001` in `harness/spec_map.yaml`.

---

## 3. What Was Verified

1. **Phase 15.2 Dedicated Test Suite (`workers/tests/test_phase15_2_artifact_isolation.py`):**
   - **24 tests passed** (100% pass rate across 5 consecutive runs).
   - Covers ART-001 through ART-018 and Adversarial Matrix ADV-ART-01 through ADV-ART-08:
     - `ART-001`: RepositoryWorker manifests reside in `artifacts/<space_id>/repository/`.
     - `ART-002`: TestRunnerWorker reports reside in `artifacts/<space_id>/test_runner/`.
     - `ART-003`: ResearchWorker artifacts reside in `artifacts/<space_id>/research/`.
     - `ART-004`: PythonWorker sandbox partitioned by `space_id`.
     - `ART-005`: ShellWorker sandbox partitioned by `space_id`.
     - `ART-006`: Dispatcher accepts strictly space-contained artifacts.
     - `ART-007`: Cross-space artifact rejection (Space B artifact rejected in Space A).
     - `ART-008`: Worker artifact directory escapes space root rejected.
     - `ART-009`: Filename path traversal escapes worker directory rejected.
     - `ART-010`: Legacy unpartitioned artifact fallback rejected.
     - `ART-011`: Space ID validation (rejects `..`, separators, colons, Windows reserved names).
     - `ART-012`: Artifact filename validation (rejects invalid chars and reserved names).
     - `ART-013`: Artifact SHA-256 integrity preserved.
     - `ART-014`: Concurrent multi-space execution with identical task IDs has zero collisions.
     - `ART-015`: Worker contract `Artifact.space_id` populated correctly.
     - `ART-016`: Worker invoker preserves `space_id` in execution results.
     - `ART-017`: FileWorker emits space-scoped artifacts.
     - `ART-018`: End-to-end integration across repository, test_runner, and dispatcher.
     - `ADV-ART-01`: Space ID path traversal rejected.
     - `ADV-ART-02`: Filename path traversal rejected.
     - `ADV-ART-03`: Windows reserved names rejected.
     - `ADV-ART-04`: Colons / alternate data streams rejected.
     - `ADV-ART-05`: Cross-space evidence substitution rejected.
     - `ADV-ART-06`: Hash tampering on disk detected and rejected.
     - `ADV-ART-07`: Disconnected base working directory rejected.
     - `ADV-ART-08`: Null byte injection rejected.

2. **Core Boundary & Hygiene Audits:**
   - `python scripts/dep_guard.py`: **PASS** (0 forbidden imports in `core/`).
   - `ruff check`: **PASS** (0 errors).
   - `mypy`: **PASS** (0 errors across modified core and worker contract files).
   - `python scripts/contract_sync.py`: **PASS** (38/38 architecture types in registry).
   - `python scripts/v1_audit_spec_coverage.py`: **PASS** (172 criteria, 235 contract IDs, 193 spec-map entries).
   - `python scripts/v1_audit_governance.py`: **PASS** (ADR Inventory 0001..0046 [PASS]).

3. **Regression Test Suite:**
   - Existing Phase 14 suites (`test_phase14_2` through `test_phase14_7`): **239 passed**.
   - Phase 14.8 workflow (`workflows/tests/test_phase14_8_software_engineering_workflow.py`): **39 passed**.
   - Phase 12 integrated execution (`test_phase12_integrated_execution.py`): **18 passed**.
   - Phase 12 end-to-end observation (`test_phase12_end_to_end_observation.py`): **23 passed**.

---

## 4. What Remains / Next Steps

Phase 15.2 addressed ONLY Finding F-02. Remaining findings from the authoritative Phase 15 Architecture Audit (`docs/PHASE_15_ARCHITECTURE_AUDIT.md`) remain deferred:
- **Finding F-03 (P1):** Bounded Pulse Retrieval (Phase 15.3).
- **Finding F-04 (P1):** Concurrent DAG Scheduler (Phase 16).
- **Finding F-05 (P1):** Full-history / Semantic Experience Retrieval (Phase 16+).
- **Finding F-06 (P1):** Durable Convergence State (Phase 15.4).
- **Finding F-07 (P2):** Agent Hierarchy Integration (Phase 16+).
- **Findings F-08 through F-13:** Deferred as defined in the Phase 15 Architecture Audit.

*Follow-up Operational Concern:* Coordinating inter-space concurrent write locking for shared physical git repositories is recognized as an independent operational concern distinct from artifact namespace isolation.
