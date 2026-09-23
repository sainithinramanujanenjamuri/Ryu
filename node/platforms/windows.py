"""Windows Host platform profile implementation.

CONTRACT_MATRIX NODE-001, NODE-009, ADR-0020, ADR-0037 — Phase 7 & 11
"""

from __future__ import annotations

import os
from pathlib import Path

from node.contract import DeviceInfo, DeviceState, DeviceType, NodeInfo, NodeState


class WindowsHostProfile:
    """Platform profile for native Windows physical host."""

    @property
    def platform_name(self) -> str:
        return "windows"

    @property
    def architecture(self) -> str:
        return "x86_64"

    @property
    def environment_profile(self) -> str:
        return "windows_host"

    @property
    def evidence_label(self) -> str:
        return "Windows host"

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
                "terminal.powershell",
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
                capability_metadata={"backend": "directx_dxgi", "vram_mb": "8192"},
                availability_state=DeviceState.ONLINE,
                total_capacity=1,
            ),
            DeviceInfo(
                device_id=f"{node_id}-storage-0",
                node_id=node_id,
                device_type=DeviceType.STORAGE,
                capability_metadata={"mount": "scratch_volume"},
                availability_state=DeviceState.ONLINE,
                total_capacity=10 * 1024 * 1024 * 1024,
            ),
        ]

    def get_scratch_directory(self) -> Path:
        temp = os.environ.get("TEMP") or os.environ.get("TMP") or "C:\\Windows\\Temp"
        return Path(temp) / "ryu_scratch"

    def get_shell_command(self) -> list[str]:
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]

