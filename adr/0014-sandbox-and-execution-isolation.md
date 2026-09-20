# ADR-0014: Multi-Layer Execution Sandbox and Resource Isolation

## Status
Accepted (Phase 6)

## Context
Executing untrusted code or automated shell scripts on a host risks filesystem corruption, credential theft, cross-Space data leaks, denial of service through runaway processes/fork bombs, and unauthorized network egress. A robust multi-layer sandbox is required to constrain Worker capabilities.

## Decision
1. **Multi-Layer Sandbox Architecture:**
   Worker execution is isolated through a modular `SandboxManager` composed of:
   - `FilesystemSandbox`: Path canonicalization, strictly bounded read/write directory trees, and explicit denylists.
   - `NetworkSandbox`: Policy-driven egress controls (`DISABLED`, `RESTRICTED`, `ALLOWED`).
   - `ProcessSandbox`: Isolated working directory, sanitized environment variables, watchdog timeout timer, and process tree termination.
2. **Filesystem Policy & Deny-by-Default:**
   - Every file operation requires path canonicalization (resolving symlinks and `..` traversals).
   - Unspecified paths are DENIED by default.
   - Protected paths (e.g. `.git`, `.env`, `core/`, ssh keys, system files like `/etc/passwd` or Windows `System32`) are strictly forbidden.
   - Cross-Space path access is forbidden; workers can only access their allocated Space workspace and artifact paths.
3. **Network Isolation:**
   - Default network policy for computation and file workers is `DISABLED`.
   - Network fetchers operate under `RESTRICTED` policy with explicit destination host/port allowlists.
   - Loopback and unauthorized external outbound sockets are blocked.
4. **Process Tree Lifecycle & Orphan Elimination:**
   - Subprocesses are spawned within an isolated working directory with stripped environment variables (sensitive host tokens, AWS/OpenAI keys, and path secrets removed).
   - A dedicated watchdog timer enforces `timeout_seconds`.
   - On completion, timeout, cancellation, or crash, the sandbox invokes a recursive process-tree termination to guarantee that zero orphan child processes survive.

## Consequences
- Workers cannot escape to host root, system directories, or other Spaces.
- Runaway subprocesses or fork bombs are contained and terminated cleanly.
- Network exfiltration attempts are blocked at the sandbox boundary.
