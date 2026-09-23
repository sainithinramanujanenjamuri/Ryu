"""Unit tests for Node platform profiles and environment detection.

CONTRACT_MATRIX NODE-009, NODE-013, ADR-0020, ADR-0037 — Phase 11
INVARIANT:
WSL2 != Native Linux Device.
WSL2 provides Linux compatibility validation; it must never be represented as
proof of native Linux hardware/device independence.
Feature compilation / stub existence != Platform support.
"""

from __future__ import annotations

import pytest

from node.contract import NodeState
from node.platforms.base import NodePlatformProfile
from node.platforms.linux import LinuxHostProfile, WSL2Profile, is_wsl2_environment
from node.platforms.stubs import (
    AndroidProfile,
    IOSProfile,
    MacOSProfile,
    RaspberryPiProfile,
)
from node.platforms.windows import WindowsHostProfile


def test_windows_host_profile_contract() -> None:
    profile = WindowsHostProfile()
    assert isinstance(profile, NodePlatformProfile)
    assert profile.platform_name == "windows"
    assert profile.architecture == "x86_64"
    assert profile.environment_profile == "windows_host"
    assert profile.evidence_label == "Windows host"
    assert profile.is_supported is True

    info = profile.inspect_system("node-win-01")
    assert info.node_id == "node-win-01"
    assert info.platform == "windows"
    assert info.environment_profile == "windows_host"
    assert info.runtime_state == NodeState.READY
    assert info.labels.get("evidence_type") == "Windows host"

    devices = profile.discover_devices("node-win-01")
    assert len(devices) >= 3
    types = {d.device_type.value for d in devices}
    assert "cpu" in types
    assert "gpu" in types
    assert "storage" in types

    shell = profile.get_shell_command()
    assert "powershell.exe" in shell[0] or "powershell" in shell[0]


def test_linux_host_profile_distinct_from_wsl2() -> None:
    native = LinuxHostProfile()
    wsl = WSL2Profile()

    # INVARIANT: WSL2 != Native Linux Device
    assert native.environment_profile == "linux_native"
    assert wsl.environment_profile == "wsl2"
    assert native.environment_profile != wsl.environment_profile

    # Evidence labels must be strictly separated
    assert native.evidence_label == "Native Linux host"
    assert wsl.evidence_label == "Linux compatibility validation via WSL2"
    assert "compatibility validation" in wsl.evidence_label
    assert "compatibility validation" not in native.evidence_label

    # Node descriptors must carry distinct evidence types
    native_info = native.inspect_system("node-lin-native")
    wsl_info = wsl.inspect_system("node-lin-wsl")
    assert native_info.environment_profile == "linux_native"
    assert wsl_info.environment_profile == "wsl2"
    assert native_info.labels["evidence_type"] == "Native Linux host"
    assert wsl_info.labels["evidence_type"] == "Linux compatibility validation via WSL2"

    # Both profiles enumerate standard POSIX devices
    native_devs = native.discover_devices("node-lin-native")
    wsl_devs = wsl.discover_devices("node-lin-wsl")
    assert len(native_devs) >= 3
    assert len(wsl_devs) >= 3


def test_post_v1_platform_stubs_compilation_vs_support() -> None:
    """INVARIANT: feature compilation / stub existence != platform support."""
    stubs = [
        MacOSProfile(),
        AndroidProfile(),
        IOSProfile(),
        RaspberryPiProfile(),
    ]

    for stub in stubs:
        assert isinstance(stub, NodePlatformProfile)
        # SCCA requirement: post-v1 platforms MUST NOT claim support in Phase 11
        assert stub.is_supported is False
        assert "placeholder" in stub.evidence_label.lower()

        # Metadata can be inspected structurally
        info = stub.inspect_system(f"node-{stub.platform_name}-test")
        assert info.runtime_state == NodeState.REGISTERED
        assert info.labels.get("status") == "post-v1"

        # Execution or device discovery attempts must raise NotImplementedError
        with pytest.raises(NotImplementedError) as exc_dev:
            stub.discover_devices(f"node-{stub.platform_name}")
        assert "spec §11 — Post-v1 platform" in str(exc_dev.value)

        with pytest.raises(NotImplementedError) as exc_scratch:
            stub.get_scratch_directory()
        assert "spec §11 — Post-v1 platform" in str(exc_scratch.value)

        with pytest.raises(NotImplementedError) as exc_shell:
            stub.get_shell_command()
        assert "spec §11 — Post-v1 platform" in str(exc_shell.value)

