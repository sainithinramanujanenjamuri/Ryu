# RYU AI — Project Memory

## Entry 0011 — Phase 9 Skills & MCP Extensibility Layer

**Date:** 2026-09-22  
**Phase:** 9 — Skills & MCP Extensibility Layer  
**Status:** COMPLETE (PHASE 9 GATE: PASS)  
**Previous Baseline:** Phase 8.5 (Human Interaction Layer)

---

### Summary

Phase 9 establishes the secure, deterministic Skills Runtime and sandboxed Model Context Protocol (MCP) tool integration layer for RYU AI. Skills and MCP tools operate under strict Space authority with zero intrinsic capabilities, enforcing all 6 Space-Centric Cognitive Architecture (SCCA) laws.

1. **Cryptographic Skill Supply Chain & Strict SemVer Pinning (`skills/model.py`, `skills/registry.py`, ADR-0027):**
   - Cryptographic signing over `(skill_id, version, content_hash, risk_tier, registered_by)` via HMAC-SHA256.
   - Exact SHA-256 payload content verification (`compute_sha256_hash`).
   - Immutable risk tiers (`LOW` | `HIGH`) cryptographically locked at registration (`REG-004`).
   - Strict SemVer pinning (`REG-005`); dynamic `@latest` or unpinned range resolution is rejected synchronously.
   - Database persistence schema for PostgreSQL: `deploy/migrations/004_create_skills_and_mcp_registry_tables.sql`.

2. **Zero-Authority Skill Execution Boundary (`skills/contract.py`, `skills/executor.py`, ADR-0028):**
   - **Law 2 Invariant:** A Skill is a capability interface, NOT an authority boundary. Skills own zero execution permissions.
   - `SkillExecutor` intercepts all invocation attempts and delegates a formal `CapabilityRequest` to the authoritative Space Kernel `AdmissionController`.
   - Pre-dispatch schema validation against JSON Schema Draft-07 / 2020-12 specifications (`REG-002`, `SKILL-002`).

3. **Sandboxed MCP Subsystem (`skills/mcp/`, ADR-0029, ADR-0030, ADR-0031):**
   - Wire protocol: JSON-RPC 2.0 strictly over `stdio` pipes (`MCP-001`). Remote HTTP/SSE network endpoints remain explicitly out of scope for Phase 9.
   - Process containment: Subprocess sandboxing with strict timeouts, watchdog termination, and resource isolation (`CHAOS-MCP-001`, `CHAOS-MCP-002`).
   - Controlled tool discovery: `MCPDiscoveryService` enumerates tools via `tools/list` handshake and registers them into the `ToolRegistry` with mandatory namespace prefixing `mcp.<server_id>.<tool_name>` (`REG-006`, `MCP-002`).
   - Discovery does NOT grant execution authority; tools must still be admitted per-call.

4. **MCP Worker Integration (`workers/mcp/worker.py`, `workers/base.py`, MCP-003):**
   - `MCPWorker` subclasses `BaseWorker` to execute admitted MCP capabilities within sandboxed child processes.
   - Emits authoritative Pulses: `worker.tool.called`, `worker.tool.succeeded`, and `worker.tool.failed` conformant to contract schemas.
   - Enforces Space isolation: rejects any execution request where `request.space_id != worker.space_id` with `terminal.permission_denied` (Law 1, `SEC-MCP-005`).

5. **Tool Output Taint & Prompt Injection Defense (ADR-0032, SKILL-003, SEC-MCP-002):**
   - All tool execution outputs are treated strictly as passive structured data.
   - Every external tool response is stamped with `taint: True`.
   - Prompt injection canaries injected inside tool outputs remain inert passive data and cannot elevate privileges or clear taint.

---

### Architectural Invariants & Verification Evidence

1. **Law 1 — Everything Happens Inside a Space:**
   - Worker and Skill execution contexts require a valid `space_id`.
   - Cross-space calls are rejected pre-dispatch (`SEC-MCP-005`).
2. **Law 2 — Capabilities Are Requested, Never Owned:**
   - Skills declare required capabilities, but cannot execute without Space Kernel admission (`SKILL-001`, `SEC-MCP-001`).
3. **Law 3 — Components Communicate Through Pulses:**
   - Tool lifecycles emit `worker.tool.called`, `worker.tool.succeeded`, `worker.tool.failed` (`MCP-003`).
4. **Law 4 — Knowledge Belongs to the Space First:**
   - Tool results belong to the originating Space's timeline and audit store.
5. **Law 5 — Humans Define Goals; Ryu Organizes Execution:**
   - High-risk tools requiring human gates route through `security.grant.approved` tokens.
6. **Law 6 — Failures Are Contained, Escalated, and Never Silent:**
   - Subprocess timeouts, crashes, and JSON-RPC parse errors map to `failure-taxonomy.json` (`terminal.*` or `transient.*`) (`CHAOS-MCP-001..003`).

---

### Phase 9 Gate Verification Checklist

- [x] ADR-0027: Signed Skill & Tool Supply Chain Registry
- [x] ADR-0028: Skill Execution Boundary & Capability Request Delegation
- [x] ADR-0029: MCP Extensibility Wire Protocol & Transport Boundary (stdio JSON-RPC 2.0)
- [x] ADR-0030: MCP Tool Discovery vs Authorization Boundary
- [x] ADR-0031: MCP Sandbox Process Containment & Resource Limits
- [x] ADR-0032: Tool Output Taint & Prompt Injection Defense
- [x] Database migration: `deploy/migrations/004_create_skills_and_mcp_registry_tables.sql`
- [x] Unit test suite: 30 / 30 PASS (`skills/tests`, `workers/tests/test_mcp_worker.py`)
- [x] Harness test suite: 16 / 16 PASS (`harness/cases/skills`, `harness/cases/mcp`)
- [x] Total repository test suite: 540 passed, 11 skipped, 0 failures (34.93s)
- [x] Dependency guard: PASS (zero forbidden imports in `core/`)
- [x] Contract sync: PASS (all 38 frozen pulse types match)
- [x] Rust Node Runtime: PASS (`cargo check` clean in 2.42s)
- [x] Linting: PASS (`ruff check` clean across all skills & workers)
- [x] Type checking: PASS (`mypy` clean across all skills & workers)
- [x] Traceability: `harness/spec_map.yaml` updated with `REG-001..006`, `SKILL-001..003`, `MCP-001..003`, `SEC-MCP-001..005`, `CHAOS-MCP-001..006`
