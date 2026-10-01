# Project Memory: 0021 — Phase 14.3 Autonomous Repository Worker & SE Protocol Foundation

**Date:** 2026-10-01  
**Author:** Ryu Autonomous Core Agent  
**Baseline:** Phase 14.2 (`b5a39a2`)  
**Status:** COMPLETE (GATE-14.3: PASS)  
**Governing ADR:** ADR-0044  
**Governing Architecture:** Space-Centric Cognitive Architecture (SCCA)  

---

## 1. Context & Purpose

Phase 14.2 established the bounded autonomous research worker and provenance tracking.
Phase 14.3 establishes the software-engineering inspection foundation (`core/space/repository_protocol.py` and `workers/repository/`), providing capability-controlled, strictly read-only repository inspection under SCCA governance.

Crucially, Phase 14.3 is **INSPECTION-ONLY**. It provides comprehensive inventorying, file classification, cryptographic content hashing, and **Python static AST parsing** (via `ast.parse`), paired with separate **polyglot test discovery**. Phase 14.3 strictly prohibits code modification, patch application, test execution, git operations, and command execution.

## 2. What Changed

1. **Domain-Neutral Core Repository Protocol (`core/space/repository_protocol.py`):**
   - 0 external dependencies (Python stdlib only, enforcing AGENTS.md §7).
   - Typed exceptions: `RepositoryError`, `RepositoryNotAuthorizedError`, `RepositoryNotFoundError`, `RepositoryRootInvalidError`, `PathTraversalError`, `SymlinkSecurityError`, `FileAccessDeniedError`, `FileTooLargeError`, `RepositoryLimitExceededError`, `SecretAccessDeniedError`, `ASTParseError`, `UnsupportedASTLanguageError`, `RepositorySpaceIsolationViolation`.
   - Data models: `FileCategory`, `FileAccessPolicy` (`ALLOWED`, `DENIED`, `MASKED`, `IGNORED`), `RepositoryIdentity` (with secret leakage detection in metadata), `FileMetadata`, `ProjectMetadata`, `ASTNodeSummary`, `ASTInspectionReport`, `RepositorySnapshot`, `RepositoryInspectionResult`.
   - Protocols: `@runtime_checkable` `RepositoryPolicyProtocol` and `RepositoryProtocol`.

2. **Repository Security & Safe Path Resolution (`workers/repository/security.py`):**
   - `resolve_safe_path()`: strict boundary enforcement preventing null-byte injections, drive escapes (`C:`), UNC paths (`\\server\share`), directory traversals (`../`), and symlink escapes outside the authorized repository root.
   - `evaluate_file_policy()` & `is_sensitive_path()`: automatic file classification (source, test, configuration, documentation, binary, build) and sensitive credential masking (`.env`, `id_rsa`, `id_ed25519`, `*.pem`, `*.key`). Sensitive files are recorded in inventory as `MASKED` with SHA-256 for evidence, but direct content reads are blocked.

3. **Deterministic Local Repository Inspector (`workers/repository/inspector.py`):**
   - Implements `RepositoryProtocol`:
     - Deterministic traversal with global lexicographical relative path sorting.
     - Hard resource ceilings: max file count, max total bytes, max depth, max per-file size.
     - Cryptographic SHA-256 content hashing.
     - **Python static AST parsing**: non-executing AST analysis for `.py` source files via standard library `ast.parse` (extracts classes, functions, line numbers, docstrings without executing top-level code or importing modules; non-Python files return `parse_status="unsupported_language"`).
     - **Polyglot test discovery**: passive pattern matching across pytest, jest/mocha, cargo test, and go test file naming conventions.
     - Passive project metadata discovery (`pyproject.toml`, `package.json`, `Cargo.toml`).

4. **Autonomous Repository Worker (`workers/repository/worker.py`):**
   - Implements `RepositoryWorker(BaseWorker)` for capabilities `repository.inspect`, `repo.inspect`, `repository.*`.
   - Actions: `inspect_tree`, `read_file`, `inspect_ast`, `discover_tests`.
   - Enforces SCCA Laws:
     - Law 1: Rejects cross-space execution and unauthorized repository access.
     - Law 2: Requires explicit capability assignment and active resource lease.
     - Law 4: Output snapshots and AST reports belong to Space.
     - Law 6: Failures mapped deterministically to failure taxonomy.
   - Untrusted repository content tagged with `taint: True` unconditionally (TAINT-001).
   - Generates `RepositorySnapshot`, RAW `ProvenanceRecord`, and manifest artifact on disk.

5. **WorkerInvoker Routing & Worker Layer Exports:**
   - Updated `workers/invoker.py` to route `repository.*` and `repo.*` capabilities to `RepositoryWorker`.
   - Updated `workers/__init__.py` to export `RepositoryWorker`.
   - Created `workers/repository/__init__.py`.

6. **Contract Matrix & Verification Tests:**
   - Created `core/space/tests/test_repository_protocol.py` (10 unit tests).
   - Created `workers/tests/test_phase14_3_repository_worker.py` (19 comprehensive tests).
   - Updated `docs/CONTRACT_MATRIX.md` for `REPO-001` status -> `INTEGRATION_VERIFIED`. Note: `REPO-002` through `REPO-005` remain `CONTRACT_ONLY` (deferred to Phase 14.4).

## 3. What Was Verified

- **Dedicated Phase 14.3 Tests:** 28 passed, 1 skipped (symlinks on Windows), 0 failures:
  - Repository Protocol Unit Tests: 10 passed (`core/space/tests/test_repository_protocol.py`).
  - Repository Worker & Security Tests: 18 passed, 1 skipped (`workers/tests/test_phase14_3_repository_worker.py`).
- **Regression Suites:** 805 passed, 13 skipped, 0 failures:
  - Core & Workers Regression: 480 passed, 1 skipped (contains the dedicated Phase 14.3 tests).
  - Harness Regression Suite: 325 passed, 12 skipped.
- **No-Modification Invariant Slice:** Verified byte-for-byte repository hash preservation before and after all inspection operations (zero files added, modified, or deleted).
- **End-to-End Vertical Slice:** SpaceKernel -> Plan -> Dispatcher -> Admission -> Lease -> WorkerInvoker -> RepositoryWorker -> Artifact -> VerifiedEvidence (exit code 0, SHA-256 match, taint preserved).
- **Deterministic Core Independence (`scripts/dep_guard.py`):** PASS — 0 forbidden imports in `core/`.
- **Contract & Codegen Sync (`scripts/contract_sync.py`):** PASS — 50 types in registry (`pulse-types.json`), 38 Sec 16 arch types.
- **Governance Audit (`scripts/v1_audit_governance.py`):** PASS — V1-005 satisfied.
- **Spec Coverage Audit (`scripts/v1_audit_spec_coverage.py`):** PASS — V1-001 satisfied (224 contract IDs).
- **Static Analysis:**
  - `ruff check`: 0 errors.
  - `mypy`: 0 issues found across all checked source files.

## 4. What Was Deferred (Non-Goals for Phase 14.3)

- **REPO-002..005**: File modification, code patching, unified diff application, hash reversibility, and line ceilings (deferred to Phase 14.4).
- **EVIDENCE-001..003**: Test execution and runner sandboxing (deferred to Phase 14.5).
- **REPAIR-001..004**: Test-repair loop and convergence integration (deferred to Phase 14.5).
- **General-Purpose Polyglot AST Analysis**: Python static AST parsing (`ast.parse`) implemented in 14.3; general multi-language semantic parsing is deferred.
- **Git Operations**: Git branching, commits, worktrees, or remote pushes.
- **Package Management**: Pip/npm/cargo package installation or environment mutation.
- **LLM Prompting**: LLM prompt generation or semantic code reasoning.

## 5. Next Steps

- Proceed to **Phase 14.4: Atomic Code Patching & Reversible Modification Engine**.
