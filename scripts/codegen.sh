#!/usr/bin/env bash
# scripts/codegen.sh
#
# RYU AI — Contract Code Generators
# Invokes all Phase 0 code generators in sequence.
# Fails immediately if any generator fails.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "======================================================================"
echo "RYU AI — Contract Code Generation"
echo "======================================================================"
echo "Repo root: $REPO_ROOT"
echo ""

PYTHON=python
if command -v python3 &>/dev/null; then
  PYTHON=python3
fi

# --------------------------------------------------------------------------
# 1. Python pulse models
# --------------------------------------------------------------------------
echo "--- [1/3] Generating Python pulse models ---"
$PYTHON "$REPO_ROOT/contracts/codegen/python/generate_pulse_models.py"
echo ""

# --------------------------------------------------------------------------
# 2. Rust proto types
# --------------------------------------------------------------------------
echo "--- [2/3] Generating Rust proto types ---"
$PYTHON "$REPO_ROOT/contracts/codegen/rust/generate_rust_proto.py"
echo ""

# --------------------------------------------------------------------------
# 3. Harness fixtures
# --------------------------------------------------------------------------
echo "--- [3/3] Generating harness fixture vectors ---"
$PYTHON "$REPO_ROOT/contracts/codegen/harness-fixtures/generate_fixtures.py"
echo ""

echo "======================================================================"
echo "Code generation complete."
echo "======================================================================"

