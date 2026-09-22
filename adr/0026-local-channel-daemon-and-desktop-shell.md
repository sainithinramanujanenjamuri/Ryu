# ADR-0026: Local Channel Daemon and Desktop Shell Boundary

## Context
Phase 8 established the authoritative human approval gate (`token-hmac-v1`), attention budget, and command-line interface (`channels/cli/`). Phase 8.5 introduces an interactive developer CLI shell and a dedicated Windows Desktop Command Center (Tauri v2 + WebView2). To serve desktop and interactive tools without granting them internal authority, a local adapter boundary is required.

## Decision
1. **Local Channel Daemon as Adapter, Not Authority:**
   - The Local Channel Daemon (`channels/daemon/`) operates strictly as a channel adapter.
   - It possesses **zero independent cognitive, execution, resource, capability, or approval authority**.
   - It acts purely as a local transport bridge between external human interaction surfaces (CLI, Desktop App) and the authoritative Space Kernel / ApprovalManager / Pulse Bus.
   - Any crash, restart, or failure of the daemon has zero impact on Space state, Admission Control, or running tasks.

2. **Loopback Transport & Bearer Authentication:**
   - The daemon binds strictly to local loopback (`127.0.0.1` or `::1`). Binding to non-loopback addresses is prohibited.
   - All HTTP and WebSocket requests require a local Bearer authentication token.
   - The token is either explicitly configured (`RYU_DAEMON_TOKEN`) or ephemerally generated at startup and written to the local runtime directory (`~/.ryu/daemon.token`) with restricted permissions (0600 on POSIX, current-user ACL on Windows).
   - **Bearer Auth != Human Authorization:** Daemon bearer authentication validates only the local loopback transport. It confers zero authority to approve capability gates.

3. **Raw Human Secret Isolation (Contract APP-006):**
   - The daemon **never** persists, caches, logs, serializes, or independently manages raw human approver secrets.
   - Approver secret keys (`token-hmac-v1` key material) remain exclusively on the client/signer side.
   - When an approval decision is submitted via the daemon, the payload contains the pre-computed `token-hmac-v1` cryptographic signature (`ApproverDecisionSubmission`) or decision intent forwarded directly to the authoritative `ApprovalClient`.
   - Windows Credential Manager serves solely as optional client-side storage, never an approval authority.

4. **Desktop IPC Boundary:**
   - The Tauri desktop application shell enforces a strict Content Security Policy (CSP), isolating WebView rendering from native OS execution.
   - The desktop frontend communicates with the daemon over authenticated local HTTP/WS, never directly connecting to PostgreSQL, Redis, or internal bus queues.

5. **Dynamic Attention Budget Visualization:**
   - The daemon surfaces runtime attention saturation state dynamically ($N$ resolved from `AttentionBudget.concurrency_limit`), which UI surfaces render without hardcoding.

## Alternatives Considered
- *Giving the Daemon Direct DB and Bus Authority:* Rejected because it violates SCCA Law 1 (Everything Happens Inside a Space) and bypasses the Space Kernel.
- *Caching Approver Secrets in Daemon for Frictionless 1-Click Approvals:* Strictly rejected because it would turn the daemon into an untrusted credential escrow and bypass human presence verification.
- *Allowing Non-Loopback Network Access:* Rejected to prevent network exposure without multi-user TLS, mutual auth, and enterprise identity infrastructure.

## Consequences
- Clean, auditable separation between human presentation clients and core Space authority.
- Human approval decisions remain cryptographically bound to the authentic approver.
- Phase 8.5 UI clients remain pure observation and intention dispatch adapters.

## Date
2026-09-21

