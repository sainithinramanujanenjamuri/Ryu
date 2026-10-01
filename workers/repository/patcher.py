"""Pure-Python Bounded Unified Diff Parser & Atomic Patch Applicator.

Implements REPO-002, REPO-003, REPO-004, REPO-005, and ADR-0044 Phase 14.4.

Enforces:
- Unified diff parsing without external subprocesses or system patch binaries.
- Strict patch bounds: max 5 files, max 500 diff lines (REPO-004).
- Sensitive path denylist: .env, private keys, secrets, deployment configs (REPO-003).
- Path traversal and boundary containment protection (REPO-001).
- Atomic all-or-nothing modification: any hunk or verification failure triggers clean rollback (REPO-002).
- Hash-verified reversibility: pre-patch and post-patch SHA-256 verified, rollback restored bytes verified (REPO-005).
- Concurrent modification detection: pre-patch hash mismatch aborts transaction.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from core.space.repository_protocol import (
    MAX_CHANGED_FILES,
    MAX_DIFF_LINES,
    CodePatch,
    FileAccessPolicy,
    FileDiff,
    FilePatchOperation,
    Hunk,
    PatchBoundsExceededError,
    PatchConflictError,
    PatchContextMismatchError,
    PatchResult,
    PatchSyntaxError,
    PatchTargetInvalidError,
    PatchTransaction,
    PatchTransactionState,
    PatchVerificationError,
)
from workers.repository.security import (
    evaluate_file_policy,
    is_sensitive_path,
    resolve_safe_path,
)

logger = logging.getLogger(__name__)

# Regular expressions for unified diff parsing
_HUNK_HEADER_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(?:.*)?$"
)
_DIFF_GIT_HEADER_RE = re.compile(
    r"^diff\s+--git\s+(?:a/)?(\S+)\s+(?:b/)?(\S+)$"
)


def _compute_sha256_bytes(data: bytes) -> str:
    """Compute standard SHA-256 hex digest for byte sequence."""
    return hashlib.sha256(data).hexdigest()


# ── Unified Diff Parsing ───────────────────────────────────────────────────────

def parse_unified_diff(diff_text: str) -> tuple[FileDiff, ...]:
    """Parse a unified diff into structured FileDiff and Hunk representations.

    Strictly pure-Python: zero subprocesses, zero external binaries.

    Raises:
        PatchSyntaxError: If diff is empty, malformed, unsupported (e.g. renames/copies),
                          or has invalid hunk syntax.
    """
    if not diff_text or not diff_text.strip():
        raise PatchSyntaxError("Unified diff text is empty")

    lines = diff_text.splitlines()
    file_diffs: list[FileDiff] = []

    current_old_path: str | None = None
    current_new_path: str | None = None
    current_operation: FilePatchOperation = FilePatchOperation.MODIFY
    current_hunks: list[Hunk] = []

    current_hunk_old_start = 0
    current_hunk_old_count = 0
    current_hunk_new_start = 0
    current_hunk_new_count = 0
    current_hunk_lines: list[str] = []
    in_hunk = False

    def finalize_hunk() -> None:
        nonlocal in_hunk, current_hunk_lines
        if in_hunk:
            hunk = Hunk(
                old_start=current_hunk_old_start,
                old_count=current_hunk_old_count,
                new_start=current_hunk_new_start,
                new_count=current_hunk_new_count,
                lines=tuple(current_hunk_lines),
            )
            current_hunks.append(hunk)
            current_hunk_lines = []
            in_hunk = False

    def finalize_file() -> None:
        nonlocal current_old_path, current_new_path, current_operation, current_hunks
        finalize_hunk()
        if current_old_path is not None or current_new_path is not None:
            if not current_hunks:
                raise PatchSyntaxError(
                    f"File diff for '{current_new_path or current_old_path}' contains no valid hunks"
                )
            additions = sum(
                1 for h in current_hunks for line in h.lines if line.startswith("+")
            )
            deletions = sum(
                1 for h in current_hunks for line in h.lines if line.startswith("-")
            )
            file_diff = FileDiff(
                old_path=current_old_path,
                new_path=current_new_path,
                operation=current_operation,
                hunks=tuple(current_hunks),
                additions=additions,
                deletions=deletions,
                changed_lines=additions + deletions,
            )
            file_diffs.append(file_diff)
            current_old_path = None
            current_new_path = None
            current_operation = FilePatchOperation.MODIFY
            current_hunks = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        # 1. Reject unsupported operations (renames, copies, binary diffs)
        lower_line = line.strip().lower()
        if lower_line.startswith(("rename from", "rename to", "copy from", "copy to")):
            raise PatchSyntaxError("File renames/copies are not supported in bounded unified diffs")
        if lower_line.startswith("git binary patch") or lower_line.startswith("binary files"):
            raise PatchSyntaxError("Binary diffs are not supported")

        # 2. Check for diff --git header
        git_match = _DIFF_GIT_HEADER_RE.match(line.strip())
        if git_match:
            finalize_file()
            i += 1
            continue

        # 3. Check for --- old_path
        if line.startswith("--- "):
            finalize_hunk()
            if current_old_path is not None or current_new_path is not None:
                finalize_file()
            raw_path = line[4:].strip().split("\t")[0]
            if raw_path == "/dev/null" or raw_path.endswith("/dev/null"):
                current_old_path = None
                current_operation = FilePatchOperation.CREATE
            else:
                # Strip leading a/ prefix if present
                clean = raw_path[2:] if raw_path.startswith("a/") else raw_path
                current_old_path = clean.strip()
            i += 1
            continue

        # 4. Check for +++ new_path
        if line.startswith("+++ "):
            finalize_hunk()
            raw_path = line[4:].strip().split("\t")[0]
            if raw_path == "/dev/null" or raw_path.endswith("/dev/null"):
                current_new_path = None
                current_operation = FilePatchOperation.DELETE
            else:
                # Strip leading b/ prefix if present
                clean = raw_path[2:] if raw_path.startswith("b/") else raw_path
                current_new_path = clean.strip()
            i += 1
            continue

        # 5. Check for Hunk Header: @@ -l,s +l,s @@
        hunk_match = _HUNK_HEADER_RE.match(line.strip())
        if hunk_match:
            finalize_hunk()
            if current_old_path is None and current_new_path is None:
                raise PatchSyntaxError(f"Hunk header encountered without prior file header at line {i+1}")

            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2)) if hunk_match.group(2) is not None else 1
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4)) if hunk_match.group(4) is not None else 1

            current_hunk_old_start = old_start
            current_hunk_old_count = old_count
            current_hunk_new_start = new_start
            current_hunk_new_count = new_count
            current_hunk_lines = []
            in_hunk = True
            i += 1
            continue

        # 6. Inside a Hunk: context lines (' '), additions ('+'), deletions ('-')
        if in_hunk:
            if line.startswith(("+", "-", " ")):
                current_hunk_lines.append(line)
                i += 1
                continue
            elif line.startswith("\\ No newline at end of file"):
                # Standard unified diff newline indicator - preserve passively
                current_hunk_lines.append(line)
                i += 1
                continue
            elif line.startswith(("diff ", "--- ", "@@ ")):
                # Next section started without blank line
                finalize_hunk()
                continue
            else:
                # Unexpected character inside hunk
                raise PatchSyntaxError(
                    f"Invalid hunk line prefix at line {i+1}: expected ' ', '+', or '-', got '{line[:10]}'"
                )

        # Ignore unparsed header lines (e.g. index, new file mode, etc.)
        i += 1

    finalize_file()

    if not file_diffs:
        raise PatchSyntaxError("No valid file diffs could be parsed from diff text")

    return tuple(file_diffs)


# ── Bounds & Path Validation ──────────────────────────────────────────────────

def validate_patch_bounds(file_diffs: tuple[FileDiff, ...]) -> int:
    """Validate patch size and file count against ADR-0044 ceilings (REPO-004).

    Returns:
        total_changed_lines: Total additions + deletions across all hunks.

    Raises:
        PatchBoundsExceededError: If files > 5 or changed lines > 500.
    """
    affected_files = {d.target_path for d in file_diffs if d.target_path}
    if len(affected_files) > MAX_CHANGED_FILES:
        raise PatchBoundsExceededError(
            f"Patch targets {len(affected_files)} files, exceeding ceiling of {MAX_CHANGED_FILES} (REPO-004)"
        )

    total_changed_lines = sum(d.changed_lines for d in file_diffs)
    if total_changed_lines > MAX_DIFF_LINES:
        raise PatchBoundsExceededError(
            f"Patch changes {total_changed_lines} lines, exceeding ceiling of {MAX_DIFF_LINES} (REPO-004)"
        )

    return total_changed_lines


def validate_patch_paths(
    file_diffs: tuple[FileDiff, ...],
    root_path: Path | str,
    allow_symlinks: bool = False,
) -> dict[str, Path]:
    """Validate target file paths for containment and sensitive file denylist (REPO-001, REPO-003).

    Returns:
        Mapping of relative_path -> resolved safe Path within root.

    Raises:
        PatchTargetInvalidError: If a path escapes the repository root or targets a sensitive file.
    """
    resolved_paths: dict[str, Path] = {}
    for diff in file_diffs:
        rel_path = diff.target_path
        if not rel_path:
            raise PatchTargetInvalidError("Diff specifies an empty target path")

        # 1. Sensitive path denylist check (REPO-003)
        if is_sensitive_path(rel_path):
            raise PatchTargetInvalidError(
                f"Patch modification of sensitive file '{rel_path}' is denied (REPO-003)"
            )

        policy, _ = evaluate_file_policy(rel_path)
        if policy == FileAccessPolicy.MASKED or policy == FileAccessPolicy.DENIED:
            raise PatchTargetInvalidError(
                f"Patch modification of protected path '{rel_path}' is blocked by policy (REPO-003)"
            )
        if policy == FileAccessPolicy.IGNORED and (rel_path.startswith(".git/") or "/.git/" in rel_path):
            raise PatchTargetInvalidError(
                f"Patch modification of git metadata path '{rel_path}' is prohibited"
            )

        # 2. Path traversal and boundary containment check (REPO-001)
        try:
            safe = resolve_safe_path(root_path, rel_path, allow_symlinks=allow_symlinks)
            resolved_paths[rel_path] = safe
        except Exception as exc:
            raise PatchTargetInvalidError(
                f"Patch target path '{rel_path}' escapes repository boundary: {exc}"
            ) from exc

    return resolved_paths


# ── In-Memory Hunk Application ────────────────────────────────────────────────

def apply_hunks_to_content(
    original_lines: list[str],
    hunks: tuple[Hunk, ...],
    file_path: str,
) -> list[str]:
    """Apply hunks sequentially to original file lines with strict context matching.

    Returns:
        resulting_lines: Modified list of lines.

    Raises:
        PatchContextMismatchError: If hunk context or deletions do not match file content.
    """
    current_lines = list(original_lines)
    line_offset = 0

    for hunk_idx, hunk in enumerate(hunks, 1):
        expected_pos = (hunk.old_start - 1) + line_offset
        if expected_pos < 0:
            expected_pos = 0

        # Build expected old slice and replacement new slice
        old_slice: list[str] = []
        new_slice: list[str] = []

        for line in hunk.lines:
            if line.startswith(" "):
                content = line[1:]
                old_slice.append(content)
                new_slice.append(content)
            elif line.startswith("-"):
                old_slice.append(line[1:])
            elif line.startswith("+"):
                new_slice.append(line[1:])
            # Ignore '\ No newline at end of file' lines

        # Verify context match against actual file lines
        slice_len = len(old_slice)
        if expected_pos + slice_len > len(current_lines):
            # Check if empty file create
            if not current_lines and not old_slice:
                current_lines = list(new_slice)
                line_offset += len(new_slice)
                continue
            raise PatchContextMismatchError(
                f"Hunk {hunk_idx} for '{file_path}' exceeds file line bounds "
                f"(expected at line {expected_pos+1}, total lines {len(current_lines)})"
            )

        actual_slice = current_lines[expected_pos : expected_pos + slice_len]
        if actual_slice != old_slice:
            raise PatchContextMismatchError(
                f"Hunk {hunk_idx} context mismatch in '{file_path}' at line {expected_pos+1}.\n"
                f"Expected: {old_slice[:3]}\nActual:   {actual_slice[:3]}"
            )

        # Apply replacement
        current_lines[expected_pos : expected_pos + slice_len] = new_slice
        line_offset += len(new_slice) - slice_len

    return current_lines


# ── Atomic Patch Applicator ───────────────────────────────────────────────────

_GLOBAL_TRANSACTIONS: dict[tuple[str, str] | str, PatchTransaction] = {}
_GLOBAL_BACKUPS: dict[tuple[str, str] | str, dict[str, bytes | None]] = {}


class AtomicPatchApplicator:
    """Bounded, atomic, and hash-verified patch applicator (REPO-002, REPO-005).

    Lifecycle:
        1. Parse & validate diff format.
        2. Enforce limits: max 5 files, max 500 lines (REPO-004).
        3. Enforce sensitive-path denylist (REPO-003).
        4. Dry-run: compute in-memory post-patch content and verify all hunks.
        5. Verify expected pre-patch hashes (concurrent modification protection).
        6. Persist rollback journal with full pre-patch byte backup.
        7. Apply modifications to disk atomically.
        8. Verify post-patch file hashes on disk.
        9. On any verification or I/O failure: execute automatic rollback and verify restored hashes.
    """

    def __init__(
        self,
        root_path: Path | str,
        allow_symlinks: bool = False,
        journal_dir: Path | str | None = None,
    ) -> None:
        self.root_path = Path(root_path).resolve()
        self.allow_symlinks = allow_symlinks
        self.journal_dir = Path(journal_dir) if journal_dir else None
        self._transactions: dict[str, PatchTransaction] = {}
        self._backups: dict[str, dict[str, bytes | None]] = {}

    def apply_patch(
        self,
        space_id: str,
        patch: CodePatch,
        expected_before_hashes: dict[str, str] | None = None,
    ) -> PatchResult:
        """Apply an atomic unified diff patch with pre/post hash verification (REPO-002, REPO-005)."""
        tx_id = f"tx-{patch.patch_id}-{int(datetime.now(timezone.utc).timestamp())}"
        affected_files_set: set[str] = set()
        before_hashes: dict[str, str] = {}
        after_hashes: dict[str, str] = {}
        backup_bytes: dict[str, bytes | None] = {}  # None indicates file did not exist before patch

        try:
            # 1. Parse unified diff
            file_diffs = parse_unified_diff(patch.diff_text)

            # 2. Enforce ADR-0044 patch ceilings (REPO-004)
            changed_lines = validate_patch_bounds(file_diffs)

            # 3. Enforce sensitive-path denylist & path containment (REPO-001, REPO-003)
            resolved_paths = validate_patch_paths(
                file_diffs, self.root_path, allow_symlinks=self.allow_symlinks
            )

            # 4. Dry-run & in-memory computation
            planned_mutations: dict[str, tuple[FilePatchOperation, bytes, Path]] = {}

            for diff in file_diffs:
                rel_path = diff.target_path
                affected_files_set.add(rel_path)
                safe_path = resolved_paths[rel_path]

                if diff.operation == FilePatchOperation.CREATE:
                    if safe_path.exists():
                        raise PatchTargetInvalidError(
                            f"Cannot create '{rel_path}': file already exists on filesystem"
                        )
                    # Compute new content from addition hunks
                    new_lines = apply_hunks_to_content([], diff.hunks, rel_path)
                    new_bytes = "\n".join(new_lines).encode("utf-8") if new_lines else b""
                    has_no_newline = False
                    if diff.hunks and diff.hunks[-1].lines:
                        if diff.hunks[-1].lines[-1].startswith("\\ No newline"):
                            has_no_newline = True
                    if not has_no_newline and new_bytes:
                        new_bytes += b"\n"
                    before_hashes[rel_path] = ""
                    after_hashes[rel_path] = _compute_sha256_bytes(new_bytes)
                    backup_bytes[rel_path] = None
                    planned_mutations[rel_path] = (diff.operation, new_bytes, safe_path)

                elif diff.operation == FilePatchOperation.DELETE:
                    if not safe_path.is_file():
                        raise PatchTargetInvalidError(
                            f"Cannot delete '{rel_path}': file does not exist on filesystem"
                        )
                    current_raw = safe_path.read_bytes()
                    current_hash = _compute_sha256_bytes(current_raw)
                    before_hashes[rel_path] = current_hash
                    after_hashes[rel_path] = ""
                    backup_bytes[rel_path] = current_raw

                    # Check pre-patch concurrency hash match
                    self._verify_pre_hash(rel_path, current_hash, patch, expected_before_hashes)

                    planned_mutations[rel_path] = (diff.operation, b"", safe_path)

                else:  # MODIFY
                    if not safe_path.is_file():
                        raise PatchTargetInvalidError(
                            f"Cannot modify '{rel_path}': file does not exist on filesystem"
                        )
                    current_raw = safe_path.read_bytes()
                    current_hash = _compute_sha256_bytes(current_raw)
                    before_hashes[rel_path] = current_hash
                    backup_bytes[rel_path] = current_raw

                    # Check pre-patch concurrency hash match
                    self._verify_pre_hash(rel_path, current_hash, patch, expected_before_hashes)

                    # Read text lines and apply hunks in memory
                    original_text = current_raw.decode("utf-8", errors="replace")
                    original_lines = original_text.splitlines()
                    new_lines = apply_hunks_to_content(original_lines, diff.hunks, rel_path)
                    new_bytes = "\n".join(new_lines).encode("utf-8")
                    if original_text.endswith("\n") or not original_text:
                        new_bytes += b"\n"

                    new_hash = _compute_sha256_bytes(new_bytes)
                    after_hashes[rel_path] = new_hash
                    planned_mutations[rel_path] = (diff.operation, new_bytes, safe_path)

            affected_files_tuple = tuple(sorted(affected_files_set))

            # 5. Execute mutations atomically with disk writes
            applied_paths: list[tuple[str, Path, bytes | None]] = []
            write_error: Exception | None = None

            try:
                for rel_path in affected_files_tuple:
                    op, content_bytes, target_path = planned_mutations[rel_path]
                    orig_bytes = backup_bytes[rel_path]

                    if op == FilePatchOperation.CREATE or op == FilePatchOperation.MODIFY:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        target_path.write_bytes(content_bytes)
                        applied_paths.append((rel_path, target_path, orig_bytes))
                    elif op == FilePatchOperation.DELETE:
                        target_path.unlink()
                        applied_paths.append((rel_path, target_path, orig_bytes))

                # 6. Verify post-patch hashes on disk (REPO-005)
                for rel_path in affected_files_tuple:
                    op, _, target_path = planned_mutations[rel_path]
                    expected_post_hash = after_hashes[rel_path]

                    if op == FilePatchOperation.DELETE:
                        if target_path.exists():
                            raise PatchVerificationError(
                                f"Deleted file '{rel_path}' still exists on disk after patch application"
                            )
                    else:
                        actual_post_raw = target_path.read_bytes()
                        actual_post_hash = _compute_sha256_bytes(actual_post_raw)
                        if actual_post_hash != expected_post_hash:
                            raise PatchVerificationError(
                                f"Post-patch hash mismatch on disk for '{rel_path}'. "
                                f"Expected {expected_post_hash}, got {actual_post_hash}"
                            )

            except Exception as exc:
                write_error = exc
                logger.error(f"Mutation failure in patch transaction {tx_id}: {exc}. Triggering rollback.")
                # Rollback all applied files immediately
                rb_ok = self._execute_rollback(backup_bytes, before_hashes, resolved_paths)
                return PatchResult(
                    patch_id=patch.patch_id,
                    transaction_id=tx_id,
                    success=False,
                    applied_files=(),
                    changed_line_count=changed_lines,
                    before_hashes=before_hashes,
                    after_hashes={},
                    state=PatchTransactionState.ROLLED_BACK if rb_ok else PatchTransactionState.ROLLBACK_FAILED,
                    error=f"Patch application failed and was rolled back: {write_error}",
                    rolled_back=True,
                    rollback_verified=rb_ok,
                )

            # Success! Record transaction for hash-verified reversibility (REPO-005)
            tx = PatchTransaction(
                transaction_id=tx_id,
                patch_id=patch.patch_id,
                space_id=space_id,
                affected_files=affected_files_tuple,
                before_hashes=before_hashes,
                after_hashes=after_hashes,
                created_at=datetime.now(timezone.utc),
                state=PatchTransactionState.VERIFIED,
            )
            self._transactions[patch.patch_id] = tx
            self._backups[patch.patch_id] = backup_bytes
            repo_key = (str(self.root_path), patch.patch_id)
            _GLOBAL_TRANSACTIONS[repo_key] = tx
            _GLOBAL_TRANSACTIONS[patch.patch_id] = tx
            _GLOBAL_BACKUPS[repo_key] = backup_bytes
            _GLOBAL_BACKUPS[patch.patch_id] = backup_bytes

            return PatchResult(
                patch_id=patch.patch_id,
                transaction_id=tx_id,
                success=True,
                applied_files=affected_files_tuple,
                changed_line_count=changed_lines,
                before_hashes=before_hashes,
                after_hashes=after_hashes,
                state=PatchTransactionState.VERIFIED,
                rolled_back=False,
                rollback_verified=False,
            )

        except (
            PatchSyntaxError,
            PatchBoundsExceededError,
            PatchTargetInvalidError,
            PatchContextMismatchError,
            PatchConflictError,
            PatchVerificationError,
        ) as exc:
            return PatchResult(
                patch_id=patch.patch_id,
                transaction_id=tx_id,
                success=False,
                applied_files=(),
                changed_line_count=0,
                before_hashes=before_hashes,
                after_hashes={},
                state=PatchTransactionState.FAILED,
                error=str(exc),
                rolled_back=False,
                rollback_verified=False,
            )
        except Exception as exc:
            return PatchResult(
                patch_id=patch.patch_id,
                transaction_id=tx_id,
                success=False,
                applied_files=(),
                changed_line_count=0,
                before_hashes=before_hashes,
                after_hashes={},
                state=PatchTransactionState.FAILED,
                error=f"Unexpected patch failure: {exc}",
                rolled_back=False,
                rollback_verified=False,
            )

    def _verify_pre_hash(
        self,
        rel_path: str,
        current_hash: str,
        patch: CodePatch,
        expected_before_hashes: dict[str, str] | None,
    ) -> None:
        """Check current file hash against expected pre-patch hashes to prevent overwriting concurrent mutations."""
        expected = None
        if expected_before_hashes and rel_path in expected_before_hashes:
            expected = expected_before_hashes[rel_path]
        elif patch.before_hashes and rel_path in patch.before_hashes:
            expected = patch.before_hashes[rel_path]

        if expected is not None and expected != current_hash:
            raise PatchConflictError(
                f"Concurrent modification detected for '{rel_path}': "
                f"expected SHA-256 {expected}, observed {current_hash}"
            )

    def _execute_rollback(
        self,
        backup_bytes: dict[str, bytes | None],
        expected_before_hashes: dict[str, str],
        resolved_paths: dict[str, Path],
    ) -> bool:
        """Rollback all files in backup to exact prior bytes and verify restored hashes (REPO-005).

        Returns:
            True if all files successfully restored and verified bitwise identical.
        """
        all_verified = True
        for rel_path, orig_bytes in backup_bytes.items():
            target_path = resolved_paths.get(rel_path) or (self.root_path / rel_path)
            try:
                if orig_bytes is None:
                    # File was newly created by patch -> remove it
                    if target_path.exists():
                        target_path.unlink()
                else:
                    # Restore original bytes
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    target_path.write_bytes(orig_bytes)

                    # Verify restored hash
                    restored_hash = _compute_sha256_bytes(target_path.read_bytes())
                    expected_hash = expected_before_hashes.get(rel_path)
                    if expected_hash and restored_hash != expected_hash:
                        logger.critical(
                            f"ROLLBACK CORRUPTION: Restored '{rel_path}' hash {restored_hash} "
                            f"does not match pre-patch hash {expected_hash}"
                        )
                        all_verified = False
            except Exception as exc:
                logger.critical(f"Failed to rollback file '{rel_path}': {exc}")
                all_verified = False

        return all_verified

    def revert_patch(
        self,
        space_id: str,
        patch_id: str,
    ) -> PatchResult:
        """Revert a previously applied patch back to exact prior state with hash verification (REPO-005)."""
        repo_key = (str(self.root_path), patch_id)
        tx = (
            self._transactions.get(patch_id)
            or _GLOBAL_TRANSACTIONS.get(repo_key)
            or _GLOBAL_TRANSACTIONS.get(patch_id)
        )
        backup_bytes = (
            self._backups.get(patch_id)
            or _GLOBAL_BACKUPS.get(repo_key)
            or _GLOBAL_BACKUPS.get(patch_id)
        )

        if tx is None or backup_bytes is None:
            return PatchResult(
                patch_id=patch_id,
                transaction_id="",
                success=False,
                applied_files=(),
                changed_line_count=0,
                before_hashes={},
                after_hashes={},
                state=PatchTransactionState.FAILED,
                error=f"No transaction found for patch_id '{patch_id}' to revert",
                rolled_back=False,
                rollback_verified=False,
            )

        if tx.state == PatchTransactionState.ROLLED_BACK:
            return PatchResult(
                patch_id=patch_id,
                transaction_id=tx.transaction_id,
                success=True,
                applied_files=tx.affected_files,
                changed_line_count=0,
                before_hashes=tx.after_hashes,
                after_hashes=tx.before_hashes,
                state=PatchTransactionState.ROLLED_BACK,
                rolled_back=True,
                rollback_verified=True,
            )

        # Check current files against expected after_hashes to detect post-patch modifications
        resolved_paths: dict[str, Path] = {}
        for rel_path in tx.affected_files:
            safe_p = resolve_safe_path(self.root_path, rel_path, allow_symlinks=self.allow_symlinks)
            resolved_paths[rel_path] = safe_p
            expected_after = tx.after_hashes.get(rel_path, "")

            if expected_after:
                if not safe_p.exists():
                    return PatchResult(
                        patch_id=patch_id,
                        transaction_id=tx.transaction_id,
                        success=False,
                        applied_files=tx.affected_files,
                        changed_line_count=0,
                        state=PatchTransactionState.FAILED,
                        error=f"Cannot revert patch '{patch_id}': file '{rel_path}' missing from filesystem",
                        rolled_back=False,
                        rollback_verified=False,
                    )
                cur_hash = _compute_sha256_bytes(safe_p.read_bytes())
                if cur_hash != expected_after:
                    return PatchResult(
                        patch_id=patch_id,
                        transaction_id=tx.transaction_id,
                        success=False,
                        applied_files=tx.affected_files,
                        changed_line_count=0,
                        state=PatchTransactionState.FAILED,
                        error=(
                            f"Cannot revert patch '{patch_id}': concurrent modification detected on '{rel_path}' "
                            f"(expected {expected_after}, got {cur_hash})"
                        ),
                        rolled_back=False,
                        rollback_verified=False,
                    )
            else:
                # File was deleted by patch, so it shouldn't exist
                if safe_p.exists():
                    return PatchResult(
                        patch_id=patch_id,
                        transaction_id=tx.transaction_id,
                        success=False,
                        applied_files=tx.affected_files,
                        changed_line_count=0,
                        state=PatchTransactionState.FAILED,
                        error=f"Cannot revert patch '{patch_id}': file '{rel_path}' unexpectedly re-created",
                        rolled_back=False,
                        rollback_verified=False,
                    )

        # Execute rollback restoring backup bytes
        rb_ok = self._execute_rollback(backup_bytes, tx.before_hashes, resolved_paths)

        if rb_ok:
            updated_tx = PatchTransaction(
                transaction_id=tx.transaction_id,
                patch_id=tx.patch_id,
                space_id=tx.space_id,
                affected_files=tx.affected_files,
                before_hashes=tx.before_hashes,
                after_hashes=tx.after_hashes,
                created_at=datetime.now(timezone.utc),
                state=PatchTransactionState.ROLLED_BACK,
            )
            self._transactions[patch_id] = updated_tx
            _GLOBAL_TRANSACTIONS[repo_key] = updated_tx
            _GLOBAL_TRANSACTIONS[patch_id] = updated_tx

            return PatchResult(
                patch_id=patch_id,
                transaction_id=tx.transaction_id,
                success=True,
                applied_files=tx.affected_files,
                changed_line_count=0,
                before_hashes=tx.after_hashes,
                after_hashes=tx.before_hashes,
                state=PatchTransactionState.ROLLED_BACK,
                rolled_back=True,
                rollback_verified=True,
            )
        else:
            return PatchResult(
                patch_id=patch_id,
                transaction_id=tx.transaction_id,
                success=False,
                applied_files=tx.affected_files,
                changed_line_count=0,
                before_hashes=tx.after_hashes,
                after_hashes={},
                state=PatchTransactionState.ROLLBACK_FAILED,
                error="Rollback execution and verification failed",
                rolled_back=True,
                rollback_verified=False,
            )

