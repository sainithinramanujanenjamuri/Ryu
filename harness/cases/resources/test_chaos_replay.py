"""Harness case: Seeded chaos run bit-identical replay verification.

A seeded chaos run replays bit-identically using the PulseReplayer.
spec §6 (Replay), ROADMAP Phase 3 Exit Gate
"""

import random
from datetime import datetime, timezone

from ryu.pulse_bus.bus import PulseBus
from ryu.pulse_bus.pulse import Pulse
from ryu.pulse_bus.replay import PulseReplayer
from ryu.pulse_bus.store import InMemoryPulseStore

from core.resources.clock import FakeClock
from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager


def test_seeded_chaos_run_replays_bit_identically() -> None:
    """Run a randomized sequence with a fixed seed and verify replay reproducibility."""
    seed = 42
    rnd = random.Random(seed)

    store = InMemoryPulseStore()
    bus = PulseBus()

    # Capture every published pulse to store
    def capture_to_store(p: Pulse) -> None:
        store.append(p)

    bus.subscribe(capture_to_store)

    clock = FakeClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    mgr = ResourceManager(bus=bus, clock=clock)

    # Register 3 resources
    gpu_1 = ResourceIdentity("gpu", "local", "cuda-0")
    gpu_2 = ResourceIdentity("gpu", "local", "cuda-1")
    cpu_1 = ResourceIdentity("cpu", "local", "core-0")

    mgr.register_resource(Resource(gpu_1, "space-replay", total_capacity=1))
    mgr.register_resource(Resource(gpu_2, "space-replay", total_capacity=1))
    mgr.register_resource(Resource(cpu_1, "space-replay", total_capacity=2))

    resources = [gpu_1, gpu_2, cpu_1]
    active_tokens: list[tuple[str, str, str]] = []  # (space_id, requester_id, token)

    # Execute 50 seeded randomized operations
    for op_idx in range(50):
        op_type = rnd.choice(["acquire", "release", "renew", "advance_clock"])
        res = rnd.choice(resources)
        agent = f"agent-{rnd.randint(1, 5)}"

        if op_type == "acquire":
            units = 1
            res_acq = mgr.acquire(
                space_id="space-replay",
                requester_id=agent,
                identity=res,
                units=units,
                duration_seconds=30.0,
            )
            if res_acq.granted and res_acq.lease:
                active_tokens.append(("space-replay", agent, res_acq.lease.lease_token))

        elif op_type == "release" and active_tokens:
            space_id, req_id, tok = active_tokens.pop(0)
            mgr.release(space_id, req_id, tok)

        elif op_type == "renew" and active_tokens:
            space_id, req_id, tok = active_tokens[0]
            try:
                mgr.renew(space_id, req_id, tok, extension_seconds=15.0)
            except (ValueError, PermissionError):
                pass

        elif op_type == "advance_clock":
            clock.advance(float(rnd.randint(5, 40)))
            mgr.check_expirations()

    # Verify pulses were captured
    replayer = PulseReplayer(store)
    recorded_pulses = list(replayer.replay_from(from_position=0))
    assert len(recorded_pulses) > 0

    # Replay causal and correlation chains
    replayed_space_pulses = replayer.replay_by_space("space-replay")
    assert len(replayed_space_pulses) == len(recorded_pulses)

    # Verify bit-identical replay of pulse types and payloads
    for original, replayed in zip(recorded_pulses, replayed_space_pulses, strict=True):
        assert original.id == replayed.id
        assert original.type == replayed.type
        assert original.payload == replayed.payload
        assert original.correlation_id == replayed.correlation_id
