# RYU AI — Project Memory

## Entry 0016 — v1.0.1 Desktop Command Center Capability Exposure

**Date:** 2026-09-27  
**Milestone:** v1.0.1 Desktop Command Center Release  
**Status:** VERIFIED (GATE: PASS)  
**Baseline:** v1.0.0 Production Release (`8ce151a`)  

---

### Executive Summary

RYU v1.0.0 verified the core runtime, authority boundaries, governance, security, execution, memory, resources, nodes, approvals, skills, and LLM integration. However, the Desktop Command Center (Tauri v2 + React) did not yet expose many of these verified capabilities to the operator.

Release v1.0.1 fulfills the mandate:
> *"Expose the capabilities that already exist in RYU AI through the Desktop Command Center without inventing a second runtime or bypassing the Space-Centric Cognitive Architecture (SCCA)."*

The Desktop Command Center remains strictly an unprivileged **Channel / Client** communicating over loopback HTTP/SSE (`http://127.0.0.1:8420`) with client-side WebCrypto HMAC signing.

---

### Delivered Work Packages

```text
┌────────────────────────────────────────────────────────────────────────┐
│                   v1.0.1 CAPABILITY EXPOSURE                           │
├──────────────────────────────────┬─────────────────────────────────────┤
│ WP-1 Conversation History        │ Rehydration from SpaceHistoryStore   │
│ WP-2 Space Lifecycle             │ Dynamic isolation & creation modals │
│ WP-3 Sandboxed HTML Preview      │ Zero-privilege iframe sandbox       │
│ WP-4 Space Artifact Explorer     │ SHA-256 integrity, inspect & save   │
│ WP-5 Sandboxed File Ingress      │ Path protection, 2MB cap, taint bus │
│ WP-6 Read-Only System Visibility │ Nodes, device grants, space memory  │
└──────────────────────────────────┴─────────────────────────────────────┘
```

#### WP-1: Conversation History Rehydration
- **Backend:** `channels/daemon/history.py` provides `DialogueTurn` and `SpaceHistoryStore` (.jsonl persistence with pulse-store fallback).
- **Endpoint:** `GET /api/v1/spaces/{space_id}/history` returns chronologically ordered turns.
- **Frontend:** `App.tsx` rehydrates messages upon space switch and mount. No ephemeral React state is treated as authoritative.

#### WP-2: Space Lifecycle & Dynamic Switching
- **Backend:** `POST /api/v1/spaces` invokes authoritative `core.space.create_space`, publishes `space.created` pulse, and updates catalog.
- **Endpoint:** `GET /api/v1/spaces/{space_id}` returns space metadata.
- **Frontend:** Space dropdown selector, `+ New Space` modal (`CreateSpaceModal.tsx`), and sidebar shortcuts.

#### WP-3: Zero-Privilege Sandboxed HTML Preview
- **Architecture:** Enforces SCCA isolation for agent-generated HTML artifacts.
- **Component:** `MarkdownMessage.tsx` provides a `[Code | Preview]` toggle for HTML fenced blocks.
- **Sandbox Boundary:** Rendered strictly via `<iframe sandbox="allow-scripts" srcDoc={code} />` with `allow-same-origin` permanently forbidden.

#### WP-4: Space Artifact Explorer
- **Backend:** `channels/daemon/artifacts.py` provides `SpaceArtifactStore` with SHA-256 integrity and path traversal protection.
- **Auto-Registration:** Daemon automatically extracts HTML code blocks from assistant responses and registers them in the space artifact store.
- **Endpoints:** `GET /api/v1/spaces/{space_id}/artifacts` and `GET /api/v1/spaces/{space_id}/artifacts/{artifact_id}/content`.
- **Frontend:** `ArtifactExplorer.tsx` in the right drawer allows filtering, content previewing, SHA copying, and downloading.

#### WP-5: Sandboxed File Ingress with Taint Tagging
- **Backend:** `POST /api/v1/spaces/{space_id}/files` with $\le 2$MB limit, text format whitelist, path traversal guards (`..`, `/`, `\`), and automatic `security.taint.detected` pulse publication (`taint: true`).
- **Frontend:** Chat input bar file upload button (`📎`), client-side 2MB validation, and user-facing security notification.

#### WP-6: Read-Only System Visibility
- **Endpoints:** `GET /api/v1/nodes` (hardware leases and node registry) and `GET /api/v1/spaces/{space_id}/memory` (space experiences and global promoted knowledge).
- **Frontend:** `SystemVisibilityView.tsx` with dedicated sub-tabs for Nodes & Hardware and Space Memory & Reflections (displaying SCCA Law 4 notice).

---

### Verification & Compliance Evidence

1. **Unit & Daemon Tests (`channels/tests`):**
   - `test_daemon_v101.py`: 15 passed in 2.1s (history, artifacts, file ingress, taint detection, space creation, nodes, memory).
   - Entire `channels/tests` suite: **57 passed, 1 skipped** (Postgres integration guard), 0 failed.
2. **Architecture Core Independence (`scripts/dep_guard.py`):**
   - Scanned `core/`: **PASS** — 0 forbidden imports.
3. **Contract Synchronization (`scripts/contract_sync.py`):**
   - Registry and architecture pulse types: **PASS** — 38 types in sync.
4. **Desktop TypeScript Compilation (`apps/ryu-desktop`):**
   - `tsc && vite build`: **PASS** — 0 errors (dist bundle 242.30 kB).
5. **Rust Desktop Binary (`src-tauri` & `node_runtime`):**
   - `cargo check --manifest-path apps/ryu-desktop/src-tauri/Cargo.toml`: **PASS** (16.94s).
   - `cargo check --manifest-path node_runtime/Cargo.toml`: **PASS** (1.94s).

---

### Governed Baseline & References

- **ADR:** `adr/0040-desktop-capability-exposure-artifact-lifecycle-and-sandbox-preview.md`
- **Specification:** `docs/V1.0.1_DESKTOP_COMMAND_CENTER_SPEC.md`
- **Contracts:** `docs/CONTRACT_MATRIX.md` (DESKTOP-001 through DESKTOP-005)
