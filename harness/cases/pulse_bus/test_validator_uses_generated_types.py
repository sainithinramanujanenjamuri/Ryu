import sys
from pathlib import Path

from ryu.pulse_bus.validator import PulseValidator


def test_validator_types_match_generated_module() -> None:
    # Make sure we can import it
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    gen_path = repo_root / "contracts" / "codegen" / "python"
    if str(gen_path) not in sys.path:
        sys.path.insert(0, str(gen_path))
    from generated.pulse_models import ALL_PULSE_TYPES

    v = PulseValidator()
    assert v.known_types() == frozenset(ALL_PULSE_TYPES)

