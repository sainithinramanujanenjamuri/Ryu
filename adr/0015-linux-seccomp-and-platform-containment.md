# ADR-0015: Linux Seccomp Syscall Filtering and Platform Security Containment

## Status
Accepted (Phase 6)

## Context
Untrusted processes must be prevented from invoking unauthorized kernel system calls (e.g. `reboot`, `ptrace`, `kexec_load`, raw socket operations). However, operating system containment primitives differ across platforms:
- Linux provides native kernel Seccomp (`prctl(PR_SET_SECCOMP)` / Berkeley Packet Filter BPF).
- Windows does not provide Seccomp or native syscall-level BPF filtering for userland processes.

## Decision
1. **Linux Seccomp Containment (`PR_SET_SECCOMP` / BPF):**
   - On Linux systems, sandboxed child processes execute with real kernel Seccomp policy installed.
   - Forbidden system calls (including `sys_ptrace`, `sys_reboot`, `sys_kexec_load`, `sys_init_module`) are intercepted directly by the Linux kernel.
   - Any violation produces a structured `SeccompViolation` which triggers immediate sandbox termination, worker failure, and observability escalation via typed Pulses.
   - Simulated Seccomp flags or mocks are strictly rejected for Linux gate verification; the Linux implementation must test real kernel filtering.
2. **Windows Platform Containment (Security Adapter):**
   - Windows lacks native Seccomp syscall filtering. The system must NOT claim Seccomp containment on Windows.
   - On Windows, process isolation is enforced via:
     - Process-tree Job Object limits (`CREATE_SUSPENDED`, Job memory limits, process count quotas).
     - Full process tree tracking and tree-kill on exit/timeout.
     - Strict path canonicalization and access denial outside the designated Space workspace.
     - Network socket restrictions and blocked loopback/outbound policy.
     - Sanitized environment stripping (removing all host API keys and system credentials).
     - Security audit logging recording all capability invocations and blocked operations.
3. **Platform-Specific Evidence Separation:**
   - Test suites and reports must cleanly separate Linux-verified properties from Windows-verified properties.
   - Terminology must remain exact: `tested`, `verified`, `blocked`, `contained`, `platform-specific`, `not supported`, `known limitation`.

## Consequences
- Real kernel syscall containment is achieved on Linux platforms.
- Windows systems provide robust process-tree, filesystem, network, and environment isolation while transparently documenting Seccomp as platform-unsupported.
- Architectural honesty is preserved without false equivalence.
