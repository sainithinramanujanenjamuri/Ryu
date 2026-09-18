"""Pulse validator: registry type checking and JSON Schema payload validation.

Loads contracts/registry/pulse-types.json and payload-schemas/*.json at
startup and validates every Pulse BEFORE it is appended to the bus.

Validation order per ROADMAP Phase 0 §24:
  1. Verify type exists in registry.
  2. Locate payload schema for that type.
  3. Validate payload against schema.
  4. Raise PulseRejectedError if any step fails (before append, never after).

spec §16 (Pulse Bus validator) — Phase 0
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ryu.pulse_bus.reject import PulseRejectedError

try:
    import jsonschema
    from jsonschema import ValidationError
    _JSONSCHEMA_AVAILABLE = True
except ImportError:  # pragma: no cover
    _JSONSCHEMA_AVAILABLE = False


def _find_contracts_root() -> Path:
    """Locate contracts/registry/ relative to this file's repository position."""
    # Climb up ancestors until contracts/registry is found
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "contracts" / "registry"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        "Cannot locate contracts/registry/ from validator.py — check repo structure."
    )


class PulseValidator:
    """
    Validates Pulse instances against the machine-readable contract registry.

    Loaded once; the registry and schemas are immutable after init.
    """

    def __init__(self, registry_root: Path | None = None) -> None:
        root = registry_root or _find_contracts_root()
        self._registry_file = root / "pulse-types.json"
        self._schemas_dir = root / "payload-schemas"
        self._known_types: set[str] = self._load_registry()
        self._schemas: dict[str, dict[str, Any]] = self._load_schemas()

    # ------------------------------------------------------------------
    # Internal loading
    # ------------------------------------------------------------------

    def _load_registry(self) -> set[str]:
        """Read pulse-types.json and return the set of registered type strings."""
        try:
            import sys
            repo_root = _find_contracts_root().parent.parent
            gen_path = repo_root / "contracts" / "codegen" / "python"
            if str(gen_path) not in sys.path:
                sys.path.insert(0, str(gen_path))
            from generated.pulse_models import ALL_PULSE_TYPES  # type: ignore[import-not-found]
            if ALL_PULSE_TYPES:
                return set(ALL_PULSE_TYPES)
        except Exception:
            pass

        with open(self._registry_file, "r", encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        types = {entry["type"] for entry in data.get("types", [])}
        if not types:
            raise RuntimeError("pulse-types.json loaded but contains no types.")
        return types

    def _load_schemas(self) -> dict[str, dict[str, Any]]:
        """Load all payload JSON Schemas from the payload-schemas/ directory."""
        schemas: dict[str, dict[str, Any]] = {}
        for json_file in self._schemas_dir.glob("*.json"):
            ptype = json_file.stem  # filename without .json = pulse type
            with open(json_file, "r", encoding="utf-8") as f:
                schemas[ptype] = json.load(f)
        return schemas

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def known_types(self) -> frozenset[str]:
        """Return the set of registered Pulse types (read-only)."""
        return frozenset(self._known_types)

    def validate_type(self, pulse_type: str) -> None:
        """
        Verify the pulse type exists in the registry.

        Raises:
            PulseRejectedError: if the type is not registered.
        """
        if pulse_type not in self._known_types:
            raise PulseRejectedError(
                reason="unknown_type",
                offending_type=pulse_type,
                details=(
                    f"Type {pulse_type!r} is not registered in pulse-types.json. "
                    f"Known namespaces: {sorted({t.split('.')[0] for t in self._known_types})}"
                ),
            )

    def validate_payload(self, pulse_type: str, payload: dict[str, Any]) -> None:
        """
        Validate the payload against the schema for the registered type.

        This must be called AFTER validate_type (assumes type is known).

        Raises:
            PulseRejectedError: if the payload is missing, malformed, or fails schema validation.
        """
        schema = self._schemas.get(pulse_type)
        if schema is None:
            raise PulseRejectedError(
                reason="missing_schema",
                offending_type=pulse_type,
                details=f"No payload schema file found for type {pulse_type!r}.",
            )

        if not _JSONSCHEMA_AVAILABLE:
            # Perform minimal structural check without jsonschema library
            required = schema.get("required", [])
            missing = [field for field in required if field not in payload]
            if missing:
                raise PulseRejectedError(
                    reason="invalid_payload",
                    offending_type=pulse_type,
                    details=f"Missing required fields: {missing}",
                )
            return

        # Full JSON Schema validation
        try:
            jsonschema.validate(instance=payload, schema=schema)
        except ValidationError as exc:
            raise PulseRejectedError(
                reason="invalid_payload",
                offending_type=pulse_type,
                details=str(exc.message),
            ) from exc

    def validate(self, pulse_type: str, payload: dict[str, Any]) -> None:
        """
        Validate type then payload in the required order.
        Stops at first failure (rejection before append).
        """
        self.validate_type(pulse_type)
        self.validate_payload(pulse_type, payload)

