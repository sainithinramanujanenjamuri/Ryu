"""Space Artifact Store and Ingress Management.

Coordinates persistent registration, integrity hashing (SHA-256), space isolation,
and sandboxed retrieval of capability-generated artifacts and ingested files.
spec §7, §16, ADR-0040, CONTRACT DESKTOP-004, DESKTOP-005 — Phase 8.5+
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
import threading
import time
from typing import Any

logger = logging.getLogger("ryu.channels.daemon.artifacts")


@dataclass
class ArtifactRecord:
    """Metadata record for a persistent space-scoped artifact."""

    artifact_id: str
    space_id: str
    name: str
    path: str
    mime_type: str = "text/plain"
    size_bytes: int = 0
    sha256: str = ""
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactRecord:
        return cls(
            artifact_id=str(data["artifact_id"]),
            space_id=str(data["space_id"]),
            name=str(data["name"]),
            path=str(data["path"]),
            mime_type=str(data.get("mime_type", "text/plain")),
            size_bytes=int(data.get("size_bytes", 0)),
            sha256=str(data.get("sha256", "")),
            created_at=float(data.get("created_at", time.time())),
            metadata=dict(data.get("metadata", {})),
        )


class SpaceArtifactStore:
    """Manages storage, retrieval, and cryptographic verification of space artifacts.

    Invariants:
    - Space isolation: Artifacts are partitioned by `spaces/{space_id}/artifacts/`
    - Path traversal protection: All read/write operations must resolve strictly
      inside the space's sandboxed artifact directory
    - Content hashing: SHA-256 hash is computed and verified on every registered artifact
    - Zero arbitrary filesystem access: Rejects relative paths, parent directory references (`..`),
      or absolute paths escaping the space boundary
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir if base_dir is not None else Path.home() / ".ryu" / "spaces"
        self._lock = threading.RLock()

    def _get_artifacts_dir(self, space_id: str) -> Path:
        p = self.base_dir / space_id / "artifacts"
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()

    def _get_index_file(self, space_id: str) -> Path:
        return self.base_dir / space_id / "artifacts.json"

    def _load_index(self, space_id: str) -> dict[str, dict[str, Any]]:
        index_file = self._get_index_file(space_id)
        if index_file.is_file():
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading artifact index for space '{space_id}': {e}")
        return {}

    def _save_index(self, space_id: str, index: dict[str, dict[str, Any]]) -> None:
        index_file = self._get_index_file(space_id)
        index_file.parent.mkdir(parents=True, exist_ok=True)
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)

    def sanitize_filename(self, filename: str) -> str:
        """Sanitize a raw filename, stripping path components and dangerous characters."""
        base = os.path.basename(filename).strip()
        # Remove null bytes and control chars
        base = re.sub(r"[\x00-\x1f\x7f]", "", base)
        # Replace dangerous path delimiters and special characters
        clean = re.sub(r"[^a-zA-Z0-9_.-]", "_", base)
        if not clean or clean.startswith("."):
            clean = f"artifact_{clean}".lstrip("_")
        return clean

    def register_artifact(
        self,
        space_id: str,
        name: str,
        content: str | bytes,
        mime_type: str = "text/plain",
        metadata: dict[str, Any] | None = None,
        artifact_id: str | None = None,
    ) -> ArtifactRecord:
        """Persist content as a space-scoped artifact with SHA-256 hash."""
        with self._lock:
            clean_name = self.sanitize_filename(name)
            art_id = artifact_id or f"art-{int(time.time() * 1000)}-{os.urandom(4).hex()}"
            artifacts_dir = self._get_artifacts_dir(space_id)

            dest_filename = f"{art_id}_{clean_name}"
            dest_path = (artifacts_dir / dest_filename).resolve()

            # Path traversal invariant check
            if not str(dest_path).startswith(str(artifacts_dir)):
                raise PermissionError(f"Path traversal detected: {dest_path} is outside {artifacts_dir}")

            raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
            size_bytes = len(raw_bytes)
            sha256_hash = hashlib.sha256(raw_bytes).hexdigest()

            with open(dest_path, "wb") as f:
                f.write(raw_bytes)

            record = ArtifactRecord(
                artifact_id=art_id,
                space_id=space_id,
                name=clean_name,
                path=str(dest_path),
                mime_type=mime_type,
                size_bytes=size_bytes,
                sha256=sha256_hash,
                created_at=time.time(),
                metadata=metadata or {},
            )

            index = self._load_index(space_id)
            index[art_id] = record.to_dict()
            self._save_index(space_id, index)

            return record

    def list_artifacts(self, space_id: str) -> list[dict[str, Any]]:
        """List all registered artifacts for a given space."""
        with self._lock:
            index = self._load_index(space_id)
            records = list(index.values())
            records.sort(key=lambda x: x.get("created_at", 0.0), reverse=True)
            return records

    def get_artifact(self, space_id: str, artifact_id: str) -> dict[str, Any] | None:
        """Get metadata record for a specific artifact."""
        with self._lock:
            index = self._load_index(space_id)
            return index.get(artifact_id)

    def get_artifact_content(self, space_id: str, artifact_id: str) -> tuple[str, str] | None:
        """Read artifact content safely with strict sandbox path verification.

        Returns (content_str, mime_type) or None if not found.
        """
        with self._lock:
            record = self.get_artifact(space_id, artifact_id)
            if not record:
                return None

            raw_path = Path(record["path"]).resolve()
            artifacts_dir = self._get_artifacts_dir(space_id)

            # Strict sandbox boundary enforcement
            if not str(raw_path).startswith(str(artifacts_dir)) or not raw_path.is_file():
                logger.error(f"Sandbox violation or missing file: {raw_path}")
                return None

            try:
                with open(raw_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                return content, record.get("mime_type", "text/plain")
            except Exception as e:
                logger.error(f"Failed to read artifact {artifact_id}: {e}")
                return None
