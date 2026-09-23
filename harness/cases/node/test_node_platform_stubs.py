"""Executable harness verification for Node Runtime contract NODE-013.

Space-Centric Cognitive Architecture (SCCA) — Phase 11
spec §11 (Platform Support - Post-v1 Stubs & Feature Flags), CONTRACT_MATRIX NODE-013
ADR-0037

INVARIANTS:
feature compilation != platform support
A successful Cargo build under a future-platform feature flag proves only that
the placeholder/feature-gated code compiles without breaking shared interfaces.
It does not establish platform support or contract compliance.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from node.platforms.stubs import (
    AndroidProfile,
    IOSProfile,
    MacOSProfile,
    RaspberryPiProfile,
)


def test_cargo_future_platform_feature_flags_compile() -> None:
    """NODE-013: Future platform feature flags compile cleanly in Rust workspace.

    INVARIANT: cargo check --features macos proves only that the placeholder compiles,
    NOT that RYU supports macOS.
    """
    repo_root = Path(__file__).resolve().parents[3]
    manifest_path = repo_root / "node_runtime" / "Cargo.toml"
    assert manifest_path.exists()

    # 1. Compile with each individual post-v1 feature flag
    features = ["macos", "android", "ios", "rpi_gpio"]
    for feat in features:
        res = subprocess.run(
            ["cargo", "check", "--manifest-path", str(manifest_path), "--features", feat],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"cargo check --features {feat} failed: {res.stderr}"

    # 2. Compile with all features simultaneously
    res_all = subprocess.run(
        ["cargo", "check", "--manifest-path", str(manifest_path), "--all-features"],
        capture_output=True,
        text=True,
    )
    assert res_all.returncode == 0, f"cargo check --all-features failed: {res_all.stderr}"


def test_python_platform_stubs_integrity_and_boundaries() -> None:
    """NODE-013: Post-v1 platform stubs exist as typed boundaries and reject premature execution."""
    stubs = {
        "macos": MacOSProfile(),
        "android": AndroidProfile(),
        "ios": IOSProfile(),
        "rpi_gpio": RaspberryPiProfile(),
    }

    for name, profile in stubs.items():
        # INVARIANT: is_supported must remain False until actual roadmap implementation
        assert profile.is_supported is False, f"Profile {name} cannot claim support in Phase 11"
        assert "post-v1" in profile.evidence_label.lower()

        # Metadata can be inspected structurally
        info = profile.inspect_system(f"node-stub-{name}")
        assert info.node_id == f"node-stub-{name}"
        assert info.labels.get("status") == "post-v1"

        # Runtime execution attempts must fail with typed NotImplementedError
        with pytest.raises(NotImplementedError) as exc_dev:
            profile.discover_devices(f"node-{name}")
        assert "spec §11 — Post-v1 platform" in str(exc_dev.value)

        with pytest.raises(NotImplementedError) as exc_scratch:
            profile.get_scratch_directory()
        assert "spec §11 — Post-v1 platform" in str(exc_scratch.value)

        with pytest.raises(NotImplementedError) as exc_shell:
            profile.get_shell_command()
        assert "spec §11 — Post-v1 platform" in str(exc_shell.value)

