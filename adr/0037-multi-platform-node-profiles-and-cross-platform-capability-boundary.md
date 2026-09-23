# ADR-0037: Multi-Platform Node Profiles and Cross-Platform Capability Boundary

## Context
Phase 7 introduced the Node Runtime execution boundary with a focus on Windows host physical execution. Phase 11 generalizes this model to multiple platforms without weakening shared architectural boundaries. In particular, Linux environments (both native Linux and Windows-hosted WSL2) must be supported with uniform capability enumeration for CPU, GPU, storage, and terminal execution.

A critical architectural distinction exists between native Linux execution on bare-metal or dedicated Linux hardware and Windows-hosted virtualized Linux execution under WSL2 (`Ubuntu 22.04.5 LTS`, `x86_64`). WSL2 is a valuable Linux compatibility environment for development and automated validation, but it cannot be claimed as proof of native Linux hardware independence.

Furthermore, post-v1 platforms (macOS, Android, iOS, and Raspberry Pi) require structural and syntactic interface integration so that future extensions can be implemented without breaking the core memory, resource, or node protocols.

## Problem
1. How can the Node Runtime discover and interact with heterogeneous operating systems while maintaining strict contract parity across all platforms?
2. How do we ensure that WSL2 is never mislabeled as native bare-metal Linux hardware in test evidence?
3. How do we integrate post-v1 target placeholders without prematurely claiming platform support?

## Decision
1. **Platform Profile Abstraction:** Introduce `NodePlatformProfile` in Python and platform adapters in Rust (`ryu-node`). The profile abstracts platform-specific introspection (CPU core count, memory sizes, scratch paths, shell commands `/bin/bash` vs `pwsh.exe`) behind a uniform protocol.
2. **Explicit Separation of Native Linux and WSL2:**
   - `LinuxHostProfile` (`"linux_native"`): Represents actual Linux execution on an x86_64 host.
   - `WSL2Profile` (`"wsl2"`): Represents virtualized Linux under WSL2. Test evidence from WSL2 is strictly documented as `"Linux compatibility validation via WSL2"` and must never be labeled as proof of native Linux hardware independence.
3. **Compile-Time Placeholders for Post-v1 Targets:**
   - In Rust (`node_runtime/crates/ryu-node/Cargo.toml`), post-v1 targets reside behind feature flags (`macos`, `android`, `ios`, `rpi_gpio`). Successful compilation proves syntax and trait conformance only; it does not establish platform support.
   - In Python (`node/platforms/stubs.py`), typed profiles (`MacOSProfile`, `AndroidProfile`, etc.) exist as stubs raising `NotImplementedError("spec §11 — Post-v1 platform")`.

## Alternatives Considered
- *Single unified Unix profile:* Rejected because WSL2 has specific interoperability and path translation quirks (`/proc/sys/fs/binfmt_misc/WSLInterop`) that differ from bare-metal Linux.
- *Treating WSL2 as bare-metal hardware:* Rejected per `AGENTS.md` §22 and SCCA honesty principles.

## Consequences
- Clean separation between platform discovery and device execution.
- Verifiable Linux compatibility using WSL2 without false claims of hardware independence.
- Zero breakage when compiling future platform placeholders.

## Date
2026-09-23

