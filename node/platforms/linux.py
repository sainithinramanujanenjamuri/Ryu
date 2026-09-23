"""Linux Host and WSL2 platform profiles.

CONTRACT_MATRIX NODE-009, ADR-0020, ADR-0037 — Phase 11
INVARIANT:
WSL2 != Native Linux Device.
WSL2 provides Linux compatibility validation; it must never be represented as
proof of native Linux hardware/device independence.
"""

from __future__ import annotations

import os
from pathlib import Path

from node.contract import DeviceInfo, DeviceState, DeviceType, NodeInfo, NodeState


class LinuxHostProfile:
    """Platform profile for native bare-metal / dedicated Linux physical or VM host."""

    @property
    def platform_name(self) -> str:
        return "linux"

    @property
    def architecture(self) -> str:
        return "x86_64"

    @property
    def environment_profile(self) -> str:
        return "linux_native"

    @property
    def evidence_label(self) -> str:
        return "Native Linux host"

    @property
    def is_supported(self) -> bool:
        return True

    def inspect_system(self, node_id: str) -> NodeInfo:
        cpu_count = os.cpu_count() or 4
        return NodeInfo(
            node_id=node_id,
            platform=self.platform_name,
            architecture=self.architecture,
            environment_profile=self.environment_profile,
            runtime_state=NodeState.READY,
            cpu_cores=cpu_count,
            memory_total_bytes=16 * 1024 * 1024 * 1024,
            storage_total_bytes=500 * 1024 * 1024 * 1024,
            capabilities=[
                "compute.cpu",
                "compute.gpu",
                "storage.workspace",
                "terminal.bash",
            ],
            labels={
                "tier": "physical",
                "platform": self.platform_name,
                "environment": self.environment_profile,
                "evidence_type": self.evidence_label,
            },
        )

    def discover_devices(self, node_id: str) -> list[DeviceInfo]:
        return [
            DeviceInfo(
                device_id=f"{node_id}-cpu-0",
                node_id=node_id,
                device_type=DeviceType.CPU,
                capability_metadata={"cores": str(os.cpu_count() or 4), "arch": "x86_64"},
                availability_state=DeviceState.ONLINE,
                total_capacity=100,
            ),
            DeviceInfo(
                device_id=f"{node_id}-gpu-0",
                node_id=node_id,
                device_type=DeviceType.GPU,
                capability_metadata={"backend": "cuda", "vram_mb": "8192"},
                availability_state=DeviceState.ONLINE,
                total_capacity=1,
            ),
            DeviceInfo(
                device_id=f"{node_id}-storage-0",
                node_id=node_id,
                device_type=DeviceType.STORAGE,
                capability_metadata={"mount": "/var/tmp/ryu_scratch"},
                availability_state=DeviceState.ONLINE,
                total_capacity=10 * 1024 * 1024 * 1024,
            ),
        ]

    def get_scratch_directory(self) -> Path:
        return Path("/var/tmp/ryu_scratch")

    def get_shell_command(self) -> list[str]:
        return ["/bin/bash", "-c"]


class WSL2Profile:
    """Platform profile for Windows-hosted WSL2 Linux compatibility environment."""

    @property
    def platform_name(self) -> str:
        return "linux"

    @property
    def architecture(self) -> str:
        return "x86_64"

    @property
    def environment_profile(self) -> str:
        return "wsl2"

    @property
    def evidence_label(self) -> str:
        return "Linux compatibility validation via WSL2"

    @property
    def is_supported(self) -> bool:
        return True

    def inspect_system(self, node_id: str) -> NodeInfo:
        cpu_count = os.cpu_count() or 4
        return NodeInfo(
            node_id=node_id,
            platform=self.platform_name,
            architecture=self.architecture,
            environment_profile=self.environment_profile,
            runtime_state=NodeState.READY,
            cpu_cores=cpu_count,
            memory_total_bytes=8 * 1024 * 1024 * 1024,
            storage_total_bytes=250 * 1024 * 1024 * 1024,
            capabilities=[
                "compute.cpu",
                "compute.gpu",
                "storage.workspace",
                "terminal.bash",
            ],
            labels={
                "tier": "virtualized",
                "platform": self.platform_name,
                "environment": self.environment_profile,
                "evidence_type": self.evidence_label,
            },
        )

    def discover_devices(self, node_id: str) -> list[DeviceInfo]:
        return [
            DeviceInfo(
                device_id=f"{node_id}-cpu-0",
                node_id=node_id,
                device_type=DeviceType.CPU,
                capability_metadata={"cores": str(os.cpu_count() or 4), "arch": "x86_64"},
                availability_state=DeviceState.ONLINE,
                total_capacity=100,
            ),
            DeviceInfo(
                device_id=f"{node_id}-gpu-0",
                node_id=node_id,
                device_type=DeviceType.GPU,
                capability_metadata={"backend": "cuda", "vram_mb": "8192"},
                availability_state=DeviceState.ONLINE,
                total_capacity=1,
            ),
            DeviceInfo(
                device_id=f"{node_id}-storage-0",
                node_id=node_id,
                device_type=DeviceType.STORAGE,
                capability_metadata={"mount": "/tmp/ryu_scratch"},
                availability_state=DeviceState.ONLINE,
                total_capacity=10 * 1024 * 1024 * 1024,
            ),
        ]

    def get_scratch_directory(self) -> Path:
        return Path("/tmp/ryu_scratch")

    def get_shell_command(self) -> list[str]:
        return ["/bin/bash", "-c"]


def is_wsl2_environment() -> bool:
    """Check whether current execution environment is WSL2."""
    if os.name != "posix":
        return False
    if Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists():
        return True
    try:
        content = Path("/proc/version").read_text(encoding="utf-8").lower()
        return "microsoft" in content or "wsl" in content
    except Exception:
        return False


def detect_linux_profile() -> LinuxHostProfile | WSL2Profile:
    """Return appropriate Linux profile distinguishing native Linux from WSL2."""
    if is_wsl2_environment():
        return WSL2Profile()
    return LinuxHostProfile()

