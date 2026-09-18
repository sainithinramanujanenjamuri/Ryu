#!/usr/bin/env bash
# scripts/dev_setup.sh
#
# RYU AI — Development Environment Check
# Verifies that required tools are available and reports their versions.
# Does NOT install anything automatically.

set -e

echo "======================================================================"
echo "RYU AI — Development Environment Check"
echo "======================================================================"
echo ""

# --------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------
echo "--- Python ---"
if command -v python3 &>/dev/null; then
  PY_VERSION=$(python3 --version 2>&1)
  echo "  Found: $PY_VERSION"
  PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
  PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
  if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]); then
    echo "  BLOCKER: Python >= 3.12 required. Found: $PY_VERSION"
    echo "  Install Python 3.12+ and ensure it is on PATH."
    PYTHON_OK=false
  else
    echo "  OK: $PY_VERSION"
    PYTHON_OK=true
  fi
elif command -v python &>/dev/null; then
  PY_VERSION=$(python --version 2>&1)
  echo "  Found: $PY_VERSION"
  echo "  WARNING: Using 'python' command. Verify this is Python 3.12+."
  PYTHON_OK=false
else
  echo "  BLOCKER: Python not found on PATH."
  PYTHON_OK=false
fi
echo ""

# --------------------------------------------------------------------------
# Cargo / Rust
# --------------------------------------------------------------------------
echo "--- Cargo (Rust) ---"
if command -v cargo &>/dev/null; then
  echo "  OK: $(cargo --version)"
else
  echo "  BLOCKER: cargo not found. Install Rust toolchain from https://rustup.rs/"
fi
echo ""

# --------------------------------------------------------------------------
# Ruff
# --------------------------------------------------------------------------
echo "--- Ruff ---"
if command -v ruff &>/dev/null; then
  echo "  OK: $(ruff --version)"
else
  echo "  MISSING: ruff not found. Install with: pip install ruff"
fi
echo ""

# --------------------------------------------------------------------------
# Pytest
# --------------------------------------------------------------------------
echo "--- Pytest ---"
if command -v pytest &>/dev/null; then
  echo "  OK: $(pytest --version 2>&1)"
else
  echo "  MISSING: pytest not found. Install with: pip install pytest"
fi
echo ""

# --------------------------------------------------------------------------
# jsonschema (required for pulse bus payload validation)
# --------------------------------------------------------------------------
echo "--- jsonschema ---"
if python3 -c "import jsonschema" 2>/dev/null || python -c "import jsonschema" 2>/dev/null; then
  echo "  OK: jsonschema available"
else
  echo "  MISSING: jsonschema not found. Install with: pip install jsonschema"
fi
echo ""

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
echo "======================================================================"
if [ "$PYTHON_OK" = false ]; then
  echo "ENVIRONMENT BLOCKER: Python 3.12+ is required but not available."
  echo "The project specification requires Python >= 3.12."
  echo "DO NOT weaken the project requirement to accommodate Python 3.11."
  exit 1
else
  echo "Environment check complete."
fi
echo "======================================================================"

