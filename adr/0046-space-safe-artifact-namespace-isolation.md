# ADR-0046 — Space-Safe Artifact Namespace Isolation

**Status:** Accepted  
**Date:** 2026-10-04  
**Phase:** 15.2 — Space-Safe Artifact Namespace Isolation (Finding F-02 — P0)  
**Supersedes:** (none — builds upon ADR-0033, ADR-0041, ADR-0044)  
**See also:** ADR-0001 (Monorepo Structure), ADR-0003 (Plan CAS Versioning), ADR-0033 (Space Memory Isolation), ADR-0041 (Dispatcher & Plan Convergence), ADR-0044 (Autonomous SE Runtime), ADR-0045 (Durable PostgreSQL Plan Store)

---

## 1. Context / Problem

The Phase 15 Architectural Audit (`docs/PHASE_15_ARCHITECTURE_AUDIT.md`) identified a critical isolation vulnerability in the RYU runtime, classified as **Finding F-02 (Unpartitioned Artifact Namespace — P0)**.

In the Phase 14.8 operational baseline (`7d4198a`), worker capabilities generated filesystem artifacts into unpartitioned subdirectories rooted directly under the base working directory:

```text
<base_working_dir>/artifacts/repository/<task_id>_*
<base_working_dir>/artifacts/test_runner/<task_id>_*
<base_working_dir>/artifacts/research/<task_id>_*
<base_working_dir>/artifacts/python/
<base_working_dir>/artifacts/shell/
```

### Architectural Deficiencies Identified:

1. **SCCA Law 1 Violation:** SCCA Law 1 mandates that *Everything Happens Inside a Space*. Artifact paths omitted `space_id` entirely, storing physical runtime artifacts outside Space authority boundaries.
2. **Cross-Space Collision Risk:** Two distinct Spaces executing tasks with identical or deterministic task IDs (e.g., `task-001`, `inspect_tree`, `patch_manifest.json`) wrote to identical paths, causing race conditions, file clobbering, and data corruption during concurrent execution.
3. **Evidence Validation Boundary Bypass:** In `core/orchestrator/dispatch_model.py`, `_is_safe_artifact_path` only verified that the artifact path was contained within the global `base_working_dir`. It had no mechanism to verify that an artifact submitted as evidence belonged to the executing Space, creating an opening for cross-space artifact injection and evidence tampering.
4. **Worker Contract Disconnect:** `workers.contract.Artifact` lacked an explicit `space_id` field, obscuring provenance tracking across the invocation boundary.

---

## 2. Decision

We establish strict Space-scoped physical artifact partitioning across the entire RYU runtime and worker execution hierarchy.

### 2.1 Authoritative Physical Directory Structure

All capability-generated artifacts must be physically located within their owning Space's directory subtree:

```text
<base_working_dir>/artifacts/<space_id>/<worker_namespace>/<filename>
```

Canonical worker namespaces:
- `repository`
- `test_runner`
- `research`
- `python`
- `shell`
- `file`

### 2.2 Core Boundary Independent Resolver (`core/space/artifact_paths.py`)

In accordance with the Core Boundary Rule (AGENTS.md Section 7), path resolution logic must reside in `core/space/artifact_paths.py` and must NEVER import from `agents/`, `workers/`, `skills/`, `workflows/`, `channels/`, `memory/`, or `llm/`.

Key functions:
- `validate_space_id(space_id: str) -> str`: Rejects traversal sequences (`..`), separators (`/`, `\\`), colons, null bytes, Windows reserved device names, and non-alphanumeric tokens outside `[-_.]`.
- `validate_worker_namespace(worker_type: str) -> str`: Validates canonical or well-formed worker identifiers.
- `validate_artifact_filename(filename: str) -> str`: Validates filenames against traversal, colons, null bytes, and reserved device names.
- `get_space_artifact_dir(base_dir, space_id, worker_type=None) -> Path`: Resolves authoritative directory with strict containment validation.
- `resolve_artifact_path(base_dir, space_id, worker_type, filename) -> Path`: Resolves target artifact path ensuring containment within `<base_dir>/artifacts/<space_id>/<worker_type>`.
- `is_safe_artifact_path(path, base_dir, space_id, worker_type=None) -> bool`: Pure containment predicate used by Dispatcher and kernel checks.

### 2.3 Dispatcher Evidence Hardening (`core/orchestrator/dispatch_model.py`)

`_is_safe_artifact_path` is updated to require `space_id` and validate strict containment within `<base_dir>/artifacts/<space_id>`:
- Disallows loose fallbacks to `<base_dir>`.
- Rejects paths outside the designated `<space_id>` directory.
- Rejects directory traversal attempts (`..`, symlinks escaping root, absolute drive jumps).

### 2.4 Worker Hierarchy Alignment

1. **Worker Contract (`workers/contract.py`):** Added `space_id: str = ""` to `Artifact` dataclass and its serialized representation.
2. **Worker Invoker (`workers/invoker.py`):** Updated `TaskExecutionResult` generation to pass complete artifact dictionaries including `space_id`.
3. **Execution Workers:**
   - `workers/repository/worker.py`: Inspection manifests, patch diffs, patch manifests, and rollback manifests are generated strictly via `resolve_artifact_path`.
   - `workers/test_runner/worker.py`: Test reports, stdout logs, and failure traces use `resolve_artifact_path`.
   - `workers/research/worker.py`: Raw retrievals, extracted facts, and synthesis reports use `resolve_artifact_path`.
   - `workers/python/worker.py`: Execution sandboxes partition working directories by `space_id`.
   - `workers/shell/worker.py`: Execution sandboxes partition working directories by `space_id`.
   - `workers/file/worker.py`: File artifacts explicitly set `space_id`.

---

## 3. Consequences

### Positive Consequences
- **Strict SCCA Alignment:** Resolves Finding F-02 (P0), restoring compliance with SCCA Law 1 (Everything happens inside a Space) and Law 4 (Knowledge belongs to the Space first).
- **Zero Cross-Space Collisions:** Multiple Spaces running concurrently can execute identical task IDs or workflows with zero risk of artifact collision or data corruption.
- **Fail-Closed Evidence Verification:** Dispatcher immediately rejects any artifact not contained within `<base_dir>/artifacts/<space_id>`.
- **Architectural Boundary Intact:** Pure core implementation verified with zero forbidden imports (`scripts/dep_guard.py` PASS).

### Negative / Operational Consequences
- **Legacy Path Incompatibility:** Artifacts produced by pre-Phase 15.2 code outside `<space_id>` are strictly rejected and will not pass Dispatcher evidence verification.
- **Strict Path Validation:** Paths containing invalid characters, traversal tokens, colons, or null bytes raise immediate validation exceptions.
