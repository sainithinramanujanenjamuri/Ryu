# ADR-0031: MCP Sandbox Process Containment and Resource Quotas

## Status
Accepted

## Context
Executing third-party MCP servers and tools involves running untrusted code on the host operating system. Without strict process containment and resource boundaries, an adversarial or buggy tool could cause denial of service (fork bombs, CPU/RAM starvation), access unauthorized directories (e.g. host credentials, `.env`, `.git`), or establish unauthorized outbound network connections.

## Problem
1. How are local MCP server processes sandboxed on both Linux and Windows platforms?
2. What resource limits and quotas must be enforced?
3. How does the system ensure child processes are terminated cleanly without leaving orphaned zombies?

## Decision
1. **Reuse Phase 6 Sandbox Infrastructure:** MCP subprocesses are contained using the established Phase 6 `SandboxManager` (`workers/sandbox/manager.py`). No secondary or duplicate sandbox engine is introduced.
2. **Platform-Specific Containment:**
   - **Windows:** Wrapped in Windows Job Objects with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (terminates all child processes on parent exit), `JOB_OBJECT_LIMIT_PROCESS_MEMORY` (hard cap of 512MB), and `JOB_OBJECT_LIMIT_ACTIVE_PROCESS` (max 5 simultaneous processes).
   - **Linux:** Isolated via Linux namespaces (mount, PID, network) and `SeccompFilter` profiles blocking dangerous syscalls (`ptrace`, `sys_chroot`, `mount`, `reboot`).
3. **Strict Resource Quotas:**
   - Maximum RAM: 512 MB per process.
   - Maximum Execution Duration: 30.0 seconds per tool call.
   - Maximum Output Payload: 10 MB buffer limit (`max_output_bytes`).
   - Maximum Concurrency: Governed by `LeaseManager` process slot leases.
4. **Network Disabled by Default:** Subprocesses operate under `NetworkPolicy(mode=DISABLED)`. External outbound connections are blocked unless explicitly granted by human approval.
5. **Filesystem Whitelisting:** Subprocesses only have read/write access to `Space.root_dir` and an ephemeral temporary directory (`tmp_dir`). Traversal to `.env`, `.git`, or system directories is blocked with `SandboxViolationError`.

## Alternatives Considered
- **Docker Containers for every tool call:** Rejected for Phase 9 due to heavyweight container startup latency (~2s per call) compared to lightweight OS sandboxes (<50ms).
- **Unrestricted Local Subprocess Execution:** Rejected because it violates SCCA security containment.

## Consequences
- Malicious tools cannot escape their sandboxed directory or exhaust machine memory.
- Subprocesses cleanly terminate on timeout, eliminating zombie processes.
- High performance, sub-second execution overhead.

## Date
2026-09-22

