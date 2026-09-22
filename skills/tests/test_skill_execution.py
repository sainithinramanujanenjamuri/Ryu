"""Unit tests for SkillExecutor and capability request delegation."""

import pytest
from unittest.mock import MagicMock

from core.capabilities.admission import AdmissionController, CapabilityRequest, CapabilityResponse
from skills.contract import (
    SkillError,
    SkillExecutionContext,
    SkillRequest,
    SkillResponse,
)
from skills.executor import SkillExecutor
from skills.model import (
    RiskTier,
    SkillLifecycleState,
    SkillRegistration,
    compute_sha256_hash,
)
from skills.registry import DefaultHmacVerifier, SkillRegistry


@pytest.fixture
def verifier():
    return DefaultHmacVerifier("exec-secret")


@pytest.fixture
def registry(verifier):
    reg = SkillRegistry(verifier=verifier)
    # Register a standard math skill
    content = {"code": "math_operations"}
    h = compute_sha256_hash(content)
    sig = verifier.sign("math-skill", "1.0.0", h, RiskTier.LOW, "admin")
    skill = SkillRegistration(
        skill_id="math-skill",
        version="1.0.0",
        content_hash=h,
        signature=sig,
        capabilities=("math.calc",),
        risk_tier=RiskTier.LOW,
        registered_by="admin",
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
        output_schema={
            "type": "object",
            "properties": {"sum": {"type": "number"}},
            "required": ["sum"],
        },
        lifecycle_state=SkillLifecycleState.ENABLED,
    )
    reg.register_skill(skill, payload=content)
    return reg


@pytest.fixture
def mock_admission():
    admission = MagicMock(spec=AdmissionController)
    admission.check_admission.return_value = CapabilityResponse(status="ok", result=None)
    return admission


@pytest.fixture
def context():
    return SkillExecutionContext(
        space_id="space-1",
        plan_id="plan-1",
        plan_version=1,
        task_id="task-1",
        correlation_id="corr-1",
        parent_pulse_id="pulse-root",
        agent_id="agent-1",
        worker_id="worker-1",
        skill_id="math-skill",
        skill_version="1.0.0",
    )


def test_skill_execution_success(registry, mock_admission, context):
    executor = SkillExecutor(registry=registry, admission_controller=mock_admission)

    def mock_handler(capability, parameters, context):
        assert capability == "math.calc"
        return {"sum": parameters["a"] + parameters["b"]}

    request = SkillRequest(
        request_id="req-1",
        context=context,
        parameters={"a": 5, "b": 10},
    )

    resp: SkillResponse = executor.execute(request, handler=mock_handler)
    assert resp.is_success
    assert resp.status == "ok"
    assert resp.output_data == {"sum": 15}
    assert resp.taint is True  # Output stamped with taint: True (ADR-0032)
    assert resp.duration_seconds >= 0.0

    mock_admission.check_admission.assert_called_once()
    called_cap_req: CapabilityRequest = mock_admission.check_admission.call_args[0][0]
    assert called_cap_req.capability == "math.calc"
    assert called_cap_req.space_id == "space-1"


def test_skill_execution_input_schema_violation(registry, mock_admission, context):
    executor = SkillExecutor(registry=registry, admission_controller=mock_admission)

    request = SkillRequest(
        request_id="req-2",
        context=context,
        parameters={"a": "not-a-number", "b": 10},  # Invalid type
    )

    resp = executor.execute(request, handler=lambda *a, **kw: {})
    assert not resp.is_success
    assert resp.status == "failed"
    assert resp.error is not None
    assert resp.error.error_class == "terminal.schema_violation"


def test_skill_execution_admission_denied(registry, context):
    mock_admission = MagicMock(spec=AdmissionController)
    mock_admission.check_admission.return_value = CapabilityResponse(
        status="denied",
        error="Attention budget exceeded",
    )

    executor = SkillExecutor(registry=registry, admission_controller=mock_admission)
    request = SkillRequest(
        request_id="req-3",
        context=context,
        parameters={"a": 1, "b": 2},
    )

    resp = executor.execute(request, handler=lambda *a, **kw: {})
    assert resp.status == "denied"
    assert resp.error is not None
    assert "Admission denied" in resp.error.message


def test_skill_execution_disabled_skill(registry, mock_admission, context):
    registry.set_skill_state("math-skill", "1.0.0", SkillLifecycleState.DISABLED)
    executor = SkillExecutor(registry=registry, admission_controller=mock_admission)

    request = SkillRequest(
        request_id="req-4",
        context=context,
        parameters={"a": 1, "b": 2},
    )

    resp = executor.execute(request, handler=lambda *a, **kw: {})
    assert resp.status == "denied"
    assert "DISABLED" in resp.error.message


def test_skill_execution_not_found(registry, mock_admission):
    ctx = SkillExecutionContext(
        space_id="s1",
        plan_id="p1",
        plan_version=1,
        task_id="t1",
        correlation_id="c1",
        parent_pulse_id="pr",
        agent_id="a1",
        worker_id="w1",
        skill_id="nonexistent-skill",
        skill_version="1.0.0",
    )
    executor = SkillExecutor(registry=registry, admission_controller=mock_admission)
    request = SkillRequest(request_id="req-5", context=ctx, parameters={})

    resp = executor.execute(request, handler=lambda *a, **kw: {})
    assert resp.status == "failed"
    assert resp.error.error_class == "terminal.not_found"
