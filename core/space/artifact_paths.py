"""Space-Safe Artifact Namespace Authority — Phase 15.2 (Finding F-02).

Enforces space-partitioned artifact directory hierarchy, canonical path resolution,
and strict containment validation (SCCA Laws 1, 2, 4, CONTRACT SPACE-ART-001, ADR-0046).

Canonical structure:
    <base_working_dir>/artifacts/<space_id>/<worker_type>/<filename>

Core Boundary Rule:
    This module is part of the deterministic core (core/space/) and MUST NOT
    import anything from agents/, workers/, skills/, workflows/, llm/, channels/, or memory/.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

# Allowed canonical worker namespaces
CANONICAL_WORKER_NAMESPACES: Final[frozenset[str]] = frozenset(
    {
        "repository",
        "test_runner",
        "research",
        "python",
        "shell",
        "file",
    }
)

# Windows reserved device names (checked case-insensitively)
WINDOWS_RESERVED_NAMES: Final[frozenset[str]] = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "com1",
        "com2",
        "com3",
        "com4",
        "com5",
        "com6",
        "com7",
        "com8",
        "com9",
        "lpt1",
        "lpt2",
        "lpt3",
        "lpt4",
        "lpt5",
        "lpt6",
        "lpt7",
        "lpt8",
        "lpt9",
    }
)

# Regex constraints
_SPACE_ID_REGEX: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9_\-.]{1,128}$")
_WORKER_TYPE_REGEX: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9_\-.]{1,64}$")
_FILENAME_REGEX: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9_\-.]{1,256}$")


def validate_space_id(space_id: str) -> str:
    """Validate a space_id string for filesystem safety.

    Rejects:
    - Empty strings or strings longer than 128 chars
    - Directory traversal sequences ('.', '..', or containing '..')
    - Path separators ('/', '\\')
    - Drive letters, colons, null bytes
    - Windows reserved device names
    - Non-alphanumeric characters outside [-_.]
    """
    if not isinstance(space_id, str):
        raise TypeError(f"space_id must be str, got {type(space_id).__name__}")
    if not space_id:
        raise ValueError("space_id cannot be empty")
    if "\x00" in space_id:
        raise ValueError("space_id cannot contain null bytes")
    if "/" in space_id or "\\" in space_id:
        raise ValueError(f"space_id cannot contain path separators: {space_id!r}")
    if ":" in space_id:
        raise ValueError(f"space_id cannot contain colons: {space_id!r}")
    if space_id in (".", "..") or ".." in space_id:
        raise ValueError(f"space_id cannot contain traversal tokens: {space_id!r}")
    if not _SPACE_ID_REGEX.match(space_id):
        raise ValueError(
            f"space_id contains invalid characters or exceeds 128 chars: {space_id!r}"
        )

    base_name = space_id.split(".")[0].lower()
    if base_name in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"space_id cannot be a Windows reserved device name: {space_id!r}")

    return space_id


def validate_worker_namespace(worker_type: str) -> str:
    """Validate a worker namespace identifier.

    Must be either one of CANONICAL_WORKER_NAMESPACES or a strictly validated
    alphanumeric string.
    """
    if not isinstance(worker_type, str):
        raise TypeError(f"worker_type must be str, got {type(worker_type).__name__}")
    if not worker_type:
        raise ValueError("worker_type cannot be empty")
    if "\x00" in worker_type:
        raise ValueError("worker_type cannot contain null bytes")
    if "/" in worker_type or "\\" in worker_type:
        raise ValueError(f"worker_type cannot contain path separators: {worker_type!r}")
    if ":" in worker_type:
        raise ValueError(f"worker_type cannot contain colons: {worker_type!r}")
    if worker_type in (".", "..") or ".." in worker_type:
        raise ValueError(f"worker_type cannot contain traversal tokens: {worker_type!r}")
    if not _WORKER_TYPE_REGEX.match(worker_type):
        raise ValueError(
            f"worker_type contains invalid characters or exceeds 64 chars: {worker_type!r}"
        )

    base_name = worker_type.split(".")[0].lower()
    if base_name in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"worker_type cannot be a Windows reserved device name: {worker_type!r}")

    return worker_type


def validate_artifact_filename(filename: str) -> str:
    """Validate that a filename represents a single safe filesystem component.

    Rejects:
    - Empty filenames or filenames exceeding 256 chars
    - Path separators ('/', '\\')
    - Traversal tokens ('.', '..')
    - Null bytes, colons
    - Windows reserved device names
    """
    if not isinstance(filename, str):
        raise TypeError(f"filename must be str, got {type(filename).__name__}")
    if not filename:
        raise ValueError("filename cannot be empty")
    if "\x00" in filename:
        raise ValueError("filename cannot contain null bytes")
    if "/" in filename or "\\" in filename:
        raise ValueError(f"filename cannot contain path separators: {filename!r}")
    if ":" in filename:
        raise ValueError(f"filename cannot contain colons: {filename!r}")
    if filename in (".", "..") or ".." in filename:
        raise ValueError(f"filename cannot contain traversal tokens: {filename!r}")
    if not _FILENAME_REGEX.match(filename):
        raise ValueError(
            f"filename contains invalid characters or exceeds 256 chars: {filename!r}"
        )

    base_name = filename.split(".")[0].lower()
    if base_name in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"filename cannot be a Windows reserved device name: {filename!r}")

    return filename


def get_space_artifact_dir(
    base_dir: Path | str,
    space_id: str,
    worker_type: str | None = None,
) -> Path:
    """Return the authoritative directory path for space-isolated artifacts.

    Canonical structure:
        <base_dir>/artifacts/<space_id>[/<worker_type>]
    """
    valid_space = validate_space_id(space_id)
    base_path = Path(base_dir).resolve()
    space_art_root = (base_path / "artifacts" / valid_space).resolve()

    # Verify containment of space_art_root inside base_path
    try:
        space_art_root.relative_to(base_path)
    except ValueError as exc:
        raise ValueError(
            f"Space artifact root escapes base_dir boundary: {space_art_root} not in {base_path}"
        ) from exc

    if worker_type is not None:
        valid_worker = validate_worker_namespace(worker_type)
        target_dir = (space_art_root / valid_worker).resolve()
        try:
            target_dir.relative_to(space_art_root)
        except ValueError as exc:
            raise ValueError(
                f"Worker artifact directory escapes space artifact root: {target_dir} not in {space_art_root}"
            ) from exc
        return target_dir

    return space_art_root


def resolve_artifact_path(
    base_dir: Path | str,
    space_id: str,
    worker_type: str,
    filename: str,
) -> Path:
    """Resolve and validate an authoritative artifact path within the space namespace.

    Canonical structure:
        <base_dir>/artifacts/<space_id>/<worker_type>/<filename>

    Guarantees:
    - Path components are strictly validated
    - Target directory resolves within space artifact root
    - Final path resolves strictly within the target directory
    - Fails closed on any escape attempt
    """
    valid_filename = validate_artifact_filename(filename)
    target_dir = get_space_artifact_dir(base_dir, space_id, worker_type)
    resolved_path = (target_dir / valid_filename).resolve()

    try:
        resolved_path.relative_to(target_dir)
    except ValueError as exc:
        raise ValueError(
            f"Artifact path escapes worker directory boundary: {resolved_path} not in {target_dir}"
        ) from exc

    return resolved_path


def is_safe_artifact_path(
    raw_path: str | Path,
    base_dir: Path | str | None,
    space_id: str,
) -> tuple[bool, Path | None, str | None]:
    """Deterministically check if an artifact path is safely contained within space boundaries.

    Fails closed:
    - Rejects null bytes, colons in relative paths, or '..' traversal components
    - If base_dir is supplied, requires strict containment within either:
        (base_dir / "artifacts" / space_id)  [Phase 15.2 canonical structure]
        or (base_dir / space_id)            [Space root structure]
    - DOES NOT permit general containment under base_dir (removes old fallback defect)
    """
    path_str = str(raw_path)
    if not path_str:
        return False, None, "Artifact path is empty"
    if "\x00" in path_str:
        return False, None, "Artifact path contains null bytes"

    # Normalize separators for inspection
    clean_path = path_str.replace("\\", "/").strip()
    parts = clean_path.split("/")
    if ".." in parts:
        return False, None, "Path traversal forbidden ('..' detected)"

    try:
        validate_space_id(space_id)
    except (ValueError, TypeError) as exc:
        return False, None, f"Invalid space_id for artifact containment: {exc}"

    candidate = Path(raw_path)

    if base_dir is not None:
        base_root = Path(base_dir).resolve()
        space_art_root = (base_root / "artifacts" / space_id).resolve()
        space_root = (base_root / space_id).resolve()

        if candidate.is_absolute():
            resolved = candidate.resolve()
        elif parts and (parts[0] == "artifacts" or parts[0] == space_id):
            # Path is expressed relative to base_root (e.g. artifacts/<space_id>/...)
            resolved = (base_root / raw_path).resolve()
        else:
            # Relative path within space: check if it exists under space_root or space_art_root
            candidate_art = (space_art_root / raw_path).resolve()
            candidate_space = (space_root / raw_path).resolve()
            if candidate_art.exists():
                resolved = candidate_art
            elif candidate_space.exists():
                resolved = candidate_space
            else:
                # Default to canonical space_art_root
                resolved = candidate_art

        # Strict containment verification: MUST be in space_art_root OR space_root
        in_art_root = False
        in_space_root = False
        try:
            resolved.relative_to(space_art_root)
            in_art_root = True
        except ValueError:
            pass

        try:
            resolved.relative_to(space_root)
            in_space_root = True
        except ValueError:
            pass

        if not (in_art_root or in_space_root):
            return (
                False,
                None,
                f"Artifact path escapes space sandbox boundary: '{resolved}' is not within '{space_art_root}' or '{space_root}'",
            )

        return True, resolved, None

    return True, candidate, None
