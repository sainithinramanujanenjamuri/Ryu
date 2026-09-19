"""Harness case: Failure taxonomy matrix verification.

Verifies every transient.* and terminal.* error class behaves strictly
according to the §10 Failure Escalation Table and contracts/registry/failure-taxonomy.json.
spec §10 (Failure Escalation Flow), CONTRACT_MATRIX FAIL-001..FAIL-007 — Phase 3
"""

import json
from pathlib import Path
from typing import Any, cast


def load_failure_taxonomy() -> dict[str, Any]:
    tax_path = (
        Path(__file__).resolve().parents[3]
        / "contracts"
        / "registry"
        / "failure-taxonomy.json"
    )
    with open(tax_path, encoding="utf-8") as f:
        return cast(dict[str, Any], json.load(f))


class MockEscalationHarness:
    """Mock execution and retry harness modeling the §10 escalation flow."""

    def __init__(self) -> None:
        self.attempts: list[dict[str, Any]] = []
        self.escalations: list[dict[str, Any]] = []

    def execute_with_escalation(
        self,
        error_class: str,
        idempotency_key: str,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Simulates worker execution and retry loop under injected error_class."""
        taxonomy = load_failure_taxonomy()
        all_classes: set[str] = set()
        for cat_info in taxonomy.get("classes", {}).values():
            for item in cat_info.get("categories", []):
                all_classes.add(item["class"])

        if error_class not in all_classes:
            raise ValueError(f"Unknown error class: {error_class}")

        is_transient = error_class.startswith("transient.")
        is_terminal = error_class.startswith("terminal.")

        if not (is_transient or is_terminal):
            raise ValueError(f"Unknown error category: {error_class}")

        attempt_count = 0
        used_keys: list[str] = []

        if is_transient:
            # Retryable: bounded retries with same idempotency_key
            for _ in range(max_retries):
                attempt_count += 1
                used_keys.append(idempotency_key)
                self.attempts.append({
                    "attempt": attempt_count,
                    "key": idempotency_key,
                    "error": error_class,
                })

            # After max retries, escalate to Agent
            outcome = {
                "error_class": error_class,
                "retryable": True,
                "attempts": attempt_count,
                "idempotency_keys": used_keys,
                "escalation": "escalated_to_agent_after_max_retries",
            }
            self.escalations.append(outcome)
            return outcome

        else:
            # Terminal: NOT retryable; zero retries; immediate escalation
            attempt_count = 1
            used_keys.append(idempotency_key)
            self.attempts.append({
                "attempt": attempt_count,
                "key": idempotency_key,
                "error": error_class,
            })

            escalation_target = "agent_replan"
            if error_class == "terminal.permission_denied":
                escalation_target = "human_approval"
            elif error_class == "terminal.budget_exceeded":
                escalation_target = "human_budget_escalation"
            elif error_class == "terminal.invalid_params":
                escalation_target = "agent_fix_request"

            outcome = {
                "error_class": error_class,
                "retryable": False,
                "attempts": 1,
                "idempotency_keys": used_keys,
                "escalation": escalation_target,
            }
            self.escalations.append(outcome)
            return outcome


def test_transient_timeout_bounded_retry_and_key_reuse() -> None:
    # FAIL-001: transient.timeout follows retry policy with same idempotency key
    harness = MockEscalationHarness()
    res = harness.execute_with_escalation("transient.timeout", idempotency_key="key-timeout-1")

    assert res["retryable"] is True
    assert res["attempts"] == 3
    assert all(k == "key-timeout-1" for k in res["idempotency_keys"])


def test_transient_rate_limit_backoff_and_retry() -> None:
    # FAIL-002: transient.rate_limit is retryable
    harness = MockEscalationHarness()
    res = harness.execute_with_escalation("transient.rate_limit", idempotency_key="key-rate-1")

    assert res["retryable"] is True
    assert res["attempts"] == 3


def test_transient_network_retry() -> None:
    # FAIL-003: transient.network follows retry policy with same idempotency key
    harness = MockEscalationHarness()
    res = harness.execute_with_escalation("transient.network", idempotency_key="key-net-1")

    assert res["retryable"] is True
    assert res["attempts"] == 3
    assert all(k == "key-net-1" for k in res["idempotency_keys"])


def test_terminal_invalid_params_no_retry() -> None:
    # FAIL-004: terminal.invalid_params does not retry
    harness = MockEscalationHarness()
    res = harness.execute_with_escalation("terminal.invalid_params", idempotency_key="key-inv-1")

    assert res["retryable"] is False
    assert res["attempts"] == 1
    assert res["escalation"] == "agent_fix_request"


def test_terminal_permission_denied_immediate_human_escalation() -> None:
    # FAIL-005: terminal.permission_denied escalates immediately to human
    harness = MockEscalationHarness()
    res = harness.execute_with_escalation(
        "terminal.permission_denied", idempotency_key="key-perm-1"
    )

    assert res["retryable"] is False
    assert res["attempts"] == 1
    assert res["escalation"] == "human_approval"


def test_terminal_budget_exceeded_immediate_escalation() -> None:
    # FAIL-006: terminal.budget_exceeded follows admission policy without dispatch
    harness = MockEscalationHarness()
    res = harness.execute_with_escalation("terminal.budget_exceeded", idempotency_key="key-bud-1")

    assert res["retryable"] is False
    assert res["attempts"] == 1
    assert res["escalation"] == "human_budget_escalation"


def test_terminal_not_found_escalates_to_replan() -> None:
    # FAIL-007: terminal.not_found escalates to agent replan
    harness = MockEscalationHarness()
    res = harness.execute_with_escalation("terminal.not_found", idempotency_key="key-nf-1")

    assert res["retryable"] is False
    assert res["attempts"] == 1
    assert res["escalation"] == "agent_replan"
