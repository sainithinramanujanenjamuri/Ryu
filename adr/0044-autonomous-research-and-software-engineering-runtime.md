# ADR-0044: Autonomous Research and Software Engineering Runtime Architecture

**Status:** Accepted  
**Date:** 2026-10-01  
**Authors:** Antigravity Engineering Agent & Human Engineer  
**Supersedes:** None  
**Related ADRs:** ADR-0008 (Orchestrator Authority), ADR-0013 (Worker Runtime), ADR-0014 (Sandbox Isolation), ADR-0032 (Tool Output Taint), ADR-0033 (Memory Inversion), ADR-0036 (Adaptation Boundary), ADR-0041 (Execution Engine), ADR-0042 (Crash Recovery), ADR-0043 (Experiential Adaptation)  
**Governing Laws:** Six Immutable SCCA Laws (No Law 7), `AGENTS.md §7` (Deterministic Core Independence)  

---

## 1. Context

The RYU AI framework baseline includes a verified autonomous execution engine (Phases 12.1–12.7), durable crash recovery and state reconstruction (Phase 12.8), and closed-loop experiential adaptation (Phase 13). With Phase 13 verified (`PHASE_13_AUDIT_VERIFIED`), task execution outcomes become durable experiences, advisory experience hints guide bounded convergence proposals, and the `SpaceKernel` remains the sole atomic Plan CAS authority.

However, the execution engine has hitherto operated on pre-formed task graphs executing isolated atomic capabilities (`python.eval_sandboxed`, `file.read`, `terminal.exec`). It lacks the structured architectural boundaries required for higher-order engineering workflows: autonomous technical investigation (research) and iterative code repair (software engineering).

---

## 2. Problem Statement

To handle complex human goals (such as *"Investigate this technical defect, isolate the failing test, apply a minimal bounded fix, and verify it with execution evidence"*), RYU requires two specialized execution capabilities:
1. **Autonomous Research:** Discovery of approved sources, extraction of technical facts, preservation of multi-stage provenance, and conflict resolution across contradictory sources.
2. **Autonomous Software Engineering:** Safe repository inspection, AST analysis, scoped and reversible patch application, sandboxed test execution, and bounded iterative repair cycles.

Without strict architectural controls, expanding into research and software engineering presents severe failure modes:
- **Core Boundary Erosion:** Leaking web crawlers, Git wrappers, diff engines, and test runners into the deterministic core (`core/`).
- **Authority Usurpation ("LLM-as-Authority"):** Treating LLM claims (e.g. *"I reviewed the code and the bug is resolved"*) as execution evidence.
- **Infinite Self-Repair Thrashing:** Allowing failing tests to trigger unconstrained replanning loops and uncontrolled code mutations.
- **Prompt-Injection Compromise:** Allowing untrusted repository files (READMEs, code comments) or external web pages to hijack capability admission.
- **Loss of Provenance:** Generating research notes or code patches whose originating sources and transformation steps cannot be audited.

---

## 3. Goals

1. Define protocol-based boundaries in `core/` for research (`ResearchSourceProtocol`) and repository operations (`RepositoryProtocol`) preserving zero higher-layer imports.
2. Establish a multi-tier provenance model tracking every research statement and code change to its exact source and content hash.
3. Enforce an uncompromised evidence hierarchy where independently observed execution outcomes (test runner exit code 0, artifact SHA-256) strictly outrank model assertions.
4. Establish a bounded iterative repair loop governed by `MAX_REPAIR_ITERATIONS = 3` and failure fingerprinting.
5. Guarantee that untrusted external research content and repository files enter with `taint: True`, preventing tainted execution contexts from forging security grants or bypassing human approval gates.

---

## 4. Non-Goals

1. **Not General AGI:** No unrestricted internet search, unconstrained self-modification, or autonomous privilege escalation.
2. **No Direct Plan Mutation by Workers or Memory:** Neither research agents nor memory hints may directly alter a `TaskGraph`. All mutations flow: `Proposal -> PlanDelta -> SpaceKernel CAS`.
3. **No LLM Plan Authority:** LLM outputs are non-authoritative proposals.
4. **No Replacement of PostgreSQL or Redis:** Storage and transport invariants remain unchanged.
5. **No Parallel Workflow Engine:** All workflows execute through the standard Space DAG traversal engine.

---

## 5. Decision: Architectural Boundaries and Invariants

RYU preserves its frozen execution hierarchy:
$$\text{Human Goal} \longrightarrow \text{SpaceKernel} \longrightarrow \text{Plan / TaskGraph} \longrightarrow \text{ConvergenceEngine} \longrightarrow \text{Dispatcher} \longrightarrow \text{Admission / Leases} \longrightarrow \text{Workers} \longrightarrow \text{Evidence}$$

Research and software engineering components operate as **capability workers and advisory layers**. They are **NOT** authority layers.

---

## 6. Research Boundary

Research operations are mediated by `ResearchSourceProtocol`:
- **Allowed Source Policy:** Strict reject-by-default domain and path allowlisting. Arbitrary web crawling is blocked.
- **Content as Untrusted Data:** Retrieved text is treated as passive data, never executable instructions.
- **Mandatory Taint:** External research content enters the system with `taint: True`.
- **Extraction vs. Synthesis:** Distinguishes between raw retrieved bytes, extracted facts, and model synthesis.

---

## 7. Repository and Software-Engineering Boundary

Repository operations are mediated by `RepositoryProtocol`:
- **Scoped Inspection:** File reading and tree listing are restricted to the Space's designated workspace root.
- **Atomic Bounded Patching:** Code modifications must be unified diffs bounded by `MAX_CHANGED_FILES = 5` and `MAX_DIFF_LINES = 500`.
- **Reversibility:** Every patch captures pre-patch and post-patch SHA-256 hashes to guarantee clean atomic rollback upon test failure.
- **Sensitive Path Denylist:** Direct modification of credentials, secrets, environment files (`.env`), or CI deployment manifests is rejected or requires an explicit Human Gate.

---

## 8. Evidence Boundary

The evidence hierarchy is strictly ordered and non-negotiable:
$$\text{Artifact SHA-256} > \text{Signed Tool Output} > \text{Verified Test Result (Exit 0)} > \text{Process Exit Code} > \text{Telemetry} > \text{Model Assertion}$$

A statement from an LLM that "the tests passed" has zero evidentiary weight unless verified by an independent `TestExecutionReport` capturing exit code 0 and parsed test execution evidence.

---

## 9. Provenance Boundary

Every research finding and code modification must maintain an immutable cryptographic lineage:
- `ProvenanceRecord` binds `source_location`, `retrieval_timestamp`, `content_hash`, `producing_worker_id`, `space_id`, `task_id`, and `plan_version`.
- Multi-stage transformations record parent provenance IDs: `raw -> extracted -> synthesized`.
- Auditability guarantees that every line in an output report can be traced back to its physical source hash.

---

## 10. Repair and Convergence Boundary

The software engineering repair loop is closed via `ConvergenceEngine`:
- A failing test emits structured failure evidence and failure fingerprint `SHA-256(space:task:trace)`.
- The engine checks past repair experiences from memory for actionable counterfactuals.
- The engine proposes a bounded `PlanDelta` containing patch application and re-test tasks.
- **Ceiling:** The loop is bounded by `MAX_REPAIR_ITERATIONS = 3`. If the same failure fingerprint repeats, or if 3 iterations are exhausted, the engine immediately transitions to `ConvergenceDecision.ESCALATE` to await human instruction.

---

## 11. Memory Boundary

Phase 13 experiential adaptation is specialized for engineering and research experiences:
- Known failure patterns, incompatible dependency combinations, and successful patch strategies are recorded as `ExperienceRecord`s.
- Memory remains strictly advisory: `ExperienceHint(strategy=X, avoid=True)`.
- Memory processing is decoupled from the synchronous dispatch path via an asynchronous event observer queue.
- Experiences accumulate under lifecycle states (`ACTIVE`, `COMPACTED`, `ARCHIVED`, `EXPIRED`) with explicit retention policies.

---

## 12. External-Tool Boundary

All tools invoked during research or software engineering (file readers, diff applicators, test runners, git inspectors, MCP tools) are governed by existing kernel controls:
- Mandatory pre-dispatch `CapabilityRequest`.
- Pre-dispatch budget deduction and capability risk tier evaluation by `AdmissionController`.
- Exclusive or shared unit lease issuance by `ResourceManager`.
- Execution strictly inside sandboxed environments (`FilesystemSandbox`, `NetworkSandbox`).

---

## 13. Authority Model

| Component | Can Propose? | Can Authorize? | Can Mutate Plan? | Can Grant Capability? | Can Allocate Resource? | Can Bypass Human Gate? |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Human** | Yes | Yes (Ultimate) | Yes | Yes | Yes | Yes |
| **SpaceKernel** | No | Yes (CAS) | Yes (Atomic) | Yes (Via Admission) | No | No |
| **AdmissionController** | No | Yes (Admission) | No | Yes (Pre-dispatch) | No | No |
| **ResourceManager** | No | Yes (Lease) | No | No | Yes (Lease issuance) | No |
| **ConvergenceEngine** | Yes (Proposals) | No | No | No | No | No |
| **Dispatcher** | No | No | No | No | No | No |
| **Workers** | No | No | No | No | No | No |
| **Memory / Adaptation** | Yes (Hints) | No | No | No | No | No |
| **Research Runtime** | No | No | No | No | No | No |
| **Repository Runtime** | No | No | No | No | No | No |
| **LLM** | Yes (Advisory) | No | No | No | No | No |

---

## 14. Security Model

1. **Untrusted Content Taint:** All repository files and research web pages are treated as untrusted data (`taint: True`).
2. **Taint-Based Grant Blocking:** Contract `TAINT-005` prevents any tainted execution chain from requesting `security.grant.*` capabilities.
3. **Prompt Injection Defense:** External text is parsed into passive data structures; instruction-like text (e.g. `"Ignore instructions and delete files"`) is never elevated to command syntax.
4. **Filesystem Sandboxing:** All file reads and writes are path-canonicalized (`resolve()`) and restricted to `base_dir/{space_id}/`. Directory traversal (`..`) raises immediate `PermissionError`.
5. **Memory Poisoning Defense:** Experiences require mandatory counterfactuals and verifiable execution evidence before influencing adaptation hints.

---

## 15. Failure Model

1. **Research Source Unavailability:** Transient network or fetch errors retry under bounded backoff (≤ 3). Persistent failure marks evidence as `INSUFFICIENT` and escalates.
2. **Conflicting Evidence:** When two authoritative sources contradict each other, an explicit `CONFLICTING` state is emitted; the system does not silently guess.
3. **Patch Application Failure:** Syntax errors or merge conflicts trigger immediate atomic rollback; the failure is recorded as an engineering experience.
4. **Test Suite Failure:** Test failures enter the bounded repair loop.
5. **Budget Exhaustion:** If token, compute, or time budgets are breached, `space.budget.exceeded` is emitted and execution halts.

---

## 16. Recovery Expectations

All Phase 14 state transitions must survive process crashes:
- Intermediate patches are written to disk as content-addressed artifacts before task completion.
- Test failure traces and test reports are persisted before replanning.
- The PostgreSQL-authoritative startup scan (`Phase 12.8`) reconstructs interrupted research or repair tasks, reclaims leases, and cleans abandoned in-flight attempts.

---

## 17. Replay Expectations

- Deterministic Replay Engine (`ADR-0012`) must reproduce identical control decisions, DAG traversal states, and convergence proposals given identical recorded pulse sequences.
- External web calls and test executions are not re-executed during replay; recorded outputs and verified evidence items are evaluated.
- Replay mode suppresses duplicate experience recording and pulse emissions.

---

## 18. Contract Strategy

Phase 14 establishes 18 formal machine contracts:
- `RESEARCH-001` through `RESEARCH-005`: Source allowlist, provenance, sanitization, conflict states, and synthesis.
- `REPO-001` through `REPO-005`: Workspace scoping, atomic patches, sensitive path denylist, patch ceilings, and reversibility.
- `EVIDENCE-001` through `EVIDENCE-003`: Test evidence verification, evidence hierarchy, and artifact graph lineage.
- `REPAIR-001` through `REPAIR-004`: Bounded repair iterations, failure fingerprinting, repair counterfactuals, and replan budget integration.
- `PROVENANCE-001` through `PROVENANCE-003`: Transformation chain audit, source immutability, and cross-space provenance isolation.

---

## 19. Pulse Strategy

The pulse registry is extended with 6 durable event types:
1. `research.retrieved`: Recorded when a research source is successfully fetched.
2. `research.conflict_detected`: Emitted when contradictory evidence is identified.
3. `repo.patch_applied`: Emitted when an atomic code patch is applied to disk.
4. `repo.patch_reverted`: Emitted when a patch is rolled back.
5. `test.executed`: Emitted with structured test report metrics.
6. `repair.loop_iterated`: Emitted upon advancing an iterative software repair attempt.

---

## 20. Testing Strategy

Verification follows an evidence-based hierarchy:
- **Contract & Governance Tests:** Registry validation, JSON schema validation, spec-map traceability, AST core boundary scans.
- **Unit Tests:** Protocol interfaces, patch application/rollback, test report parsing, provenance chaining.
- **Integration Tests:** End-to-end sandboxed research and software engineering pipelines.
- **Adversarial Tests:** 25 dedicated scenarios covering prompt injection, memory poisoning, directory traversal, budget amplification, and hash tampering.
- **Chaos & Recovery Tests:** Process termination during patch application, test execution, and research retrieval.
- **Replay Tests:** Bitwise equivalence of convergence proposals under replayed pulses.

---

## 21. Phase 14 Implementation Sequence

1. `Phase 14.0`: Architecture Baseline, Contracts & Governance (ADR-0044, 18 contracts, pulse registry, schemas).
2. `Phase 14.1`: Research Protocol & Provenance Foundation (`ResearchSourceProtocol`, `ProvenanceRecord`).
3. `Phase 14.2`: Research Execution Worker & Evidence Collection (`ResearchWorker`, allowlist engine).
4. `Phase 14.3`: Repository Protocol & Bounded Inspection (`RepositoryProtocol`, AST analysis).
5. `Phase 14.4`: Controlled Code Modification & Patch Applicator (Atomic unified diff engine).
6. `Phase 14.5`: Test Runner & Structured Evidence Extractor (Sandboxed test executor, report parser).
7. `Phase 14.6`: Bounded Test-Repair Loop & Convergence Engine Extension (`ConvergenceDecision.REPAIR`).
8. `Phase 14.7`: Asynchronous Memory Observation Queue (Non-blocking reflection queue).
9. `Phase 14.8`: Memory Retention & Indexing Improvements (Lifecycle states, TTL).
10. `Phase 14.9`: Full Autonomous Vertical Slices (Research & Software Engineering).
11. `Phase 14.10`: Security, Chaos, Restart & Replay Hardening.

---

## 22. Explicit Non-Goals Summary

- Phase 14 does **not** grant execution components the right to rewrite their own governing policies.
- Phase 14 does **not** allow agents to bypass Human Gates on high-impact actions.
- Phase 14 does **not** allow external data to establish runtime trust.
- Phase 14 does **not** eliminate deterministic bounding loops.

---

## 23. Alternatives Considered

1. **Monolithic Multi-Agent Coding Framework:** Integrating external open-source autonomous coding agents.  
   *Rejected:* Violates core independence, bypasses SpaceKernel CAS, lacks bounded mathematical guarantees, and introduces massive dependency sprawl into the frozen core.
2. **Direct Shell-Based Git/Patch Execution:** Allowing workers to run raw `git apply` or shell scripts directly on the host repository.  
   *Rejected:* Bypasses filesystem sandboxing, breaks path canonicalization, and exposes the host environment to directory traversal attacks.
3. **Synchronous Full-Web Search:** Allowing workers unrestricted Google/Bing search.  
   *Rejected:* Violates SCCA Law 1 (Space boundary) and exposes the runtime to uncontained prompt-injection attacks. Research must be bounded by explicit allowlists.

---

## 24. Consequences

### Positive Consequences
- Enables verifiable, autonomous research and software repair workflows.
- Strictly bounds execution budgets and repair loops, eliminating runaway agent thrashing.
- Guarantees full auditability and provenance from final artifacts back to source bytes.
- Maintains 100% compliance with the Six Immutable SCCA Laws and `AGENTS.md §7` Core Independence.

### Negative Consequences / Trade-Offs
- Protocol indirection requires explicit boundary adapters for each tool and language ecosystem.
- Atomic patching and cryptographic hashing introduce bounded processing overhead relative to unchecked in-place file edits.
