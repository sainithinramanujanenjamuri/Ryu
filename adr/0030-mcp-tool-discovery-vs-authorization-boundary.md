# ADR-0030: MCP Tool Discovery vs. Authorization Boundary

## Status
Accepted

## Context
When an MCP server connects, it exposes a list of available tools via the `tools/list` JSON-RPC method. In many naive agent architectures, discovered tools immediately become callable by agents. In an enterprise cognitive architecture governed by SCCA, this represents an unacceptable vulnerability: an untrusted or compromised MCP server could declare dangerous capabilities and immediately execute them.

## Problem
1. Does discovering an MCP tool automatically make it executable by agents?
2. How are MCP tools mapped into the RYU Tools Layer?
3. What is the default security posture for discovered tools?
4. How is namespace collision prevented between different MCP servers?

## Decision
1. **Core Invariant — Discovery $\neq$ Authorization:** Discovering a tool through `tools/list` registers the tool's schema in the Tools Layer, but DOES NOT grant execution authorization. An agent cannot invoke a discovered tool until the capability has been explicitly admitted by the Space Kernel's `AdmissionController`.
2. **Capability Namespacing (`REG-006`):** All MCP tools are registered in the Tools Layer under an explicit namespace:
   `mcp.<server_id>.<tool_name>`
   This prevents namespace collisions across servers and prevents external tools from spoofing native RYU capabilities (e.g. `file.read` vs `mcp.github.read`).
3. **Default High-Risk Classification:** All discovered third-party MCP tools are stamped with `risk_tier: high` by default. Under SCCA policy, high-risk tools always require fresh human re-approval for every request, regardless of prior approvals or session state.
4. **Schema Normalization:** Tool schemas returned by MCP servers are validated against JSON Schema Draft 2020-12. Malformed, incomplete, or recursive schemas are rejected at discovery time, preventing malformed payload injection into the cognitive planner.

## Alternatives Considered
- **Automatic Auto-Approval of Discovered Tools:** Rejected because it allows third-party tools to bypass human approval and admission control.
- **Flattened Tool Names:** Allowing an MCP server to declare a tool called `bash` or `read`. Rejected because it allows malicious servers to shadow native, trusted tools.

## Consequences
- Every MCP tool has an unambiguous, audit-traceable identifier (`mcp.<server_id>.<tool_name>`).
- Discovered tools remain passive until admitted by Space policy.
- Agents and planners receive valid, sanitized schemas only.

## Date
2026-09-22

