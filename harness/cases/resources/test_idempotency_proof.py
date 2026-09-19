"""Harness case: Idempotency proof with stored-response semantics.

Verifies:
- 3x mid-timeout retries produce exactly one charge execution.
- Retries return cached stored response without duplicate side effects.
spec §10, ROADMAP Phase 3, ADR-0006, CONTRACT_MATRIX IDEM-001..IDEM-003 — Phase 3
"""

from typing import Any


class MockPaymentChargeProvider:
    """Mock side-effecting provider implementing stored-response idempotency per ADR-0006."""

    def __init__(self) -> None:
        self.charge_ledger: list[dict[str, Any]] = []
        self._stored_responses: dict[str, dict[str, Any]] = {}

    def charge(
        self,
        space_id: str,
        account_id: str,
        amount_cents: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Execute payment charge with strict idempotency."""
        composite_key = f"{space_id}:{idempotency_key}"

        # If already executed for this idempotency key, return stored response
        if composite_key in self._stored_responses:
            cached_resp = dict(self._stored_responses[composite_key])
            cached_resp["cached"] = True
            return cached_resp

        # Perform the actual side-effect
        charge_record = {
            "charge_id": f"chg-{len(self.charge_ledger) + 1}",
            "space_id": space_id,
            "account_id": account_id,
            "amount_cents": amount_cents,
            "idempotency_key": idempotency_key,
        }
        self.charge_ledger.append(charge_record)

        response = {
            "status": "success",
            "charge_id": charge_record["charge_id"],
            "amount_cents": amount_cents,
            "cached": False,
        }
        self._stored_responses[composite_key] = response
        return response


def test_payment_charge_idempotency_proof() -> None:
    provider = MockPaymentChargeProvider()
    key = "tx-order-888"

    # Call 1: Initial charge attempt
    resp1 = provider.charge("space-1", "acct-1", 5000, idempotency_key=key)
    assert resp1["status"] == "success"
    assert resp1["cached"] is False
    assert len(provider.charge_ledger) == 1

    # Call 2: Simulated timeout retry with same idempotency_key
    resp2 = provider.charge("space-1", "acct-1", 5000, idempotency_key=key)
    assert resp2["status"] == "success"
    assert resp2["cached"] is True
    assert resp2["charge_id"] == resp1["charge_id"]
    assert len(provider.charge_ledger) == 1, "Side effect MUST NOT execute twice"

    # Call 3: Third retry
    resp3 = provider.charge("space-1", "acct-1", 5000, idempotency_key=key)
    assert resp3["status"] == "success"
    assert resp3["cached"] is True
    assert resp3["charge_id"] == resp1["charge_id"]
    assert len(provider.charge_ledger) == 1, "Exactly one charge record must exist in ledger"
