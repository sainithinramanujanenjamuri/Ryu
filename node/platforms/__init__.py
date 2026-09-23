"""Node Platform Profiles package.

CONTRACT_MATRIX NODE-009, NODE-013, ADR-0037 — Phase 11
"""

from __future__ import annotations

import os
from node.platforms.base import NodePlatformProfile
from node.platforms.linux import LinuxHostProfile, WSL2Profile, detect_linux_profile
from node.platforms.stubs import (
    AndroidProfile,
    IOSProfile,
    MacOSProfile,
    RaspberryPiProfile,
)
from node.platforms.windows import WindowsHostProfile


def get_current_platform_profile() -> NodePlatformProfile:
    """Detect and return appropriate profile for current host environment."""
    if os.name == "nt":
        return WindowsHostProfile()
    if os.name == "posix":
        return detect_linux_profile()
    return LinuxHostProfile()


__all__ = [
    "AndroidProfile",
    "IOSProfile",
    "LinuxHostProfile",
    "MacOSProfile",
    "NodePlatformProfile",
    "RaspberryPiProfile",
    "WSL2Profile",
    "detect_linux_profile",
    "get_current_platform_profile",
]

