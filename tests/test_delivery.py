"""Unit tests for Delivery Agent. Owned by Member 3."""

import pytest

from src.agents.delivery import investigate_delivery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockRepository:
    """Minimal repository stub that supplies data via get_* methods."""

    def __init__(
        self,
        orders: dict[str, dict] | None = None,
        items: dict[str, list[dict]] | None = None,
    ):
        self._orders = orders or {}
        self._items = items or {}

    def get_order(self, order_id: str) -> dict | None:
        return self._orders.get(order_id)

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


def _order(
    order_id: str = "order1",
    status: str = "delivered",
    delivered_customer: str | None = "2018-04-01 12:00:00",
    estimated_delivery: str | None = "2018-03-28 00:00:00",
    delivered_carrier: str | None = "2018-03-15 18:00:00",
) -> dict:
    return {
        "order_id": order_id,
        "customer_id": "cust1",
        "order_status": status,
        "order_purchase_timestamp": "2018-03-10 10:00:00",
        "order_approved_at": "2018-03-10 11:00:00",
        "order_delivered_carrier_date": delivered_carrier or "",
        "order_delivered_customer_date": delivered_customer or "",
        "order_estimated_delivery_date": estimated_delivery or "",
    }


def _item(
    order_id: str = "order1",
    item_id: int = 1,
    seller_id: str = "seller1",
    shipping_limit: str = "2018-03-15 20:00:00",
) -> dict:
    return {
        "order_id": order_id,
        "order_item_id": item_id,
        "product_id": "prod1",
        "seller_id": seller_id,
        "shipping_limit_date": shipping_limit,
        "price": 50.0,
        "freight_value": 10.0,
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestLateDeliverySellerFault:
    """Delivered after estimated AND carrier received after shipping limit."""

    def test_late_seller(self):
        repo = MockRepository(
            orders={"o1": _order(
                "o1",
                delivered_customer="2018-04-01 12:00:00",
                estimated_delivery="2018-03-28 00:00:00",
                delivered_carrier="2018-03-16 02:00:00",  # after limit
            )},
            items={"o1": [_item("o1", 1, "s1", "2018-03-15 20:00:00")]},
        )
        result = investigate_delivery(_ticket(order_id="o1"), repo)
        f = result["facts"]

        # delivery_variance > 0  (late delivery)
        assert f["delivery_variance_hours"] is not None
        assert f["delivery_variance_hours"] > 0

        # seller handed off after limit
        assert len(f["seller_handoff_analysis"]) == 1
        sha = f["seller_handoff_analysis"][0]
        assert sha["late_handoff"] is True
        assert sha["handoff_variance_hours"] > 0

        assert "s1" in f["late_handoff_seller_ids"]

    def test_handoff_structure(self):
        repo = MockRepository(
            orders={"o1": _order("o1")},
            items={"o1": [_item("o1")]},
        )
        result = investigate_delivery(_ticket(order_id="o1"), repo)
        for key in (
            "case_id", "from_agent", "to_agent", "task",
            "facts", "evidence_ids", "missing_or_conflicting_data", "next_task",
        ):
            assert key in result, f"Missing key: {key}"
        assert result["from_agent"] == "delivery_agent"


class TestLateDeliveryLogisticsFault:
    """Delivered after estimated, but seller handed off on time."""

    def test_logistics_fault(self):
        repo = MockRepository(
            orders={"o2": _order(
                "o2",
                delivered_customer="2018-04-01 12:00:00",   # after estimate
                estimated_delivery="2018-03-28 00:00:00",
                delivered_carrier="2018-03-14 10:00:00",    # BEFORE limit
            )},
            items={"o2": [_item("o2", 1, "s1", "2018-03-15 20:00:00")]},
        )
        result = investigate_delivery(_ticket(order_id="o2"), repo)
        f = result["facts"]

        assert f["delivery_variance_hours"] > 0  # late delivery
        sha = f["seller_handoff_analysis"][0]
        assert sha["late_handoff"] is False  # seller was on time
        assert f["late_handoff_seller_ids"] == []


class TestOnTimeDelivery:
    """Delivered before or at estimated date → negative or zero variance."""

    def test_on_time(self):
        repo = MockRepository(
            orders={"o3": _order(
                "o3",
                delivered_customer="2018-03-25 10:00:00",
                estimated_delivery="2018-03-28 00:00:00",
            )},
            items={"o3": [_item("o3")]},
        )
        result = investigate_delivery(_ticket(order_id="o3"), repo)
        f = result["facts"]

        assert f["delivery_variance_hours"] < 0  # early delivery


class TestNullTimestamp:
    """Missing delivered_customer_date → variance is null."""

    def test_null_delivered(self):
        repo = MockRepository(
            orders={"o4": _order(
                "o4",
                delivered_customer=None,
                estimated_delivery="2018-03-28 00:00:00",
                delivered_carrier="2018-03-14 10:00:00",
            )},
            items={"o4": [_item("o4")]},
        )
        result = investigate_delivery(_ticket(order_id="o4"), repo)
        f = result["facts"]

        assert f["delivery_variance_hours"] is None
        assert f["delivered_at"] is None
        assert "missing_delivered_customer_date" in result["missing_or_conflicting_data"]

    def test_null_estimated(self):
        repo = MockRepository(
            orders={"o4b": _order(
                "o4b",
                delivered_customer="2018-04-01 12:00:00",
                estimated_delivery=None,
            )},
            items={"o4b": [_item("o4b")]},
        )
        result = investigate_delivery(_ticket(order_id="o4b"), repo)
        assert result["facts"]["delivery_variance_hours"] is None
        assert "missing_estimated_delivery_date" in result["missing_or_conflicting_data"]

    def test_null_carrier_for_handoff(self):
        """Missing carrier date → handoff_variance is null for all sellers."""
        repo = MockRepository(
            orders={"o4c": _order(
                "o4c",
                delivered_customer="2018-04-01 12:00:00",
                estimated_delivery="2018-03-28 00:00:00",
                delivered_carrier=None,
            )},
            items={"o4c": [_item("o4c", 1, "s1")]},
        )
        result = investigate_delivery(_ticket(order_id="o4c"), repo)
        sha = result["facts"]["seller_handoff_analysis"][0]
        assert sha["handoff_variance_hours"] is None
        assert sha["late_handoff"] is None


class TestMultiSellerMixed:
    """Two sellers: one late handoff, one on time."""

    def test_mixed_sellers(self):
        repo = MockRepository(
            orders={"o5": _order(
                "o5",
                delivered_customer="2018-04-01 12:00:00",
                estimated_delivery="2018-03-28 00:00:00",
                delivered_carrier="2018-03-16 00:00:00",
            )},
            items={"o5": [
                _item("o5", 1, "s_late", "2018-03-15 20:00:00"),   # limit before carrier
                _item("o5", 2, "s_ok",   "2018-03-17 00:00:00"),   # limit after carrier
            ]},
        )
        result = investigate_delivery(_ticket(order_id="o5"), repo)
        f = result["facts"]

        assert len(f["seller_handoff_analysis"]) == 2

        analysis_map = {a["seller_id"]: a for a in f["seller_handoff_analysis"]}
        assert analysis_map["s_late"]["late_handoff"] is True
        assert analysis_map["s_ok"]["late_handoff"] is False

        assert "s_late" in f["late_handoff_seller_ids"]
        assert "s_ok" not in f["late_handoff_seller_ids"]


class TestMultiSellerEarliestLimit:
    """For a seller with multiple items, use the earliest shipping_limit."""

    def test_earliest_limit(self):
        repo = MockRepository(
            orders={"o5b": _order(
                "o5b",
                delivered_carrier="2018-03-16 00:00:00",
            )},
            items={"o5b": [
                _item("o5b", 1, "s1", "2018-03-15 10:00:00"),  # earlier
                _item("o5b", 2, "s1", "2018-03-15 22:00:00"),  # later
            ]},
        )
        result = investigate_delivery(_ticket(order_id="o5b"), repo)
        sha = result["facts"]["seller_handoff_analysis"][0]

        # Should use earliest limit: 2018-03-15 10:00:00
        assert sha["shipping_limit_at"] == "2018-03-15 10:00:00"
        # Carrier at 2018-03-16 00:00:00 vs limit 2018-03-15 10:00:00 → 14h late
        assert sha["handoff_variance_hours"] == 14.0
        assert sha["late_handoff"] is True


class TestNoItemOrder:
    """No items → empty seller_handoff_analysis."""

    def test_empty_analysis(self):
        repo = MockRepository(
            orders={"o6": _order("o6")},
            items={},
        )
        result = investigate_delivery(_ticket(order_id="o6"), repo)
        f = result["facts"]

        assert f["seller_handoff_analysis"] == []
        assert f["late_handoff_seller_ids"] == []


class TestOrderNotFound:
    """Order missing from repository → all null, missing_data reported."""

    def test_missing_order(self):
        repo = MockRepository(orders={}, items={})
        result = investigate_delivery(_ticket(order_id="ghost"), repo)

        assert result["facts"]["delivered_at"] is None
        assert result["facts"]["delivery_variance_hours"] is None
        assert "order_not_found" in result["missing_or_conflicting_data"]


class TestVarianceRounding:
    """Variance must be rounded to exactly 2 decimal places."""

    def test_rounding(self):
        # 2018-04-01 15:23:33 - 2018-03-28 00:00:00
        # = 4 days, 15h, 23m, 33s = 111.3925 hours
        repo = MockRepository(
            orders={"o7": _order(
                "o7",
                delivered_customer="2018-04-01 15:23:33",
                estimated_delivery="2018-03-28 00:00:00",
            )},
            items={"o7": [_item("o7")]},
        )
        result = investigate_delivery(_ticket(order_id="o7"), repo)
        v = result["facts"]["delivery_variance_hours"]

        # 4*24 + 15 + 23/60 + 33/3600 = 96 + 15 + 0.383… + 0.00916… = 111.3925
        assert v == 111.39  # rounded to 2 dp
