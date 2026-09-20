"""RYU AI CLI entrypoint for `python -m channels.cli`.

spec §2, §4, ROADMAP Phase 8 — Phase 8
"""

import sys
from pathlib import Path

# Ensure core/pulse_bus/src and repository root are on sys.path when invoked via CLI
_repo_root = Path(__file__).resolve().parent.parent.parent
_pulse_bus_src = _repo_root / "core" / "pulse_bus" / "src"
if str(_pulse_bus_src) not in sys.path:
    sys.path.insert(0, str(_pulse_bus_src))
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from channels.cli.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

