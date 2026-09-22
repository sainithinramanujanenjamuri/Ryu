# RYU AI — Project Memory

## Entry 0010 — Phase 8.5 Human Interaction Layer (CLI + Windows Application)

**Date:** 2026-09-21  
**Phase:** 8.5 — Human Interaction Layer  
**Status:** COMPLETE (PHASE 8.5 GATE: PASS)  
**Previous Baseline:** `ac1d639` (Phase 8)

---

### Summary

Phase 8.5 matures the human interaction layer of RYU AI across two complementary surfaces:
1. **8.5A — Professional Interactive Developer CLI Shell (`ryu` / `ryu shell`)**:
   - Interactive REPL (`ryu>`) built with `prompt_toolkit`.
   - Contextual autocompletion of subcommands and dynamic pending approval IDs (CLI-010).
   - Dynamic Attention Budget status banner rendering `0 / N` active gates without hardcoding $N$ (APP-003, CLI-009).
   - Interactive un-echoed password entry for approver secrets preventing terminal scrollback leakage (CLI-011).
   - Live continuous pulse streaming tail via `ryu audit stream --follow` (CLI-012).
   - Full contract compatibility with existing Phase 8 static scripts (`ryu status`, `ryu --json status`, exit codes).
2. **8.5B — RYU Windows Desktop Application Command Center (`apps/ryu-desktop/`)**:
   - High-performance, dark command center built with **Tauri v2** (Rust core + native Windows WebView2 + React/TypeScript).
   - Strict Content Security Policy (CSP) and loopback IPC security boundaries.
   - Dynamic visual attention gauge rendering $N$ slots from runtime state.
   - High-fidelity human approval cards rendering full cryptographic metadata (hash, plan version, requester, parameters, risk tier).
   - Client-side WebCrypto HMAC-SHA256 decision signing.
   - Real-time Server-Sent Events (SSE) pulse timeline and searchable immutable audit views.
3. **Local Channel Daemon Adapter (`channels/daemon/`)**:
   - Loopback HTTP/SSE server binding strictly to `127.0.0.1` / `::1`.
   - Local Bearer authentication for transport protection.
   - **Zero Independent Authority:** Operates strictly as a channel adapter without cognitive, execution, or approval authority.
   - **Contract APP-006 (Raw Human Token Isolation):** Never persists, caches, logs, or serializes raw human approver secrets.

---

### Architectural Invariants & Key Implementations

1. **Channels, Never Authorities (ADR-0026):**
   - The CLI, Desktop App, and Local Daemon have ZERO independent authority.
   - All approvals route through the existing Phase 8 authoritative `token-hmac-v1` Human Gate and Space Kernel.
   - SCCA Laws 1 through 6 remain fully enforced.
2. **Raw Human Token Isolation (APP-006):**
   - Human approver secret key material remains strictly on the client side.
   - The Local Daemon only receives pre-computed cryptographic signatures (`ApproverDecisionSubmission`) or decision intents forwarded directly to the authoritative `ApprovalClient`.
   - Formally verified by security regression test `test_daemon_never_persists_raw_human_token`.
3. **Dynamic Attention Budget Visualization (APP-003):**
   - The UI and CLI dynamically read the concurrency saturation cap $N$ from runtime `AttentionBudget`.
   - Eliminates all hardcoded limits from user presentation.
4. **Performance Evaluation & Baselines:**
   - Evaluated via `scripts/benchmarks_phase8_5.py`:
     - `token-hmac-v1` signing: >110,000 ops/sec (mean 8.6 µs/op).
     - Loopback HTTP round-trip latency: p50 1.51 ms, p95 16.61 ms (well within provisional $\le 45$ms target).
     - End-to-end decision submission: p50 1.63 ms, p95 17.04 ms (well within provisional $\le 350$ms target).

---

### Phase 8.5 Gate Verification

- [x] Interactive CLI Operational (`channels/cli/shell.py`, `channels/tests/test_cli_shell.py`)
- [x] Phase 8 Contract Compatibility Preserved (478 baseline tests passing, zero regression)
- [x] Tauri Desktop App Shell Operational (`apps/ryu-desktop` builds cleanly with Vite + TypeScript)
- [x] Single Authoritative Signing (`token-hmac-v1` verified by `ApproverAuthenticator`)
- [x] Local Daemon Authority Confined (`channels/daemon/server.py`, `channels/tests/test_daemon.py`)
- [x] Raw Human Secret Isolation (`test_daemon_never_persists_raw_human_token` PASS)
- [x] Attention Budget Dynamic ($N$ read from runtime)
- [x] Single Pulse Stream Path (SSE / Bus subscription)
- [x] Performance Benchmarked (`scripts/benchmarks_phase8_5.py` PASS)
- [x] Dependency guard PASS (zero forbidden imports in `core/`)

