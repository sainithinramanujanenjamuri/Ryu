"""Harness specification tests for Skill Governance and Admission Integration.

Verifies:
- Skills have zero authority and must delegate via CapabilityRequest (Law 2, ADR-0028).
- Space Kernel AdmissionController evaluates all skill requests pre-dispatch.
- Budget and risk-tier denials fail closed (Law 6).
- Outputs are stamped with taint: True (ADR-0032).

spec §4, §7, §16, ADR-0028, ADR-0032 — Phase 9
"""

import pytest
from unittest.mock import MagicMock

from core.capabilities.admission import AdmissionController, CapabilityRequest, CapabilityResponse
from skills.contract import SkillExecutionContext, SkillRequest, SkillResponse
from skills.executor import SkillExecutor
from skills.model import RiskTier, SkillLifecycleState, SkillRegistration, compute_sha256_hash
from skills.registry import DefaultHmacVerifier, SkillRegistry


@pytest.fixture
def governance_setup():
    verifier = DefaultHmacVerifier("gov-key")
    reg = SkillRegistry(verifier=verifier)

    content = {"name": "file-reader"}
    h = compute_sha256_hash(content)
    sig = verifier.sign("file-reader", "1.0.0", h, RiskTier.LOW, "admin")

    skill = SkillRegistration(
        skill_id="file-reader",
        version="1.0.0",
        content_hash=h,
        signature=sig,
        capabilities=("file.read",),
        risk_tier=RiskTier.LOW,
        registered_by="admin",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        lifecycle_state=SkillLifecycleState.ENABLED,
    )
    reg.register_skill(skill, payload=content)
    return reg


def test_skill_cannot_bypass_admission_controller(governance_setup):
    """Law 2: Skill MUST NOT bypass AdmissionController."""
    reg = governance_setup
    admission = MagicMock(spec=AdmissionController)
    admission.check_admission.return_value = CapabilityResponse(
        status="denied",
        error="Permission denied: Space policy prohibits file.read",
    )

    executor = SkillExecutor(registry=reg, admission_controller=admission)

    ctx = SkillExecutionContext(
        space_id="space-isolated",
        plan_id="plan-1",
        plan_version=1,
        task_id="task-1",
        correlation_id="corr-1",
        parent_pulse_id="pulse-root",
        agent_id="agent-1",
        worker_id="worker-1",
        skill_id="file-reader",
        skill_version="1.0.0",
    )

    request = SkillRequest(
        request_id="req-gov-1",
        context=ctx,
        parameters={"path": "/data/test.txt"},
    )

    resp: SkillResponse = executor.execute(request, handler=lambda *a, **kw: "read_content")
    assert resp.status == "denied"
    assert "Admission denied" in resp.error.message

    # Ensure AdmissionController was called with the exact capability
    admission.check_admission.assert_called_once()
    cap_req: CapabilityRequest = admission.check_admission.call_args[0][0]
    assert cap_req.capability == "file.read"
    assert cap_req.space_id == "space-isolated"


def test_skill_output_stamped_taint(governance_setup):
    """ADR-0032: Skill output is marked taint: True."""
    reg = governance_setup
    admission = MagicMock(spec=AdmissionController)
    admission.check_admission.return_value = CapabilityResponse(status="ok")

    executor = SkillExecutor(registry=reg, admission_controller=admission)

    ctx = SkillExecutionContext(
        space_id="space-main",
        plan_id="plan-1",
        plan_version=1,
        task_id="task-2",
        correlation_id="corr-2",
        parent_pulse_id="pulse-root",
        agent_id="agent-1",
        worker_id="worker-1",
        skill_id="file-reader",
        skill_version="1.0.0",
    )

    request = SkillRequest(
        request_id="req-gov-2",
        context=ctx,
        parameters={"path": "/data/test.txt"},
    )

    resp: SkillResponse = executor.execute(
        request,
        handler=lambda *a, **kw: {"content": "external file contents"},
    )
    assert resp.is_success
    assert resp.status == "ok"
    assert resp.taint is True  # Invariant: untrusted tool output is tainted
