"""Channels Layer — Phase 0 Scaffold. spec §2 — Phase 8"""

import sys
from pathlib import Path

# Ensure core/pulse_bus/src and repository root are on sys.path
_repo_root = Path(__file__).resolve().parent.parent
_pulse_bus_src = _repo_root / "core" / "pulse_bus" / "src"
if str(_pulse_bus_src) not in sys.path:
    sys.path.insert(0, str(_pulse_bus_src))
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
