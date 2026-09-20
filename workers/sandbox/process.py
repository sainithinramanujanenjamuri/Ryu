"""Process lifecycle management, environment isolation, and tree termination.

spec §7 (Execution Layer), §10 (Prompt-Injection & Taint Model),
CONTRACT_MATRIX WORKER-003, ADR-0014
"""

from __future__ import annotations

import logging
import os
import platform
import signal
import subprocess
from pathlib import Path

from workers.contract import ExecutionLimits

logger = logging.getLogger(__name__)

# Default minimal environment variable allowlist
DEFAULT_ENV_ALLOWLIST = [
    "PATH",
    "TEMP",
    "TMP",
    "PYTHONPATH",
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
]


def sanitize_environment(
    allowlist: list[str] | None = None, extra_env: dict[str, str] | None = None
) -> dict[str, str]:
    """Return a stripped environment dictionary containing only allowlisted keys.

    Guarantees host credentials (AWS, OpenAI, Anthropic, DB URLs, etc.) are stripped.
    """
    keys = allowlist or DEFAULT_ENV_ALLOWLIST
    clean_env: dict[str, str] = {}
    for key in keys:
        if key in os.environ:
            clean_env[key] = os.environ[key]

    if extra_env:
        for k, v in extra_env.items():
            clean_env[k] = str(v)

    return clean_env


def terminate_process_tree(pid: int) -> None:
    """Unconditionally terminate a process and all of its child processes."""
    if pid <= 0:
        return

    # Try psutil if available
    try:
        import psutil  # type: ignore[import-untyped]

        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        try:
            parent.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("psutil process tree kill failed (%s), falling back to platform tools", exc)

    # Platform-specific fallback
    is_windows = platform.system() == "Windows"
    if is_windows:
        try:
            # taskkill /F /T /PID <pid> forcefully terminates process and children
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception as exc:
            logger.debug("taskkill fallback failed: %s", exc)
    else:
        try:
            getpgid = getattr(os, "getpgid", None)
            killpg = getattr(os, "killpg", None)
            sigkill = getattr(signal, "SIGKILL", getattr(signal, "SIGTERM", 9))
            if getpgid and killpg:
                pgid = getpgid(pid)
                killpg(pgid, sigkill)
            else:
                os.kill(pid, sigkill)
        except Exception:
            try:
                sigkill = getattr(signal, "SIGKILL", getattr(signal, "SIGTERM", 9))
                os.kill(pid, sigkill)
            except Exception:
                pass


class ProcessSandbox:
    """Executes subprocesses within an isolated working directory and stripped environment."""

    def __init__(
        self,
        working_dir: Path | str,
        limits: ExecutionLimits | None = None,
        env_allowlist: list[str] | None = None,
    ) -> None:
        self.working_dir = Path(working_dir)
        self.limits = limits or ExecutionLimits()
        self.env_allowlist = env_allowlist or DEFAULT_ENV_ALLOWLIST

    def run_command(
        self,
        command: list[str],
        input_data: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Execute a command in the sandbox with timeout and process-tree cleanup.

        Returns (exit_code, stdout_str, stderr_str).
        Raises TimeoutError if execution exceeds timeout_seconds.
        """
        env = sanitize_environment(self.env_allowlist, extra_env=extra_env)

        proc = None
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE if input_data else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self.working_dir),
                env=env,
            )

            stdout_data, stderr_data = proc.communicate(
                input=input_data, timeout=self.limits.timeout_seconds
            )
            return proc.returncode, stdout_data, stderr_data

        except subprocess.TimeoutExpired:
            if proc:
                terminate_process_tree(proc.pid)
            raise TimeoutError(
                f"Subprocess exceeded timeout limit of {self.limits.timeout_seconds}s"
            )
        except Exception as exc:
            if proc:
                terminate_process_tree(proc.pid)
            raise exc
        finally:
            if proc and proc.poll() is None:
                terminate_process_tree(proc.pid)
