"""Tests for Phase 12.3: Admission Control & Fractional Lease Pipeline Integration.

Authoritative Specification: docs/PHASE_12_EXECUTION_ENGINE_SPEC.md
Architectural Decision: adr/0041-autonomous-task-dispatcher-dag-traversal-and-plan-convergence-engine.md
SCCA Laws:
    - Law 1: Everything Happens Inside a Space
    - Law 2: Capabilities Are Requested, Never Owned
    - Law 6: Failures Are Contained, Escalated, and Never Silent
Core Boundary: AGENTS.md §7 (Deterministic Core Independence)
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Any

import pytest
from ryu.pulse_bus.pulse import Pulse

from core.orchestrator.dispatch_model import (
    DeterministicDispatcher,
    TaskPipelineResult,
)
from core.plans.delta import PlanDelta
from core.plans.task_graph import (
    TaskNotFoundError,
    TaskState,
)
from core.resources.clock import FakeClock
from core.resources.identity import Resource, ResourceIdentity
from core.resources.manager import ResourceManager
from core.space.approver import ApprovalRequest
from core.space.kernel import SpaceKernel


class SpyPulseBus:
    """In-memory pulse bus capturing all published pulses for audit verification."""

    def __init__(self) -> None:
        self.published: list[Pulse] = []
        self._lock = threading.Lock()

    def publish(self, pulse: Pulse) -> Pulse:
        with self._lock:
            self.published.append(pulse)
        return pulse

    def find_by_type(self, pulse_type: str) -> list[Pulse]:
        with self._lock:
            return [p for p in self.published if p.type == pulse_type]


def _setup_env(
    space_id: str = "space-alpha",
    owner_id: str = "owner-alpha",
    budget: float = 100.0,
    policy: str = "hard_stop",
    clock: FakeClock | None = None,
) -> tuple[SpyPulseBus, SpaceKernel, ResourceManager, DeterministicDispatcher]:
    """Helper fixture initializing the isolated SCCA Space environment."""
    bus = SpyPulseBus()
    kernel = SpaceKernel(
        space_id=space_id,
        owner_id=owner_id,
        bus=bus,
        budget=budget,
        budget_policy=policy,
    )
    res_mgr = ResourceManager(bus=bus, clock=clock)
    dispatcher = DeterministicDispatcher()
    return bus, kernel, res_mgr, dispatcher


def _add_task_to_kernel(
    kernel: SpaceKernel,
    task_id: str,
    capability: str = "compute.cpu",
    state: str = "ready",
    dependencies: list[str] | None = None,
    params: dict[str, Any] | None = None,
) -> int:
    """Helper to commit an initial task to the kernel PlanStore via PlanDelta CAS."""
    cur_ver = kernel.get_plan_version()
    delta = PlanDelta(
        space_id=kernel.space_id,
        base_version=cur_ver,
        resulting_version=cur_ver + 1,
        ops=[
            {
                "op": "add",
                "target_node_id": task_id,
                "capability": capability,
                "state": state,
                "dependencies": dependencies or [],
                "params": params or {},
            }
        ],
    )
    ok, new_ver, err = kernel.commit_plan_delta(delta)
    assert ok, f"Failed to initialize task: {err}"
    return new_ver


# ==============================================================================
# 1. ADMISSION SUCCESS & LIFECYCLE
# ==============================================================================

def test_admission_success_lifecycle() -> None:
    """Scenario 1: Task in READY transitions READY -> ADMISSION_PENDING -> ADMITTED."""
    _, kernel, _, dispatcher = _setup_env(budget=50.0)
    _add_task_to_kernel(kernel, "task-1", state="ready")

    admitted, resp, pver = dispatcher.request_task_admission(
        kernel=kernel,
        task_id="task-1",
    )

    assert admitted is True
    assert resp.status == "ok"
    graph = kernel.get_task_graph()
    node = graph.get_node("task-1")
    assert node is not None
    assert node.state == TaskState.ADMITTED.value
    # Plan version incremented by 2 (ready -> admission_pending -> admitted)
    assert pver == kernel.get_plan_version()


def test_admission_from_pending_with_satisfied_dependencies() -> None:
    """Task in PENDING with satisfied dependencies transitions PENDING -> READY -> ADMISSION_PENDING -> ADMITTED."""
    _, kernel, _, dispatcher = _setup_env(budget=50.0)
    _add_task_to_kernel(kernel, "task-parent", state="completed")
    _add_task_to_kernel(
        kernel,
        "task-child",
        state="pending",
        dependencies=["task-parent"],
    )

    admitted, resp, _ = dispatcher.request_task_admission(
        kernel=kernel,
        task_id="task-child",
    )

    assert admitted is True
    assert resp.status == "ok"
    graph = kernel.get_task_graph()
    child_node = graph.get_node("task-child")
    assert child_node is not None
    assert child_node.state == TaskState.ADMITTED.value


def test_admission_denied_when_dependencies_unsatisfied() -> None:
    """Task in PENDING with unsatisfied dependencies is rejected and remains PENDING."""
    _, kernel, _, dispatcher = _setup_env(budget=50.0)
    _add_task_to_kernel(kernel, "task-parent", state="running")
    _add_task_to_kernel(
        kernel,
        "task-child",
        state="pending",
        dependencies=["task-parent"],
    )

    admitted, resp, _ = dispatcher.request_task_admission(
        kernel=kernel,
        task_id="task-child",
    )

    assert admitted is False
    assert resp.error == "unresolved_dependency: task-parent"
    graph = kernel.get_task_graph()
    child_node = graph.get_node("task-child")
    assert child_node is not None
    assert child_node.state == TaskState.PENDING.value


# ==============================================================================
# 2. ADMISSION DENIAL & BUDGET EXHAUSTION
# ==============================================================================

def test_admission_denial_budget_exhausted() -> None:
    """Scenario 2: Admission denial (budget_exhausted -> BLOCKED with space.budget.exceeded pulse)."""
    bus, kernel, _, dispatcher = _setup_env(budget=0.0, policy="hard_stop")
    _add_task_to_kernel(kernel, "task-budget-fail", state="ready")

    admitted, resp, _ = dispatcher.request_task_admission(
        kernel=kernel,
        task_id="task-budget-fail",
    )

    assert admitted is False
    assert resp.status == "denied"
    assert resp.error == "budget_exhausted"

    graph = kernel.get_task_graph()
    node = graph.get_node("task-budget-fail")
    assert node is not None
    assert node.state == TaskState.BLOCKED.value
    assert node.error == "budget_exhausted"

    # Verify typed pulse emitted per SCCA Law 6
    pulses = bus.find_by_type("space.budget.exceeded")
    assert len(pulses) >= 1
    assert pulses[0].space_id == kernel.space_id
    assert pulses[0].payload["policy_mode"] == "hard_stop"


# ==============================================================================
# 3. HUMAN APPROVAL REQUIRED & BLOCKING
# ==============================================================================

def test_admission_human_approval_required_blocks_task() -> None:
    """Scenario 3: Human approval required -> BLOCKED without approval."""
    _, kernel, _, dispatcher = _setup_env(budget=100.0)
    # High risk node capability requires approval gate
    _add_task_to_kernel(kernel, "task-gated", capability="node.hardware.grant", state="ready")

    admitted, resp, _ = dispatcher.request_task_admission(
        kernel=kernel,
        task_id="task-gated",
        approval=None,
    )

    assert admitted is False
    assert resp.error == "approval_required"

    graph = kernel.get_task_graph()
    node = graph.get_node("task-gated")
    assert node is not None
    assert node.state == TaskState.BLOCKED.value
    assert node.error == "approval_required"


# ==============================================================================
# 4. APPROVAL VERIFICATION & UNBLOCKING
# ==============================================================================

def test_approval_verification_and_unblocking() -> None:
    """Scenario 4: Valid approval unblocks task; consumed atomically; fake/stale rejected."""
    _, kernel, _, dispatcher = _setup_env(budget=100.0)
    _add_task_to_kernel(kernel, "task-gated", capability="node.hardware.grant", state="ready")

    # Step 1: Block task initially
    dispatcher.request_task_admission(kernel=kernel, task_id="task-gated", approval=None)
    assert kernel.get_task_graph().get_node("task-gated").state == TaskState.BLOCKED.value  # type: ignore

    # Step 2: Unblock with valid approval
    current_ver = kernel.get_plan_version()
    valid_approval = ApprovalRequest(
        request_id="app-100",
        space_id=kernel.space_id,
        capability="node.hardware.grant",
        approver_id="human-operator-1",
        status="approved",
        plan_version=current_ver + 1,  # Matches plan version after BLOCKED -> ADMISSION_PENDING transition
    )

    admitted, resp, _ = dispatcher.request_task_admission(
        kernel=kernel,
        task_id="task-gated",
        approval=valid_approval,
    )

    assert admitted is True
    assert resp.status == "ok"
    assert kernel.get_task_graph().get_node("task-gated").state == TaskState.ADMITTED.value  # type: ignore
    assert valid_approval.status == "consumed"
    assert valid_approval.consumed_at is not None


def test_rejected_approvals_consumed_stale_and_unapproved() -> None:
    """Scenario 4: Rejection of unapproved, already-consumed, and plan-version-mismatched approvals."""
    _, kernel, _, dispatcher = _setup_env(budget=100.0)
    _add_task_to_kernel(kernel, "task-unapproved", capability="node.gated", state="ready")

    # A: Status is still 'pending'
    pending_app = ApprovalRequest(
        request_id="app-pending",
        space_id=kernel.space_id,
        capability="node.gated",
        approver_id="human-1",
        status="pending",
    )
    ok, resp, _ = dispatcher.request_task_admission(kernel, "task-unapproved", approval=pending_app)
    assert not ok
    assert resp.error == "approval_pending"

    # Reset task back to ready
    kernel.propose_task_transition("task-unapproved", "ready", kernel.get_plan_version(), from_state="blocked")

    # B: Already consumed
    consumed_app = ApprovalRequest(
        request_id="app-consumed",
        space_id=kernel.space_id,
        capability="node.gated",
        approver_id="human-1",
        status="approved",
        consumed_at=time.time() - 10.0,
    )
    ok, resp, _ = dispatcher.request_task_admission(kernel, "task-unapproved", approval=consumed_app)
    assert not ok
    assert resp.error == "approval_already_consumed"

    # Reset task back to ready
    kernel.propose_task_transition("task-unapproved", "ready", kernel.get_plan_version(), from_state="blocked")

    # C: Stale plan version
    stale_app = ApprovalRequest(
        request_id="app-stale",
        space_id=kernel.space_id,
        capability="node.gated",
        approver_id="human-1",
        status="approved",
        plan_version=9999,
    )
    ok, resp, _ = dispatcher.request_task_admission(kernel, "task-unapproved", approval=stale_app)
    assert not ok
    assert resp.error == "approval_plan_version_mismatch"


# ==============================================================================
# 5. HARD-STOP BUDGET ENFORCEMENT & SINGLE ESCALATION WINDOW
# ==============================================================================

def test_hard_stop_budget_enforcement_and_single_escalation_pulse() -> None:
    """Scenario 5: 0 leases granted when budget exhausted; exactly 1 escalation pulse per window."""
    bus, kernel, res_mgr, dispatcher = _setup_env(budget=0.0, policy="hard_stop")
    ident = ResourceIdentity("compute", "host-1", "core-0")
    res_mgr.register_resource(Resource(identity=ident, space_id=kernel.space_id, total_capacity=4))

    _add_task_to_kernel(kernel, "task-zero-budget", state="ready")

    # Execute coordinated pipeline
    result1 = dispatcher.coordinate_admission_and_lease(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id="task-zero-budget",
        resource_identity=ident,
    )
    assert result1.admitted is False
    assert result1.leased is False
    assert result1.lease is None
    assert result1.terminal_state == TaskState.BLOCKED.value

    # Repeat call in same escalation window
    result2 = dispatcher.coordinate_admission_and_lease(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id="task-zero-budget",
        resource_identity=ident,
    )
    assert result2.admitted is False
    assert result2.leased is False

    # Invariant: exactly 1 escalation pulse emitted per escalation window
    pulses = bus.find_by_type("space.budget.exceeded")
    assert len(pulses) == 1


# ==============================================================================
# 6. RESOURCE LEASE SUCCESS
# ==============================================================================

def test_resource_lease_success() -> None:
    """Scenario 6: ADMITTED -> LEASE_PENDING -> LEASED with token and pulse."""
    bus, kernel, res_mgr, dispatcher = _setup_env(budget=100.0)
    ident = ResourceIdentity("gpu", "node-1", "cuda-0")
    res = Resource(identity=ident, space_id=kernel.space_id, total_capacity=2)
    res_mgr.register_resource(res)

    _add_task_to_kernel(kernel, "task-compute", state="admitted")

    leased, acq, pver = dispatcher.acquire_task_lease(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id="task-compute",
        identity=ident,
        units=1,
    )

    assert leased is True
    assert acq.granted is True
    assert acq.lease is not None
    assert pver == kernel.get_plan_version()

    graph = kernel.get_task_graph()
    node = graph.get_node("task-compute")
    assert node is not None
    assert node.state == TaskState.LEASED.value
    assert node.result_ref == acq.lease.lease_token

    # Verify resource.granted pulse was published
    granted_pulses = bus.find_by_type("resource.granted")
    assert len(granted_pulses) >= 1
    assert granted_pulses[0].payload["lease_token"] == acq.lease.lease_token


# ==============================================================================
# 7. FRACTIONAL LEASE ALLOCATION & CAPACITY DEDUCTION
# ==============================================================================

def test_fractional_lease_allocation_and_capacity_deduction() -> None:
    """Scenario 7: Fractional resource capacity deduction and release accounting."""
    _, kernel, res_mgr, dispatcher = _setup_env()
    ident = ResourceIdentity("ram", "host-1", "bank-0")
    res = Resource(identity=ident, space_id=kernel.space_id, total_capacity=10)
    res_mgr.register_resource(res)

    _add_task_to_kernel(kernel, "task-1", state="admitted")
    _add_task_to_kernel(kernel, "task-2", state="admitted")

    # Allocate 4 units to task-1
    ok1, acq1, _ = dispatcher.acquire_task_lease(kernel, res_mgr, "task-1", ident, units=4)
    assert ok1 is True
    assert res.available_capacity == 6

    # Allocate 3 units to task-2
    ok2, acq2, _ = dispatcher.acquire_task_lease(kernel, res_mgr, "task-2", ident, units=3)
    assert ok2 is True
    assert res.available_capacity == 3

    assert acq1.lease is not None and acq2.lease is not None
    assert acq1.lease.lease_token != acq2.lease.lease_token

    # Explicit release of task-1 restores 4 units
    released = dispatcher.release_task_lease(kernel, res_mgr, "task-1", acq1.lease.lease_token)
    assert released is True
    assert res.available_capacity == 7


# ==============================================================================
# 8. RESOURCE CONTENTION & QUEUEING
# ==============================================================================

def test_resource_contention_and_queueing() -> None:
    """Scenario 8: Contested resource enqueues request; task remains LEASE_PENDING."""
    _, kernel, res_mgr, dispatcher = _setup_env()
    ident = ResourceIdentity("gpu", "node-1", "cuda-0")
    res = Resource(identity=ident, space_id=kernel.space_id, total_capacity=1)
    res_mgr.register_resource(res)

    _add_task_to_kernel(kernel, "task-holder", state="admitted")
    _add_task_to_kernel(kernel, "task-waiter", state="admitted")

    # Holder acquires full capacity (1 unit)
    ok1, acq1, _ = dispatcher.acquire_task_lease(kernel, res_mgr, "task-holder", ident, units=1)
    assert ok1 is True
    assert acq1.granted is True

    # Waiter attempts acquisition: contested
    ok2, acq2, _ = dispatcher.acquire_task_lease(kernel, res_mgr, "task-waiter", ident, units=1)
    assert ok2 is False
    assert acq2.granted is False
    assert acq2.queue_position == 1

    # Invariant: Waiter stays in LEASE_PENDING
    graph = kernel.get_task_graph()
    waiter_node = graph.get_node("task-waiter")
    assert waiter_node is not None
    assert waiter_node.state == TaskState.LEASE_PENDING.value

    # Release holder lease
    assert acq1.lease is not None
    dispatcher.release_task_lease(kernel, res_mgr, "task-holder", acq1.lease.lease_token)

    # Waiter can now acquire the freed resource
    ok2_retry, acq2_retry, _ = dispatcher.acquire_task_lease(kernel, res_mgr, "task-waiter", ident, units=1)
    assert ok2_retry is True
    assert acq2_retry.granted is True
    assert kernel.get_task_graph().get_node("task-waiter").state == TaskState.LEASED.value  # type: ignore


# ==============================================================================
# 9. RESOURCE DENIAL: OVERCAPACITY & UNREGISTERED
# ==============================================================================

def test_resource_denial_unregistered_and_overcapacity() -> None:
    """Scenario 9: Unregistered or overcapacity resource requests transition to FAILED."""
    bus, kernel, res_mgr, dispatcher = _setup_env()
    _add_task_to_kernel(kernel, "task-unreg", state="admitted")
    _add_task_to_kernel(kernel, "task-overcap", state="admitted")

    # A: Unregistered resource
    unregistered_ident = ResourceIdentity("fpga", "node-99", "chip-0")
    ok_unreg, acq_unreg, _ = dispatcher.acquire_task_lease(
        kernel, res_mgr, "task-unreg", unregistered_ident, units=1
    )
    assert ok_unreg is False
    assert acq_unreg.granted is False
    assert "not found" in (acq_unreg.reason or "").lower()
    assert kernel.get_task_graph().get_node("task-unreg").state == TaskState.FAILED.value  # type: ignore

    # B: Overcapacity
    registered_ident = ResourceIdentity("cpu", "host-1", "cores")
    res_mgr.register_resource(Resource(identity=registered_ident, space_id=kernel.space_id, total_capacity=4))
    ok_over, acq_over, _ = dispatcher.acquire_task_lease(
        kernel, res_mgr, "task-overcap", registered_ident, units=16
    )
    assert ok_over is False
    assert acq_over.granted is False
    assert "exceeds total capacity" in (acq_over.reason or "").lower()
    assert kernel.get_task_graph().get_node("task-overcap").state == TaskState.FAILED.value  # type: ignore

    # Verify resource.denied pulses
    denied_pulses = bus.find_by_type("resource.denied")
    assert len(denied_pulses) >= 2


# ==============================================================================
# 10. IDEMPOTENCY: DUPLICATE ACQUISITIONS
# ==============================================================================

def test_idempotency_duplicate_acquire_no_double_allocation() -> None:
    """Scenario 10: Duplicate acquire requests return cached=True with zero double capacity deduction."""
    _, kernel, res_mgr, dispatcher = _setup_env()
    ident = ResourceIdentity("gpu", "node-1", "cuda-0")
    res = Resource(identity=ident, space_id=kernel.space_id, total_capacity=4)
    res_mgr.register_resource(res)

    _add_task_to_kernel(kernel, "task-idempotent", state="admitted")

    # Initial acquisition of 2 units
    ok1, acq1, _ = dispatcher.acquire_task_lease(kernel, res_mgr, "task-idempotent", ident, units=2)
    assert ok1 is True
    assert acq1.granted is True
    assert acq1.lease is not None
    assert res.available_capacity == 2

    # Duplicate acquisition for the same task
    ok2, acq2, _ = dispatcher.acquire_task_lease(kernel, res_mgr, "task-idempotent", ident, units=2)
    assert ok2 is True
    assert acq2.granted is True
    # Zero double deduction
    assert res.available_capacity == 2


# ==============================================================================
# 11. STALE PLAN VERSION & LEASE ROLLBACK PROTECTION
# ==============================================================================

def test_stale_plan_version_lease_rollback_protection() -> None:
    """Scenario 11: If plan CAS transition to LEASED fails, acquired lease is rolled back."""
    _, kernel, res_mgr, dispatcher = _setup_env()
    ident = ResourceIdentity("gpu", "node-1", "cuda-0")
    res = Resource(identity=ident, space_id=kernel.space_id, total_capacity=2)
    res_mgr.register_resource(res)

    _add_task_to_kernel(kernel, "task-stale", state="admitted")

    # Hook res_mgr.acquire to inject a concurrent plan delta immediately after lease acquisition
    original_acquire = res_mgr.acquire

    def _concurrent_acquire(*args: Any, **kwargs: Any) -> Any:
        acq_result = original_acquire(*args, **kwargs)
        # Concurrent plan update happens before CAS transition to LEASED
        v = kernel.get_plan_version()
        bump_delta = PlanDelta(
            space_id=kernel.space_id,
            base_version=v,
            resulting_version=v + 1,
            ops=[{"op": "reassign", "target_node_id": "task-stale", "params": {"concurrent": True}}],
        )
        kernel.commit_plan_delta(bump_delta)
        return acq_result

    res_mgr.acquire = _concurrent_acquire  # type: ignore

    # Attempt lease acquisition: res_mgr grants lease, but plan CAS fails due to version mismatch
    ok, acq, _ = dispatcher.acquire_task_lease(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id="task-stale",
        identity=ident,
    )

    # Invariant: Dispatcher caught CAS failure and rolled back the acquired lease
    assert ok is False
    assert "cas_failed_on_leased_transition" in (acq.reason or "")
    # Invariant: Capacity was released back to resource manager (no orphaned hold)
    assert res.available_capacity == 2


# ==============================================================================
# 12. SPACE ISOLATION ENFORCEMENT (SCCA LAW 1, SPACE-001)
# ==============================================================================

def test_space_isolation_enforcement() -> None:
    """Scenario 12: Cross-space resource acquisition and lease release raise PermissionError."""
    _, kernel_a, res_mgr, dispatcher = _setup_env(space_id="space-a")
    _, kernel_b, _, _ = _setup_env(space_id="space-b")

    # Resource registered in space-a
    ident = ResourceIdentity("gpu", "node-1", "cuda-0")
    res = Resource(identity=ident, space_id="space-a", total_capacity=1)
    res_mgr.register_resource(res)

    _add_task_to_kernel(kernel_b, "task-b", state="admitted")

    # Dispatcher in space-b attempts to acquire space-a's resource -> PermissionError
    with pytest.raises(PermissionError, match="Cross-space resource access rejected"):
        dispatcher.acquire_task_lease(
            kernel=kernel_b,
            resource_mgr=res_mgr,
            task_id="task-b",
            identity=ident,
        )


# ==============================================================================
# 13. CONCURRENT DISPATCHERS ON SHARED RESOURCES
# ==============================================================================

def test_concurrent_dispatchers_thread_safety() -> None:
    """Scenario 13: Multiple concurrent dispatchers contending for finite resource capacity."""
    _, kernel, res_mgr, dispatcher = _setup_env(budget=500.0)
    ident = ResourceIdentity("cores", "node-1", "pool-0")
    total_cores = 5
    res = Resource(identity=ident, space_id=kernel.space_id, total_capacity=total_cores)
    res_mgr.register_resource(res)

    task_count = 10
    for i in range(task_count):
        _add_task_to_kernel(kernel, f"task-{i}", state="admitted")

    results: list[bool] = []
    lock = threading.Lock()

    def _acquire_worker(tid: str) -> None:
        ok, _, _ = dispatcher.acquire_task_lease(
            kernel=kernel,
            resource_mgr=res_mgr,
            task_id=tid,
            identity=ident,
            units=1,
        )
        with lock:
            results.append(ok)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_acquire_worker, f"task-{i}") for i in range(task_count)]
        concurrent.futures.wait(futures)

    # Invariant: Exactly total_cores acquisitions succeeded immediately
    granted_count = sum(1 for r in results if r is True)
    assert granted_count == total_cores
    assert res.available_capacity == 0


# ==============================================================================
# 14. LEASE EXPIRATION & RECLAMATION
# ==============================================================================

def test_lease_expiration_and_clock_sweep() -> None:
    """Scenario 14: Expired leases are swept and capacity automatically reclaimed."""
    clock = FakeClock()
    _, kernel, res_mgr, dispatcher = _setup_env(clock=clock)
    ident = ResourceIdentity("token_bucket", "node-1", "llm-quota")
    res = Resource(identity=ident, space_id=kernel.space_id, total_capacity=2)
    res_mgr.register_resource(res)

    _add_task_to_kernel(kernel, "task-expiring", state="admitted")
    _add_task_to_kernel(kernel, "task-next", state="admitted")

    # Lease with 10.0s duration
    ok1, acq1, _ = dispatcher.acquire_task_lease(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id="task-expiring",
        identity=ident,
        units=2,
        duration_seconds=10.0,
    )
    assert ok1 is True
    assert res.available_capacity == 0

    # Advance clock past lease expiry
    clock.advance(15.0)

    # Next acquisition triggers sweep and reclaims expired capacity
    ok2, acq2, _ = dispatcher.acquire_task_lease(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id="task-next",
        identity=ident,
        units=2,
        duration_seconds=10.0,
    )
    assert ok2 is True
    assert acq2.granted is True


# ==============================================================================
# 15. STRICT PROOF OF NO WORKER EXECUTION
# ==============================================================================

def test_strict_proof_of_no_worker_execution() -> None:
    """Scenario 15: Terminal state of Phase 12.3 is strictly LEASED (zero worker execution)."""
    _, kernel, res_mgr, dispatcher = _setup_env(budget=100.0)
    ident = ResourceIdentity("cpu", "node-1", "core-0")
    res = Resource(identity=ident, space_id=kernel.space_id, total_capacity=2)
    res_mgr.register_resource(res)

    _add_task_to_kernel(kernel, "task-phase12-3", state="ready")

    # Run coordinated pipeline
    result: TaskPipelineResult = dispatcher.coordinate_admission_and_lease(
        kernel=kernel,
        resource_mgr=res_mgr,
        task_id="task-phase12-3",
        resource_identity=ident,
    )

    # SCCA Invariants:
    assert result.admitted is True
    assert result.leased is True
    assert result.terminal_state == TaskState.LEASED.value
    assert result.lease is not None

    # Verify task state in authoritative PlanStore
    graph = kernel.get_task_graph()
    node = graph.get_node("task-phase12-3")
    assert node is not None
    assert node.state == TaskState.LEASED.value

    # STRICT PROOF: Must NOT be in any Phase 12.4 execution states
    assert node.state != "dispatched"
    assert node.state != "running"
    assert node.state != "completed"
    assert node.state != "observing"
    assert node.state != "evaluating"


# ==============================================================================
# 16. TAINT INJECTION CANARY PROTECTION (TAINT-005)
# ==============================================================================

def test_taint_canary_protection_taint_005() -> None:
    """Security: Tainted execution context attempting security grant is blocked and reported CRITICAL."""
    bus, kernel, _, dispatcher = _setup_env()
    _add_task_to_kernel(
        kernel,
        "task-tainted",
        capability="security.grant.admin",
        state="ready",
    )

    admitted, resp, _ = dispatcher.request_task_admission(
        kernel=kernel,
        task_id="task-tainted",
        is_tainted=True,
    )

    assert admitted is False
    assert resp.error == "tainted_security_grant_blocked"

    # Verify security.grant.denied pulse with CRITICAL severity and taint=True
    pulses = bus.find_by_type("security.grant.denied")
    assert len(pulses) == 1
    assert pulses[0].severity.value == "critical"
    assert pulses[0].taint is True


# ==============================================================================
# 17. INVALID TASK OPERATIONS & BOUNDARY INTEGRITY
# ==============================================================================

def test_task_not_found_raises_exception() -> None:
    """Non-existent task ID raises TaskNotFoundError."""
    _, kernel, res_mgr, dispatcher = _setup_env()
    ident = ResourceIdentity("cpu", "node-1", "core-0")

    with pytest.raises(TaskNotFoundError, match="Task 'unknown-task' not found"):
        dispatcher.request_task_admission(kernel, "unknown-task")

    with pytest.raises(TaskNotFoundError, match="Task 'unknown-task' not found"):
        dispatcher.acquire_task_lease(kernel, res_mgr, "unknown-task", ident)


def test_completed_task_admission_denied() -> None:
    """Completed task cannot be re-admitted."""
    _, kernel, _, dispatcher = _setup_env()
    _add_task_to_kernel(kernel, "task-done", state="completed")

    admitted, resp, _ = dispatcher.request_task_admission(kernel, "task-done")
    assert admitted is False
    assert "invalid_task_state_for_admission" in (resp.error or "")
