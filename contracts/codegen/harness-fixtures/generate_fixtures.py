#!/usr/bin/env python3
"""
Real code generator: Reads contracts/registry/pulse-types.json and payload schemas,
and generates valid and invalid test vector fixtures for harness verification.
"""

from __future__ import annotations

import json
from pathlib import Path


def generate_fixtures() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    registry_file = repo_root / "contracts" / "registry" / "pulse-types.json"
    schemas_dir = repo_root / "contracts" / "registry" / "payload-schemas"
    output_dir = repo_root / "harness" / "fixtures"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "pulse_vectors.json"

    with open(registry_file, "r", encoding="utf-8") as f:
        registry_data = json.load(f)

    types = registry_data.get("types", [])

    valid_vectors = []
    invalid_vectors = []

    for item in types:
        ptype = item["type"]
        schema_path = schemas_dir / f"{ptype}.json"
        if schema_path.exists():
            with open(schema_path, "r", encoding="utf-8") as sf:
                sdata = json.load(sf)
            required_fields = sdata.get("required", [])
            properties = sdata.get("properties", {})

            # Build minimal valid payload
            valid_payload = {}
            for field in required_fields:
                fmeta = properties.get(field, {})
                ftype = fmeta.get("type")
                if "enum" in fmeta:
                    valid_payload[field] = fmeta["enum"][0]
                elif ftype == "string":
                    if fmeta.get("format") == "date-time":
                        valid_payload[field] = "2026-09-18T12:00:00Z"
                    elif fmeta.get("pattern"):
                        valid_payload[field] = "transient.timeout"
                    else:
                        valid_payload[field] = f"sample_{field}"
                elif ftype == "integer":
                    valid_payload[field] = 1
                elif ftype == "number":
                    valid_payload[field] = 100.0
                elif ftype == "boolean":
                    valid_payload[field] = True
                elif ftype == "array":
                    valid_payload[field] = ["ref_1"]
                elif ftype == "object":
                    valid_payload[field] = {"key": "value"}

            valid_vectors.append({
                "type": ptype,
                "valid": True,
                "payload": valid_payload
            })

            # Build invalid payload (missing required field)
            if required_fields:
                missing_field = required_fields[0]
                broken_payload = {k: v for k, v in valid_payload.items() if k != missing_field}
                invalid_vectors.append({
                    "type": ptype,
                    "valid": False,
                    "error_reason": f"missing_field_{missing_field}",
                    "payload": broken_payload
                })

    data = {
        "generated_at": "2026-09-18T12:00:00Z",
        "description": "Deterministic test vectors derived from contracts/registry",
        "valid_vectors": valid_vectors,
        "invalid_vectors": invalid_vectors,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(
        f"[fixture-codegen] Generated {len(valid_vectors)} valid and "
        f"{len(invalid_vectors)} invalid vectors in {output_file}"
    )
    return output_file


if __name__ == "__main__":
    generate_fixtures()

