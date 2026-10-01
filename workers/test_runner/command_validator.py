"""Test Command Validator & Allowlist Engine (ADR-0044, EVIDENCE-001).

Validates structured test commands, enforces strict allowlists, blocks
shell metacharacters, prevents command injection, and guarantees that working
directories and target paths remain strictly contained within the authorized
repository boundary.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from core.space.test_execution_protocol import (
    TestCommand,
    TestCommandValidationError,
)

# Allowed test runner commands
_ALLOWED_RUNNERS = {
    "pytest",
    "python -m pytest",
    "python3 -m pytest",
    "unittest",
    "python -m unittest",
    "python3 -m unittest",
}

# Permitted pytest / unittest arguments
_ALLOWED_ARG_PREFIXES = (
    "-v",
    "-q",
    "-s",
    "-x",
    "--maxfail=",
    "-k",
    "-m",
    "--tb=",
    "--tb",
    "--disable-warnings",
    "-ra",
    "-rf",
    "-rs",
    "--strict-markers",
    "--no-header",
    "--no-summary",
    "--capture=",
    "-o",
    "--override-ini=cache_dir",  # only safe cache overrides
)

# Disallowed dangerous pytest arguments
_DANGEROUS_ARG_PATTERNS = re.compile(
    r"(?i)(--pdb|--trace|--pyargs|-p\s+no:|--override-ini(?!=[a-zA-Z0-9_]*cache_dir)|--import-mode=importlib)"
)

# Shell metacharacters and control characters
_SHELL_META_PATTERN = re.compile(r"[;&|`$><\n\r\x00]")


def validate_and_resolve_test_command(
    command: TestCommand,
    repository_root: Path | str,
) -> tuple[list[str], Path]:
    """Validate a TestCommand and resolve it into an argument vector and safe working directory.

    Returns:
        tuple of (argv: list[str], safe_working_dir: Path)

    Raises:
        TestCommandValidationError: If any safety, containment, or allowlist policy is violated.
    """
    repo_path = Path(repository_root).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise TestCommandValidationError(
            f"Repository root '{repository_root}' does not exist or is not a directory"
        )

    # 1. Validate runner allowlist
    clean_runner = command.runner.strip()
    if clean_runner not in _ALLOWED_RUNNERS:
        raise TestCommandValidationError(
            f"Test runner '{clean_runner}' is not in the authorized test runner allowlist: "
            f"{sorted(_ALLOWED_RUNNERS)}"
        )

    # Build base argv using current Python executable for hermiticity and environment binding
    py_exec = sys.executable
    if "pytest" in clean_runner:
        argv = [py_exec, "-m", "pytest"]
    elif "unittest" in clean_runner:
        argv = [py_exec, "-m", "unittest"]
    else:
        raise TestCommandValidationError(f"Unsupported runner ecosystem: '{clean_runner}'")

    # 2. Validate arguments
    for arg in command.arguments:
        clean_arg = arg.strip()
        if not clean_arg:
            continue

        if _SHELL_META_PATTERN.search(clean_arg):
            raise TestCommandValidationError(
                f"Test argument '{clean_arg}' contains dangerous shell metacharacters"
            )

        if _DANGEROUS_ARG_PATTERNS.search(clean_arg):
            raise TestCommandValidationError(
                f"Test argument '{clean_arg}' contains dangerous or unauthorized execution flag"
            )

        argv.append(clean_arg)

    # 3. Validate and resolve working directory
    working_dir = repo_path
    if command.working_directory:
        raw_work = command.working_directory.strip()
        if _SHELL_META_PATTERN.search(raw_work):
            raise TestCommandValidationError(
                f"Working directory '{raw_work}' contains shell metacharacters"
            )
        cand_work = (repo_path / raw_work).resolve()
        try:
            cand_work.relative_to(repo_path)
        except ValueError:
            raise TestCommandValidationError(
                f"Working directory '{raw_work}' resolves outside repository root '{repo_path}'"
            )
        if not cand_work.exists() or not cand_work.is_dir():
            raise TestCommandValidationError(
                f"Working directory '{cand_work}' does not exist or is not a directory"
            )
        working_dir = cand_work

    # 4. Validate and resolve target paths
    for target in command.target_paths:
        clean_target = target.strip()
        if not clean_target:
            continue

        if _SHELL_META_PATTERN.search(clean_target):
            raise TestCommandValidationError(
                f"Target test path '{clean_target}' contains shell metacharacters"
            )

        # Separate node ID if present (e.g. test_file.py::test_fn)
        file_part = clean_target.split("::")[0]
        cand_path = (working_dir / file_part).resolve()

        try:
            cand_path.relative_to(repo_path)
        except ValueError:
            raise TestCommandValidationError(
                f"Target path '{clean_target}' resolves outside repository root '{repo_path}'"
            )

        # Sensitive path check: cannot run tests inside sensitive credential dirs
        lowered = cand_path.name.lower()
        if lowered in (".env", "id_rsa", "id_ed25519", "credentials.json"):
            raise TestCommandValidationError(
                f"Target path '{clean_target}' targets a protected sensitive file"
            )

        argv.append(clean_target)

    return argv, working_dir
