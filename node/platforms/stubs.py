"""Post-v1 Platform Profile Stubs and Architectural Placeholders.

CONTRACT_MATRIX NODE-013, ADR-0037 — Phase 11
INVARIANT:
Feature compilation / stub existence != Platform support.
These profiles are compile-time and structural placeholders only.
Attempting execution raises NotImplementedError("spec §11 — Post-v1 platform").
"""

from __future__ import annotations

from pathlib import Path

from node.contract import DeviceInfo, NodeInfo, NodeState


class MacOSProfile:
    """Post-v1 platform stub for Apple macOS (darwin)."""

    @property
    def platform_name(self) -> str:
        return "macos"

    @property
    def architecture(self) -> str:
        return "aarch64"

    @property
    def environment_profile(self) -> str:
        return "macos_host"

    @property
    def evidence_label(self) -> str:
        return "macOS compile-time placeholder (Post-v1)"

    @property
    def is_supported(self) -> bool:
        return False

    def inspect_system(self, node_id: str) -> NodeInfo:
        return NodeInfo(
            node_id=node_id,
            platform=self.platform_name,
            architecture=self.architecture,
            environment_profile=self.environment_profile,
            runtime_state=NodeState.REGISTERED,
            cpu_cores=8,
            memory_total_bytes=16 * 1024 * 1024 * 1024,
            storage_total_bytes=500 * 1024 * 1024 * 1024,
            capabilities=["compute.cpu", "compute.metal"],
            labels={"tier": "placeholder", "status": "post-v1"},
        )

    def discover_devices(self, node_id: str) -> list[DeviceInfo]:
        raise NotImplementedError("spec §11 — Post-v1 platform: macOS device discovery")

    def get_scratch_directory(self) -> Path:
        raise NotImplementedError("spec §11 — Post-v1 platform: macOS scratch directory")

    def get_shell_command(self) -> list[str]:
        raise NotImplementedError("spec §11 — Post-v1 platform: macOS shell execution")


class AndroidProfile:
    """Post-v1 platform stub for Android mobile/edge runtime."""

    @property
    def platform_name(self) -> str:
        return "android"

    @property
    def architecture(self) -> str:
        return "aarch64"

    @property
    def environment_profile(self) -> str:
        return "android_edge"

    @property
    def evidence_label(self) -> str:
        return "Android compile-time placeholder (Post-v1)"

    @property
    def is_supported(self) -> bool:
        return False

    def inspect_system(self, node_id: str) -> NodeInfo:
        return NodeInfo(
            node_id=node_id,
            platform=self.platform_name,
            architecture=self.architecture,
            environment_profile=self.environment_profile,
            runtime_state=NodeState.REGISTERED,
            cpu_cores=4,
            memory_total_bytes=4 * 1024 * 1024 * 1024,
            storage_total_bytes=64 * 1024 * 1024 * 1024,
            capabilities=["compute.cpu", "compute.nnapi"],
            labels={"tier": "edge", "status": "post-v1"},
        )

    def discover_devices(self, node_id: str) -> list[DeviceInfo]:
        raise NotImplementedError("spec §11 — Post-v1 platform: Android device discovery")

    def get_scratch_directory(self) -> Path:
        raise NotImplementedError("spec §11 — Post-v1 platform: Android scratch directory")

    def get_shell_command(self) -> list[str]:
        raise NotImplementedError("spec §11 — Post-v1 platform: Android shell execution")


class IOSProfile:
    """Post-v1 platform stub for Apple iOS mobile/tablet runtime."""

    @property
    def platform_name(self) -> str:
        return "ios"

    @property
    def architecture(self) -> str:
        return "aarch64"

    @property
    def environment_profile(self) -> str:
        return "ios_sandboxed"

    @property
    def evidence_label(self) -> str:
        return "iOS compile-time placeholder (Post-v1)"

    @property
    def is_supported(self) -> bool:
        return False

    def inspect_system(self, node_id: str) -> NodeInfo:
        return NodeInfo(
            node_id=node_id,
            platform=self.platform_name,
            architecture=self.architecture,
            environment_profile=self.environment_profile,
            runtime_state=NodeState.REGISTERED,
            cpu_cores=6,
            memory_total_bytes=6 * 1024 * 1024 * 1024,
            storage_total_bytes=128 * 1024 * 1024 * 1024,
            capabilities=["compute.cpu", "compute.coreml"],
            labels={"tier": "sandboxed_device", "status": "post-v1"},
        )

    def discover_devices(self, node_id: str) -> list[DeviceInfo]:
        raise NotImplementedError("spec §11 — Post-v1 platform: iOS device discovery")

    def get_scratch_directory(self) -> Path:
        raise NotImplementedError("spec §11 — Post-v1 platform: iOS scratch directory")

    def get_shell_command(self) -> list[str]:
        raise NotImplementedError("spec §11 — Post-v1 platform: iOS shell execution")


class RaspberryPiProfile:
    """Post-v1 platform stub for Raspberry Pi embedded/IoT runtime with GPIO."""

    @property
    def platform_name(self) -> str:
        return "linux"

    @property
    def architecture(self) -> str:
        return "aarch64"

    @property
    def environment_profile(self) -> str:
        return "rpi_embedded"

    @property
    def evidence_label(self) -> str:
        return "Raspberry Pi GPIO compile-time placeholder (Post-v1)"

    @property
    def is_supported(self) -> bool:
        return False

    def inspect_system(self, node_id: str) -> NodeInfo:
        return NodeInfo(
            node_id=node_id,
            platform=self.platform_name,
            architecture=self.architecture,
            environment_profile=self.environment_profile,
            runtime_state=NodeState.REGISTERED,
            cpu_cores=4,
            memory_total_bytes=4 * 1024 * 1024 * 1024,
            storage_total_bytes=32 * 1024 * 1024 * 1024,
            capabilities=["compute.cpu", "hardware.gpio"],
            labels={"tier": "iot_embedded", "status": "post-v1"},
        )

    def discover_devices(self, node_id: str) -> list[DeviceInfo]:
        raise NotImplementedError("spec §11 — Post-v1 platform: Raspberry Pi GPIO discovery")

    def get_scratch_directory(self) -> Path:
        raise NotImplementedError("spec §11 — Post-v1 platform: Raspberry Pi scratch directory")

    def get_shell_command(self) -> list[str]:
        raise NotImplementedError("spec §11 — Post-v1 platform: Raspberry Pi shell execution")

