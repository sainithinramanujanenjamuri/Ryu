"""Base protocol and descriptor for Node platform profiles.

CONTRACT_MATRIX NODE-009, NODE-013, ADR-0037 — Phase 11
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from node.contract import DeviceInfo, NodeInfo


@runtime_checkable
class NodePlatformProfile(Protocol):
    """Protocol defining cross-platform node capabilities and environment discovery."""

    @property
    def platform_name(self) -> str:
        """Operating system name ('windows', 'linux', 'macos', etc.)."""
        ...

    @property
    def architecture(self) -> str:
        """CPU architecture ('x86_64', 'aarch64', etc.)."""
        ...

    @property
    def environment_profile(self) -> str:
        """Environment classification ('windows_host', 'linux_native', 'wsl2', etc.)."""
        ...

    @property
    def evidence_label(self) -> str:
        """Audit and evidence label identifying environment fidelity."""
        ...

    @property
    def is_supported(self) -> bool:
        """Whether this platform target is fully supported in current release (v1.0 baseline)."""
        ...

    def inspect_system(self, node_id: str) -> NodeInfo:
        """Inspect host and return canonical NodeInfo descriptor."""
        ...

    def discover_devices(self, node_id: str) -> list[DeviceInfo]:
        """Enumerate local physical and virtual devices on this host."""
        ...

    def get_scratch_directory(self) -> Path:
        """Return host scratch directory for temporary workspaces."""
        ...

    def get_shell_command(self) -> list[str]:
        """Return base command invocation for interactive/script shell."""
        ...

