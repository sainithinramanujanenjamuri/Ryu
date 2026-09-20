"""Rust Node Runtime Bridge.

Thin typed subprocess adapter connecting Python to the compiled native ryu-node binary.
spec §11 (Node Runtime), CONTRACT_MATRIX NODE-001, NODE-002, NODE-008
ADR-0017, ADR-0019, ADR-0020

INVARIANT:
RustNodeBridge exposes strictly 6 typed subcommands.
Structural exclusion of arbitrary execution: No command execution, shell spawning,
or arbitrary binary execution APIs exist or can be called.
Crash recovery is strictly transport recovery; no automatic replay of non-idempotent work.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from node.contract import (
    DeviceGrant,
    DeviceInfo,
    NodeHealthReport,
    NodeInfo,
    RustBridgeError,
)

# Canonical 6 allowed typed subcommands
ALLOWED_SUBCOMMANDS = frozenset(
    [
        "inspect",
        "inspect-devices",
        "validate-grant",
        "bind",
        "release",
        "health",
        "audit-verify",
        "version",
    ]
)


class RustNodeBridge:
    """Invokes compiled native ryu-node binary strictly via the 6 typed subcommands.

    Excludes arbitrary native execution by design.
    """

    def __init__(self, binary_path: str | Path | None = None) -> None:
        self.binary_path = self._locate_binary(binary_path)

    @classmethod
    def _locate_binary(cls, candidate: str | Path | None) -> Path:
        """Find the compiled ryu-node executable."""
        if candidate:
            p = Path(candidate)
            if p.exists():
                return p

        # Check repository target directories
        repo_root = Path(__file__).resolve().parent.parent
        ext = ".exe" if sys.platform == "win32" else ""
        possible_paths = [
            repo_root / "node_runtime" / "target" / "debug" / f"ryu-node{ext}",
            repo_root / "node_runtime" / "target" / "release" / f"ryu-node{ext}",
            repo_root / "target" / "debug" / f"ryu-node{ext}",
            repo_root / "target" / "release" / f"ryu-node{ext}",
        ]
        for path in possible_paths:
            if path.exists():
                return path

        # Fallback default location (may not exist yet if not built)
        return possible_paths[0]

    def _invoke(self, subcommand: str, args: list[str]) -> str:
        """Execute a typed subcommand and return stdout."""
        if subcommand not in ALLOWED_SUBCOMMANDS:
            raise RustBridgeError(
                f"Unauthorized bridge subcommand '{subcommand}'. "
                f"Structural security boundary allows only: {sorted(ALLOWED_SUBCOMMANDS)}"
            )

        if not self.binary_path.exists():
            raise RustBridgeError(
                f"Compiled native ryu-node binary not found at '{self.binary_path}'. "
                "Run 'cargo build --manifest-path node_runtime/Cargo.toml' first."
            )

        cmd = [str(self.binary_path), subcommand] + args
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15.0,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RustBridgeError(
                f"ryu-node bridge timed out executing subcommand '{subcommand}'"
            ) from exc
        except Exception as exc:
            raise RustBridgeError(
                f"ryu-node subprocess invocation failed: {exc}"
            ) from exc

        if res.returncode != 0:
            raise RustBridgeError(
                f"ryu-node subcommand '{subcommand}' failed with exit code "
                f"{res.returncode}: {res.stderr.strip()}"
            )

        return res.stdout.strip()

    def version(self) -> str:
        """Retrieve ryu-node binary version."""
        return self._invoke("version", [])

    def inspect(self, node_id: str) -> NodeInfo:
        """Inspect platform and hardware environment."""
        stdout = self._invoke("inspect", ["--node-id", node_id])
        try:
            data = json.loads(stdout)
            return NodeInfo.from_dict(data)
        except Exception as exc:
            raise RustBridgeError(f"Failed to parse inspect output: {exc}") from exc

    def inspect_devices(self, node_id: str) -> list[DeviceInfo]:
        """Discover platform hardware devices (CPU, GPU, storage)."""
        stdout = self._invoke("inspect-devices", ["--node-id", node_id])
        try:
            data = json.loads(stdout)
            return [DeviceInfo.from_dict(d) for d in data]
        except Exception as exc:
            raise RustBridgeError(f"Failed to parse inspect-devices output: {exc}") from exc

    def validate_grant(
        self,
        node_id: str,
        secret: str,
        grant: DeviceGrant,
        current_time: str | None = None,
    ) -> tuple[bool, str | None]:
        """Cryptographically verify grant signature, expiry, and revocation via Rust."""
        args = [
            "--node-id",
            node_id,
            "--secret",
            secret,
            "--grant",
            json.dumps(grant.to_dict()),
        ]
        time_arg = current_time or datetime.now(timezone.utc).isoformat()
        args.extend(["--time", time_arg])

        stdout = self._invoke("validate-grant", args)
        try:
            data = json.loads(stdout)
            return bool(data.get("valid", False)), data.get("error")
        except Exception as exc:
            raise RustBridgeError(f"Failed to parse validate-grant output: {exc}") from exc

    def bind(
        self,
        node_id: str,
        secret: str,
        audit_log_path: str | Path,
        req: dict[str, Any],
        current_time: str | None = None,
    ) -> dict[str, Any]:
        """Validate grant, bind device, and append to audit log via native runtime."""
        args = [
            "--node-id",
            node_id,
            "--secret",
            secret,
            "--audit-log",
            str(audit_log_path),
            "--req",
            json.dumps(req),
        ]
        time_arg = current_time or datetime.now(timezone.utc).isoformat()
        args.extend(["--time", time_arg])

        stdout = self._invoke("bind", args)
        try:
            return cast(dict[str, Any], json.loads(stdout))
        except Exception as exc:
            raise RustBridgeError(f"Failed to parse bind output: {exc}") from exc

    def release(
        self,
        node_id: str,
        audit_log_path: str | Path,
        grant_id: str,
        binding_id: str,
        current_time: str | None = None,
    ) -> bool:
        """Release device binding and append to audit log via native runtime."""
        args = [
            "--node-id",
            node_id,
            "--audit-log",
            str(audit_log_path),
            "--grant-id",
            grant_id,
            "--binding-id",
            binding_id,
        ]
        time_arg = current_time or datetime.now(timezone.utc).isoformat()
        args.extend(["--time", time_arg])

        stdout = self._invoke("release", args)
        try:
            data = json.loads(stdout)
            return bool(data.get("released", False))
        except Exception as exc:
            raise RustBridgeError(f"Failed to parse release output: {exc}") from exc

    def health(self) -> NodeHealthReport:
        """Fetch node metrics and active bindings count."""
        stdout = self._invoke("health", [])
        try:
            data = json.loads(stdout)
            return NodeHealthReport(
                node_id="local-node",
                status=data.get("status", "healthy"),
                cpu_percent=data.get("cpu_percent", 0.0),
                memory_used_bytes=data.get("memory_used_bytes", 0),
                memory_total_bytes=data.get("memory_total_bytes", 0),
                uptime_seconds=data.get("uptime_seconds", 0),
                active_bindings_count=data.get("active_bindings_count", 0),
            )
        except Exception as exc:
            raise RustBridgeError(f"Failed to parse health output: {exc}") from exc

    def audit_verify(self, log_path: str | Path) -> tuple[bool, int, str | None]:
        """Verify audit log SHA-256 hash chaining using native Rust implementation."""
        stdout = self._invoke("audit-verify", ["--log-path", str(log_path)])
        try:
            data = json.loads(stdout)
            return (
                bool(data.get("verified", False)),
                int(data.get("count", 0)),
                data.get("error"),
            )
        except Exception as exc:
            raise RustBridgeError(f"Failed to parse audit-verify output: {exc}") from exc
