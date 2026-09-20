"""SandboxManager coordinating filesystem, network, process, and seccomp containment.

spec §7 (Execution Layer), §10 (Prompt-Injection & Taint Model),
CONTRACT_MATRIX WORKER-003, ADR-0014
"""

from __future__ import annotations

from pathlib import Path

from workers.contract import ExecutionLimits, SandboxPolicy
from workers.sandbox.filesystem import FilesystemSandbox
from workers.sandbox.network import NetworkSandbox
from workers.sandbox.process import ProcessSandbox
from workers.sandbox.seccomp import PlatformSecurityAdapter, SeccompFilter


class SandboxManager:
    """Unified sandbox manager for capability worker execution."""

    def __init__(
        self,
        working_dir: Path | str,
        policy: SandboxPolicy | None = None,
        limits: ExecutionLimits | None = None,
    ) -> None:
        self.working_dir = Path(working_dir)
        self.policy = policy or SandboxPolicy()
        self.limits = limits or ExecutionLimits()

        self.filesystem = FilesystemSandbox(self.policy.fs_policy)
        self.network = NetworkSandbox(self.policy.network_policy)
        self.process = ProcessSandbox(
            working_dir=self.working_dir,
            limits=self.limits,
            env_allowlist=self.policy.env_allowlist,
        )
        self.seccomp = SeccompFilter(profile=self.policy.seccomp_profile)
        self.security_adapter = PlatformSecurityAdapter()

    def run_command(
        self,
        command: list[str],
        input_data: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Execute a subprocess command inside the sandbox."""
        self.security_adapter.record_event(
            action="process_exec",
            target=command[0] if command else "",
            result="allowed",
            details={"args_count": len(command)},
        )
        with self.network.intercept_sockets():
            return self.process.run_command(
                command=command,
                input_data=input_data,
                extra_env=extra_env,
            )

    def validate_file_read(self, path: str | Path) -> Path:
        """Validate read access to a filesystem path."""
        try:
            p = self.filesystem.validate_read(path)
            self.security_adapter.record_event(
                action="file_read", target=str(path), result="allowed"
            )
            return p
        except PermissionError as exc:
            self.security_adapter.record_event(
                action="file_read",
                target=str(path),
                result="blocked",
                details={"reason": str(exc)},
            )
            raise

    def validate_file_write(self, path: str | Path) -> Path:
        """Validate write access to a filesystem path."""
        try:
            p = self.filesystem.validate_write(path)
            self.security_adapter.record_event(
                action="file_write", target=str(path), result="allowed"
            )
            return p
        except PermissionError as exc:
            self.security_adapter.record_event(
                action="file_write",
                target=str(path),
                result="blocked",
                details={"reason": str(exc)},
            )
            raise

    def validate_network_egress(self, host: str, port: int) -> None:
        """Validate network connection egress."""
        try:
            self.network.validate_connection(host, port)
            self.security_adapter.record_event(
                action="network_egress", target=f"{host}:{port}", result="allowed"
            )
        except PermissionError as exc:
            self.security_adapter.record_event(
                action="network_egress",
                target=f"{host}:{port}",
                result="blocked",
                details={"reason": str(exc)},
            )
            raise
