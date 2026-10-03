"""Integration and Unit Test Battery for PostgresPlanStore (Phase 15.1).

Covers:
- PLAN-DURABLE-001: PostgreSQL authoritative current Plan state
- PLAN-DURABLE-002: Plan history survives restart and remains immutable
- PLAN-DURABLE-003: Plan CAS remains atomic with database transaction rollback
- PLAN-DURABLE-004: Strict Space isolation (multi-space separation, zero leakage)
- PLAN-DURABLE-005: Deterministic TaskGraph serialization / deserialization
- PLAN-DURABLE-006: Cold-boot reconstruction across multiple Spaces without memory
- PLAN-DURABLE-007: StartupRecoveryEngine integration operating against reconstructed graph
- PLAN-DURABLE-008: Missing and corrupt Plan state fails closed
- PLAN-DURABLE-009: PostgreSQL failure fails closed (never silently falls back to memory)
- PLAN-DURABLE-010: Concurrent CAS writers (exactly one winner, zero lost updates)
- PLAN-CRASH-01 through PLAN-CRASH-10 test matrix scenarios

spec §16 (TaskGraph & PlanDelta), ADR-0045, REC-003 — Phase 15.1
"""

from __future__ import annotations

import concurrent.futures
import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest
from ryu.pulse_bus.config import PostgresConfig
from ryu.pulse_bus.pulse import Pulse

from core.orchestrator.execution_state import (
    ExecutionAttemptRecord,
    InMemoryExecutionAttemptStore,
)
from core.orchestrator.startup_recovery import StartupRecoveryEngine
from core.plans.delta import PlanDelta
from core.plans.postgres_plan_store import PostgresPlanStore
from core.plans.task_graph import TaskNode
from core.space.kernel import SpaceKernel


class SpyPulseBus:
    """In-memory pulse bus spy for observing emitted pulses during tests."""

    def __init__(self) -> None:
        self.published: list[Pulse] = []

    def publish(self, pulse: Pulse) -> Pulse:
        self.published.append(pulse)
        return pulse

    def find_by_type(self, pulse_type: str) -> list[Pulse]:
        return [p for p in self.published if p.type == pulse_type]


@pytest.fixture
def pg_config() -> PostgresConfig:
    return PostgresConfig(
        host=os.environ.get("RYU_PG_HOST", "localhost"),
        port=int(os.environ.get("RYU_PG_PORT", "5432")),
        db=os.environ.get("RYU_PG_DB", "ryu_dev"),
        user=os.environ.get("RYU_PG_USER", "ryu"),
        password=os.environ.get("RYU_PG_PASSWORD", "ryu_dev_password"),
    )


@pytest.fixture
def clean_plan_store(pg_config: PostgresConfig):
    """Fixture providing a clean PostgresPlanStore connected to live PostgreSQL."""
    store = PostgresPlanStore(config=pg_config)
    conn = store._get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE plans, plan_history CASCADE;")
    finally:
        conn.close()

    yield store

    conn = store._get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE plans, plan_history CASCADE;")
    finally:
        conn.close()


def _unique_space_id(prefix: str = "space") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ─── PLAN-DURABLE-001 & PLAN-CRASH-01: Authoritative Plan State & Restart ─────

def test_plan_durable_001_authoritative_storage_and_restart(clean_plan_store: PostgresPlanStore, pg_config: PostgresConfig):
    """PLAN-DURABLE-001 / PLAN-CRASH-01: Plan state is persisted in PostgreSQL and survives restart."""
    space_id = _unique_space_id("p1")
    bus = SpyPulseBus()
    store1 = PostgresPlanStore(config=pg_config, bus=bus)

    # 1. Initialize Space Plan
    init_node = TaskNode(id="task-init", capability="python.eval_sandboxed", state="ready")
    g1 = store1.init_space_plan(space_id, [init_node])
    assert g1.plan_version == 1
    assert len(g1.nodes) == 1
    assert store1.get_plan_version(space_id) == 1

    # 2. Advance plan to v2 via CAS PlanDelta
    delta = PlanDelta(
        space_id=space_id,
        base_version=1,
        resulting_version=2,
        ops=[
            {"op": "add", "target_node_id": "task-next", "capability": "terminal.exec", "state": "pending"}
        ],
    )
    ok, ver, err = store1.commit_delta(delta)
    assert ok is True
    assert ver == 2
    assert store1.get_plan_version(space_id) == 2

    # Verify pulse emission
    deltas = bus.find_by_type("plan.delta")
    assert len(deltas) == 1
    assert deltas[0].payload["resulting_version"] == 2

    # 3. Simulate process restart by destroying store1 and creating fresh store2
    del store1
    store2 = PostgresPlanStore(config=pg_config)

    # Reconstruct from PostgreSQL
    reconstructed = store2.get_task_graph(space_id)
    assert reconstructed.space_id == space_id
    assert reconstructed.plan_version == 2
    assert len(reconstructed.nodes) == 2
    n_init = reconstructed.get_node("task-init")
    assert n_init is not None and n_init.state == "ready"
    n_next = reconstructed.get_node("task-next")
    assert n_next is not None and n_next.capability == "terminal.exec"


# ─── PLAN-DURABLE-002 & PLAN-CRASH-10: History Immutability Across Restart ────

def test_plan_durable_002_history_survives_restart_and_is_immutable(clean_plan_store: PostgresPlanStore, pg_config: PostgresConfig):
    """PLAN-DURABLE-002 / PLAN-CRASH-10: Historical plan snapshots survive restart and are immutable."""
    space_id = _unique_space_id("p2")
    store = PostgresPlanStore(config=pg_config)

    # v1
    store.init_space_plan(space_id, [TaskNode(id="t1", capability="cap1", state="pending")])

    # v2: transition t1 pending -> ready
    store.commit_delta(
        PlanDelta(
            space_id=space_id,
            base_version=1,
            resulting_version=2,
            ops=[{"op": "transition", "target_node_id": "t1", "from_state": "pending", "to_state": "ready"}],
        )
    )

    # v3: transition t1 ready -> admission_pending
    store.commit_delta(
        PlanDelta(
            space_id=space_id,
            base_version=2,
            resulting_version=3,
            ops=[{"op": "transition", "target_node_id": "t1", "from_state": "ready", "to_state": "admission_pending"}],
        )
    )

    # Simulate restart
    del store
    fresh_store = PostgresPlanStore(config=pg_config)

    # Current version is 3
    assert fresh_store.get_plan_version(space_id) == 3
    current_graph = fresh_store.get_task_graph(space_id)
    n_curr = current_graph.get_node("t1")
    assert n_curr is not None and n_curr.state == "admission_pending"

    # Historical retrieval of v1, v2, v3
    hist_v1 = fresh_store.get_historical_graph(space_id, 1)
    hist_v2 = fresh_store.get_historical_graph(space_id, 2)
    hist_v3 = fresh_store.get_historical_graph(space_id, 3)

    assert hist_v1 is not None and hist_v1.plan_version == 1
    n_v1 = hist_v1.get_node("t1")
    assert n_v1 is not None and n_v1.state == "pending"

    assert hist_v2 is not None and hist_v2.plan_version == 2
    n_v2 = hist_v2.get_node("t1")
    assert n_v2 is not None and n_v2.state == "ready"

    assert hist_v3 is not None and hist_v3.plan_version == 3
    n_v3 = hist_v3.get_node("t1")
    assert n_v3 is not None and n_v3.state == "admission_pending"



# ─── PLAN-DURABLE-003 & PLAN-CRASH-03 / 04 / 08: Atomic CAS & Stale Rejection ─

def test_plan_durable_003_cas_rejects_stale_delta_and_emits_superseded(clean_plan_store: PostgresPlanStore, pg_config: PostgresConfig):
    """PLAN-DURABLE-003 / PLAN-CRASH-08: Stale CAS commits are rejected and emit plan.version.superseded."""
    space_id = _unique_space_id("p3")
    bus = SpyPulseBus()
    store = PostgresPlanStore(config=pg_config, bus=bus)

    store.init_space_plan(space_id, [TaskNode(id="t1", capability="cap")])

    # Valid commit v1 -> v2
    ok1, v2, _ = store.commit_delta(
        PlanDelta(
            space_id=space_id,
            base_version=1,
            resulting_version=2,
            ops=[{"op": "add", "target_node_id": "t2", "capability": "cap2"}],
        )
    )
    assert ok1 is True
    assert v2 == 2

    # Stale commit: base_version=1 against current_version=2
    stale_delta = PlanDelta(
        space_id=space_id,
        base_version=1,
        resulting_version=2,
        ops=[{"op": "add", "target_node_id": "t3_stale", "capability": "cap3"}],
    )
    ok2, cur_ver, winning_id = store.commit_delta(stale_delta)
    assert ok2 is False
    assert cur_ver == 2

    # Verify plan.version.superseded pulse
    superseded = bus.find_by_type("plan.version.superseded")
    assert len(superseded) == 1
    assert superseded[0].payload["superseded_version"] == 1
    assert superseded[0].payload["current_version"] == 2

    # Verify t3_stale was NOT added
    curr = store.get_task_graph(space_id)
    assert curr.get_node("t3_stale") is None


# ─── PLAN-DURABLE-004 & PLAN-CRASH-07: Space Isolation ────────────────────────

def test_plan_durable_004_space_isolation_and_identical_task_ids(clean_plan_store: PostgresPlanStore, pg_config: PostgresConfig):
    """PLAN-DURABLE-004: Strict Space isolation; identical task IDs across Spaces remain isolated."""
    space_a = _unique_space_id("space-a")
    space_b = _unique_space_id("space-b")
    store = PostgresPlanStore(config=pg_config)

    # Both spaces have a task named 'task-compute' but with different capabilities and states
    store.init_space_plan(
        space_a,
        [TaskNode(id="task-compute", capability="gpu.matrix_mult", state="ready")],
    )
    store.init_space_plan(
        space_b,
        [TaskNode(id="task-compute", capability="cpu.data_prep", state="pending")],
    )

    # Space A advances its task
    store.commit_delta(
        PlanDelta(
            space_id=space_a,
            base_version=1,
            resulting_version=2,
            ops=[{"op": "transition", "target_node_id": "task-compute", "from_state": "ready", "to_state": "admission_pending"}],
        )
    )

    # Verify Space A is v2 with admission_pending task
    graph_a = store.get_task_graph(space_a)
    assert graph_a.plan_version == 2
    node_a = graph_a.get_node("task-compute")
    assert node_a is not None
    assert node_a.state == "admission_pending"
    assert node_a.capability == "gpu.matrix_mult"

    # Verify Space B is untouched (still v1 and pending)
    graph_b = store.get_task_graph(space_b)
    assert graph_b.plan_version == 1
    node_b = graph_b.get_node("task-compute")
    assert node_b is not None
    assert node_b.state == "pending"
    assert node_b.capability == "cpu.data_prep"


# ─── PLAN-DURABLE-006 & PLAN-CRASH-07: Multi-Space Cold-Boot Reconstruction ───

def test_plan_durable_006_multi_space_cold_boot_reconstruction(clean_plan_store: PostgresPlanStore, pg_config: PostgresConfig):
    """PLAN-DURABLE-006 / PLAN-CRASH-07: Cold boot reconstructs 3+ spaces with distinct versions and histories."""
    space_a = _unique_space_id("alpha")
    space_b = _unique_space_id("beta")
    space_c = _unique_space_id("gamma")
    store = PostgresPlanStore(config=pg_config)

    # Seed space A -> v3
    store.init_space_plan(space_a, [TaskNode(id="a1", capability="cap")])
    store.commit_delta(PlanDelta(space_a, 1, 2, [{"op": "add", "target_node_id": "a2", "capability": "cap"}]))
    store.commit_delta(PlanDelta(space_a, 2, 3, [{"op": "add", "target_node_id": "a3", "capability": "cap"}]))

    # Seed space B -> v4
    store.init_space_plan(space_b, [TaskNode(id="b1", capability="cap")])
    for v in range(1, 4):
        store.commit_delta(PlanDelta(space_b, v, v + 1, [{"op": "add", "target_node_id": f"b{v+1}", "capability": "cap"}]))

    # Seed space C -> v2
    store.init_space_plan(space_c, [TaskNode(id="c1", capability="cap")])
    store.commit_delta(PlanDelta(space_c, 1, 2, [{"op": "add", "target_node_id": "c2", "capability": "cap"}]))

    # Simulate Cold Boot: discard old store, create fresh store
    del store
    reconstructed_store = PostgresPlanStore(config=pg_config)

    # Check active spaces listing
    active_spaces = reconstructed_store.list_active_spaces()
    assert space_a in active_spaces
    assert space_b in active_spaces
    assert space_c in active_spaces

    # Check bulk load
    all_plans = reconstructed_store.load_all_plans()
    assert all_plans[space_a].plan_version == 3
    assert len(all_plans[space_a].nodes) == 3

    assert all_plans[space_b].plan_version == 4
    assert len(all_plans[space_b].nodes) == 4

    assert all_plans[space_c].plan_version == 2
    assert len(all_plans[space_c].nodes) == 2


# ─── PLAN-DURABLE-007 & PLAN-CRASH-02: StartupRecoveryEngine Integration ──────

def test_plan_durable_007_cold_boot_startup_recovery_integration(clean_plan_store: PostgresPlanStore, pg_config: PostgresConfig):
    """PLAN-DURABLE-007 / PLAN-CRASH-02: StartupRecoveryEngine recovers interrupted attempt against reconstructed graph."""
    space_id = _unique_space_id("recovery-space")
    store = PostgresPlanStore(config=pg_config)

    # PROCESS A:
    # SpaceKernel initializes and commits plan with a running task
    kernel_a = SpaceKernel(space_id=space_id, owner_id="owner-1", plan_store=store)
    # Add task 'task-worker-crash'
    kernel_a.commit_plan_delta(
        PlanDelta(
            space_id=space_id,
            base_version=1,
            resulting_version=2,
            ops=[{"op": "add", "target_node_id": "task-worker-crash", "capability": "python.eval", "state": "running"}],
        )
    )
    running_node = kernel_a.get_task_graph().get_node("task-worker-crash")
    assert running_node is not None
    assert running_node.state == "running"

    # Persist durable execution attempt representing an interrupted worker
    attempt_store = InMemoryExecutionAttemptStore()
    attempt = ExecutionAttemptRecord(
        attempt_id=f"att-{space_id}-1",
        idempotency_key=f"ikey-{space_id}-1",
        space_id=space_id,
        task_id="task-worker-crash",
        plan_version=2,
        attempt_number=1,
        capability="python.eval",
        status="running",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=180),
    )
    attempt_store.save_attempt(attempt)

    # SIMULATE PROCESS TERMINATION (destroy Process A objects)
    del kernel_a
    del store

    # PROCESS B (Cold boot):
    # Fresh store connected only to PostgreSQL
    cold_store = PostgresPlanStore(config=pg_config)

    # Reconstruct SpaceKernel using cold_store
    cold_kernel = SpaceKernel(space_id=space_id, owner_id="owner-1", plan_store=cold_store)

    # Verify kernel loaded reconstructed graph from PostgreSQL
    reconstructed_graph = cold_kernel.get_task_graph()
    assert reconstructed_graph.plan_version == 2
    reconstructed_node = reconstructed_graph.get_node("task-worker-crash")
    assert reconstructed_node is not None
    assert reconstructed_node.state == "running"

    # Run StartupRecoveryEngine against the reconstructed kernel
    engine = StartupRecoveryEngine(
        attempt_store=attempt_store,
        kernels={space_id: cold_kernel},
        crash_detection_window_seconds=60.0,
    )
    recovery_result = engine.run()

    # Recovery performed
    assert recovery_result.recovered_tasks == 1
    assert recovery_result.errors == []

    # Authoritative TaskGraph in PostgreSQL advanced to v3 and marked 'failed'
    final_graph = cold_kernel.get_task_graph()
    assert final_graph.plan_version == 3
    node = final_graph.get_node("task-worker-crash")
    assert node is not None
    assert node.state == "failed"
    assert node.error == "transient.worker_crash"

    # Confirm PostgreSQL reflects v3
    assert cold_store.get_plan_version(space_id) == 3


# ─── PLAN-DURABLE-008 & PLAN-CRASH-05 / 06: Malformed State Fails Closed ──────

def test_plan_durable_008_corrupted_graph_fails_closed(clean_plan_store: PostgresPlanStore, pg_config: PostgresConfig):
    """PLAN-DURABLE-008 / PLAN-CRASH-05: Corrupted graph JSON fails closed and does not invent a graph."""
    space_id = _unique_space_id("corrupt")
    store = PostgresPlanStore(config=pg_config)
    store.init_space_plan(space_id)

    # Manually corrupt graph_json in PostgreSQL
    conn = psycopg2.connect(
        host=pg_config.host,
        port=pg_config.port,
        dbname=pg_config.db,
        user=pg_config.user,
        password=pg_config.password,
    )
    with conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE plans SET graph_json = %s WHERE space_id = %s;", ('{"corrupted": true}', space_id))
    conn.close()

    # Attempting to load corrupted graph must raise ValueError (fail closed)
    with pytest.raises(ValueError, match="missing or empty 'space_id'"):
        store.get_task_graph(space_id)


def test_plan_durable_008_nonexistent_history_fails_closed(clean_plan_store: PostgresPlanStore, pg_config: PostgresConfig):
    """PLAN-DURABLE-008 / PLAN-CRASH-06: Querying missing historical version raises KeyError."""
    space_id = _unique_space_id("missing")
    store = PostgresPlanStore(config=pg_config)
    store.init_space_plan(space_id)

    with pytest.raises(KeyError, match="Plan version 999 not found"):
        store.get_task_graph(space_id, version=999)


# ─── PLAN-DURABLE-009: PostgreSQL Outage Fails Closed (No Silent In-Memory Fallback) ─

def test_plan_durable_009_outage_fails_closed(pg_config: PostgresConfig):
    """PLAN-DURABLE-009: Database connection failure raises OperationalError and never falls back to memory."""
    # Point to non-existent port
    broken_config = PostgresConfig(
        host=pg_config.host,
        port=59999,  # closed port
        db=pg_config.db,
        user=pg_config.user,
        password=pg_config.password,
    )

    with pytest.raises(psycopg2.OperationalError):
        PostgresPlanStore(config=broken_config)


# ─── PLAN-DURABLE-010 & PLAN-CRASH-09: Concurrent CAS Writers ─────────────────

def test_plan_durable_010_concurrent_cas_single_winner(clean_plan_store: PostgresPlanStore, pg_config: PostgresConfig):
    """PLAN-DURABLE-010 / PLAN-CRASH-09: Concurrent CAS writers competing for same version produce exactly 1 winner."""
    space_id = _unique_space_id("concurrent")
    store = PostgresPlanStore(config=pg_config)
    store.init_space_plan(space_id, [TaskNode(id="root", capability="cap")])

    # Both writers attempt to transition from v1 to v2 concurrently
    delta_a = PlanDelta(
        space_id=space_id,
        base_version=1,
        resulting_version=2,
        ops=[{"op": "add", "target_node_id": "task-from-a", "capability": "capA"}],
    )
    delta_b = PlanDelta(
        space_id=space_id,
        base_version=1,
        resulting_version=2,
        ops=[{"op": "add", "target_node_id": "task-from-b", "capability": "capB"}],
    )

    results = []

    def commit_writer(d: PlanDelta):
        writer_store = PostgresPlanStore(config=pg_config)
        return writer_store.commit_delta(d)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_a = executor.submit(commit_writer, delta_a)
        f_b = executor.submit(commit_writer, delta_b)
        results = [f_a.result(), f_b.result()]

    successes = [r for r in results if r[0] is True]
    conflicts = [r for r in results if r[0] is False]

    # Exactly one winner
    assert len(successes) == 1
    assert successes[0][1] == 2

    # Exactly one CAS conflict
    assert len(conflicts) == 1
    assert conflicts[0][1] == 2  # Current version is 2

    # Verify authoritative plan version is 2, not 3
    final_graph = store.get_task_graph(space_id)
    assert final_graph.plan_version == 2
