"""Unit tests for Payment Agent. Owned by Member 3."""

import pytest

from src.agents.payment import investigate_payment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockRepository:
    """Minimal repository stub that supplies data via get_* methods."""

    def __init__(
        self,
        payments: dict[str, list[dict]] | None = None,
        items: dict[str, list[dict]] | None = None,
    ):
        self._payments = payments or {}
        self._items = items or {}

    def get_payments(self, order_id: str) -> list[dict]:
        return self._payments.get(order_id, [])

    def get_items(self, order_id: str) -> list[dict]:
        return self._items.get(order_id, [])


def _ticket(case_id: str = "EC_TEST", order_id: str = "order1") -> dict:
    return {
        "case_id": case_id,
        "customer_request": {
            "language": "vi",
            "message": "Test",
            "claimed_order_id": order_id,
        },
        "investigation_scope": {
            "include_customer_history": True,
            "include_product_context": True,
        },
        "policy_version": "EC_POLICY_V2",
    }


def _payment(
    order_id: str = "order1",
    seq: int = 1,
    ptype: str = "credit_card",
    value: float = 100.0,
) -> dict:
    return {
        "order_id": order_id,
        "payment_sequential": seq,
        "payment_type": ptype,
        "payment_installments": 1,
        "payment_value": value,
    }


def _item(
    order_id: str = "order1",
    item_id: int = 1,
    price: float = 80.0,
    freight: float = 20.0,
) -> dict:
    return {
        "order_id": order_id,
        "order_item_id": item_id,
        "price": price,
        "freight_value": freight,
        "seller_id": "seller1",
        "product_id": "product1",
        "shipping_limit_date": "2018-01-15 00:00:00",
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestSinglePaymentSingleItem:
    """Basic case: one payment, one item, amounts match → reconciled."""

    def test_reconciled(self):
        repo = MockRepository(
            payments={"o1": [_payment("o1", 1, "credit_card", 100.0)]},
            items={"o1": [_item("o1", 1, 80.0, 20.0)]},
        )
        result = investigate_payment(_ticket(order_id="o1"), repo)
        f = result["facts"]

        assert f["payment_total_brl"] == 100.0
        assert f["item_total_brl"] == 80.0
        assert f["freight_total_brl"] == 20.0
        assert f["expected_total_brl"] == 100.0
        assert f["difference_brl"] == 0.0
        assert f["reconciled"] is True

    def test_evidence_ids(self):
        repo = MockRepository(
            payments={"o1": [_payment("o1", 1, "credit_card", 100.0)]},
            items={"o1": [_item("o1")]},
        )
        result = investigate_payment(_ticket(order_id="o1"), repo)
        assert result["evidence_ids"] == ["payment:o1:1"]

    def test_payment_types(self):
        repo = MockRepository(
            payments={"o1": [_payment("o1", 1, "credit_card", 100.0)]},
            items={"o1": [_item("o1")]},
        )
        result = investigate_payment(_ticket(order_id="o1"), repo)
        assert result["facts"]["payment_types"] == ["credit_card"]

    def test_handoff_structure(self):
        repo = MockRepository(
            payments={"o1": [_payment("o1")]},
            items={"o1": [_item("o1")]},
        )
        result = investigate_payment(_ticket(order_id="o1"), repo)
        # Verify all required Handoff keys are present
        for key in (
            "case_id", "from_agent", "to_agent", "task",
            "facts", "evidence_ids", "missing_or_conflicting_data", "next_task",
        ):
            assert key in result, f"Missing key: {key}"
        assert result["from_agent"] == "payment_agent"
        assert result["to_agent"] == "coordinator"


class TestSplitPayment:
    """Multiple payment rows, total matches item + freight."""

    def test_two_payment_rows(self):
        repo = MockRepository(
            payments={"o2": [
                _payment("o2", 1, "credit_card", 60.0),
                _payment("o2", 2, "voucher", 40.0),
            ]},
            items={"o2": [_item("o2", 1, 80.0, 20.0)]},
        )
        result = investigate_payment(_ticket(order_id="o2"), repo)
        f = result["facts"]

        assert f["payment_total_brl"] == 100.0
        assert f["reconciled"] is True
        assert f["payment_types"] == ["credit_card", "voucher"]
        assert len(result["evidence_ids"]) == 2

    def test_duplicate_payment_type_deduplication(self):
        repo = MockRepository(
            payments={"o2": [
                _payment("o2", 1, "credit_card", 50.0),
                _payment("o2", 2, "credit_card", 50.0),
            ]},
            items={"o2": [_item("o2", 1, 80.0, 20.0)]},
        )
        result = investigate_payment(_ticket(order_id="o2"), repo)
        assert result["facts"]["payment_types"] == ["credit_card"]


class TestPaymentMismatch:
    """Payment total does not match expected → reconciled=False."""

    def test_mismatch(self):
        repo = MockRepository(
            payments={"o3": [_payment("o3", 1, "credit_card", 120.0)]},
            items={"o3": [_item("o3", 1, 80.0, 20.0)]},
        )
        result = investigate_payment(_ticket(order_id="o3"), repo)
        f = result["facts"]

        assert f["difference_brl"] == 20.0
        assert f["reconciled"] is False


class TestToleranceThreshold:
    """Difference within ±0.10 BRL → reconciled=True."""

    def test_within_tolerance(self):
        repo = MockRepository(
            payments={"o4": [_payment("o4", 1, "credit_card", 100.08)]},
            items={"o4": [_item("o4", 1, 80.0, 20.0)]},
        )
        result = investigate_payment(_ticket(order_id="o4"), repo)
        f = result["facts"]

        assert f["difference_brl"] == 0.08
        assert f["reconciled"] is True

    def test_at_boundary(self):
        repo = MockRepository(
            payments={"o4b": [_payment("o4b", 1, "credit_card", 100.10)]},
            items={"o4b": [_item("o4b", 1, 80.0, 20.0)]},
        )
        result = investigate_payment(_ticket(order_id="o4b"), repo)
        assert result["facts"]["reconciled"] is True  # abs(0.10) <= 0.10

    def test_exceeds_tolerance(self):
        repo = MockRepository(
            payments={"o4c": [_payment("o4c", 1, "credit_card", 100.11)]},
            items={"o4c": [_item("o4c", 1, 80.0, 20.0)]},
        )
        result = investigate_payment(_ticket(order_id="o4c"), repo)
        assert result["facts"]["reconciled"] is False


class TestNoItemOrder:
    """Order with no items: expected, difference, reconciled → null."""

    def test_null_fields(self):
        repo = MockRepository(
            payments={"o5": [_payment("o5", 1, "credit_card", 50.0)]},
            items={},  # no items
        )
        result = investigate_payment(_ticket(order_id="o5"), repo)
        f = result["facts"]

        assert f["payment_total_brl"] == 50.0
        assert f["item_total_brl"] is None
        assert f["freight_total_brl"] is None
        assert f["expected_total_brl"] is None
        assert f["difference_brl"] is None
        assert f["reconciled"] is None

    def test_missing_data_reported(self):
        repo = MockRepository(
            payments={"o5": [_payment("o5")]},
            items={},
        )
        result = investigate_payment(_ticket(order_id="o5"), repo)
        assert "no_item_rows_for_order" in result["missing_or_conflicting_data"]


class TestMaxFivePaymentIDs:
    """More than 5 payments → only first 5 evidence IDs emitted."""

    def test_cap_at_five(self):
        payments = [_payment("o6", seq=i, value=10.0) for i in range(1, 8)]
        repo = MockRepository(
            payments={"o6": payments},
            items={"o6": [_item("o6")]},
        )
        result = investigate_payment(_ticket(order_id="o6"), repo)

        assert len(result["evidence_ids"]) == 5
        assert len(result["facts"]["payment_ids"]) == 5
        # Verify first and last evidence ID
        assert result["evidence_ids"][0] == "payment:o6:1"
        assert result["evidence_ids"][4] == "payment:o6:5"


class TestRoundingPrecision:
    """All monetary amounts must be rounded to 2 decimal places."""

    def test_amounts_rounded(self):
        repo = MockRepository(
            payments={"o7": [
                _payment("o7", 1, "credit_card", 33.33),
                _payment("o7", 2, "boleto", 66.67),
            ]},
            items={"o7": [_item("o7", 1, 83.33, 16.67)]},
        )
        result = investigate_payment(_ticket(order_id="o7"), repo)
        f = result["facts"]

        assert f["payment_total_brl"] == 100.0
        assert f["item_total_brl"] == 83.33
        assert f["freight_total_brl"] == 16.67
        assert f["expected_total_brl"] == 100.0
        assert f["difference_brl"] == 0.0
        assert f["reconciled"] is True

    def test_negative_difference(self):
        """Payment less than expected → negative difference."""
        repo = MockRepository(
            payments={"o8": [_payment("o8", 1, "credit_card", 90.0)]},
            items={"o8": [_item("o8", 1, 80.0, 20.0)]},
        )
        result = investigate_payment(_ticket(order_id="o8"), repo)
        f = result["facts"]
        assert f["difference_brl"] == -10.0
        assert f["reconciled"] is False
