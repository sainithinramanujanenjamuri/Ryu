# ADR-0029: MCP Extensibility Wire Protocol and Transport Boundary

## Status
Accepted

## Context
SCCA §5 (Extensibility Layer) establishes the Model Context Protocol (MCP) as RYU AI's open extensibility wire protocol. MCP provides a standardized JSON-RPC 2.0 interface for LLMs and cognitive architectures to interact with tools, data resources, and prompt templates.

However, accepting arbitrary external tool connections introduces profound security risks, including unauthenticated network listeners, remote code execution, DNS rebinding, and data exfiltration.

## Problem
1. Which MCP protocol version and wire format will RYU support?
2. Which transport layers are permitted in Phase 9, and which are deferred?
3. How is the MCP server process isolated and monitored?

## Decision
1. **JSON-RPC 2.0 Wire Protocol:** RYU AI implements the Model Context Protocol specification over JSON-RPC 2.0. Messages adhere to standard schemas for `initialize`, `notifications/initialized`, `tools/list`, and `tools/call`.
2. **Strict `stdio` Transport (Phase 9):** Phase 9 limits MCP server communication strictly to local subprocesses via standard input/output pipes (`stdio`).
3. **Out-of-Scope Transports:** Remote network transports—including HTTP/SSE, WebSockets, streaming HTTP, and arbitrary Internet sockets—are explicitly deferred and OUT OF SCOPE for Phase 9. All MCP servers must be launched locally as contained child processes.
4. **Asynchronous Pipe Multiplexing & Watchdogs:** The `MCPClient` communicates over asynchronous non-blocking pipes with strict per-call timeouts (default 30 seconds). If a server hangs or fails to respond, the watchdog immediately terminates the subprocess via SIGTERM/SIGKILL (or Windows Job Object close) and releases held leases.
5. **No Network by Default:** Subprocesses launched for `stdio` MCP servers run under `NetworkPolicy(mode=DISABLED)` unless explicit network capability grants are approved.

## Alternatives Considered
- **HTTP/SSE Server Transport:** Rejected for Phase 9 because network-bound listeners expose unnecessary attack surfaces and loopback port contention before multi-platform node controls (Phase 11) are finalized.
- **In-Process Dynamic Import / Plugin Loading:** Rejected because executing third-party code inside the main RYU runtime process compromises crash containment and memory isolation.

## Consequences
- MCP servers run as independent OS processes isolated from RYU's runtime memory.
- Inter-process communication is auditable byte-for-byte over standard pipes.
- Remote MCP integration remains safely gated behind future multi-platform node phases.

## Date
2026-09-22

