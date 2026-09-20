# ADR-0020: Platform Adapter: Windows Host and Linux WSL2 Profiles

## Context
RYU AI operates across multiple physical and virtual host environments. Phase 7 targets the Windows host as its primary physical-device MVP target, with Linux/WSL2 serving as a development and complementary validation environment. The architecture must separate platform-specific hardware APIs from shared protocol and grant semantics.

## Decision
1. **Platform Adapter Separation:**
   ```text
   Platform Adapter Boundary
   ├── Windows Adapter (Win32 API, DirectX DXGI, Job Objects)
   └── Linux Adapter (/proc, sysfs, POSIX signals)
   ```
2. **Environment Profiles:**
   - `Native Linux`: Direct hardware access (/proc/cpuinfo, /dev, nvidia-smi).
   - `WSL2`: Virtualized Linux kernel under hypervisor (`profile: "wsl2"`), using `/dev/dxg` for GPU passthrough.
3. **Physical Validation Anchor:** Claims of physical device validation for the Phase 7 MVP apply specifically to the real Windows host. WSL2 is explicitly acknowledged as virtualized development infrastructure.
4. **Honesty Principle:** Windows does not emulate Linux Seccomp; Linux does not emulate Win32 Job Objects. Unsupported capabilities are reported honestly as `unsupported: true`.

## Alternatives Considered
- *Treating WSL2 as Native Linux Hardware:* Rejected because virtualized hypervisor abstraction cannot substitute for physical device validation where device-side enforcement matters.
- *Single Monolithic Platform Layer:* Rejected because OS-specific code would leak into core contracts.

## Consequences
- Clean platform adapter boundary isolating OS APIs from protocol and grant contracts.
- Accurate reporting of platform capabilities without simulation or false claims.

## Date
2026-09-20
