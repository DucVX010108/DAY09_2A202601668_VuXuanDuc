"""Delivery and seller-handoff agent. Owned by Member 3."""

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_TS_FMT = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------------------
# Module-level CSV caches (fallback when OlistRepository is not ready)
# ---------------------------------------------------------------------------
_orders_index: dict[str, dict] | None = None
_items_index: dict[str, list[dict]] | None = None


def _load_orders_index() -> dict[str, dict]:
    """Lazy-load and index olist_orders_dataset.csv by order_id."""
    global _orders_index
    if _orders_index is not None:
        return _orders_index
    _orders_index = {}
    csv_path = _DATA_DIR / "olist_orders_dataset.csv"
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            _orders_index[row["order_id"]] = row
    return _orders_index


def _load_items_index() -> dict[str, list[dict]]:
    """Lazy-load and index olist_order_items_dataset.csv by order_id."""
    global _items_index
    if _items_index is not None:
        return _items_index
    _items_index = {}
    csv_path = _DATA_DIR / "olist_order_items_dataset.csv"
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            oid = row["order_id"]
            row["order_item_id"] = int(row["order_item_id"])
            row["price"] = float(row["price"])
            row["freight_value"] = float(row["freight_value"])
            _items_index.setdefault(oid, []).append(row)
    return _items_index


# ---------------------------------------------------------------------------
# Repository adapter
# ---------------------------------------------------------------------------

def _get_order(repository: object, order_id: str) -> dict | None:
    """Retrieve a single order row, using repo or CSV fallback."""
    if hasattr(repository, "get_order"):
        return repository.get_order(order_id)
    if hasattr(repository, "orders") and isinstance(
        getattr(repository, "orders", None), dict
    ):
        return repository.orders.get(order_id)
    return _load_orders_index().get(order_id)


def _get_items(repository: object, order_id: str) -> list[dict]:
    """Retrieve item rows for an order, using repo or CSV fallback."""
    if hasattr(repository, "get_items"):
        return repository.get_items(order_id)
    if hasattr(repository, "items") and isinstance(
        getattr(repository, "items", None), dict
    ):
        return repository.items.get(order_id, [])
    return _load_items_index().get(order_id, [])


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _clean_ts(val: str | None) -> str | None:
    """Return a cleaned timestamp string or None for empty / NaN values."""
    if val is None:
        return None
    stripped = val.strip()
    if stripped == "" or stripped.lower() in ("nan", "nat"):
        return None
    return stripped


def _parse_dt(val: str | None) -> datetime | None:
    """Parse a CSV timestamp into a datetime, or None."""
    cleaned = _clean_ts(val)
    if cleaned is None:
        return None
    try:
        return datetime.strptime(cleaned, _TS_FMT)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def investigate_delivery(ticket: dict, repository: object) -> dict:
    """Return timestamps, delivery variance and per-seller handoff facts.

    Returns a ``Handoff``-compatible dict.
    """
    case_id: str = ticket["case_id"]
    order_id: str = ticket["customer_request"]["claimed_order_id"]

    order = _get_order(repository, order_id)
    items = _get_items(repository, order_id)

    missing: list[str] = []

    # ------------------------------------------------------------------
    # Edge case: order not found
    # ------------------------------------------------------------------
    if order is None:
        missing.append("order_not_found")
        return {
            "case_id": case_id,
            "from_agent": "delivery_agent",
            "to_agent": "coordinator",
            "task": "delivery_analysis",
            "facts": {
                "delivered_at": None,
                "estimated_delivery_at": None,
                "carrier_handoff_at": None,
                "delivery_variance_hours": None,
                "seller_handoff_analysis": [],
                "late_handoff_seller_ids": [],
            },
            "evidence_ids": [],
            "missing_or_conflicting_data": missing,
            "next_task": "policy_evaluation",
        }

    # ------------------------------------------------------------------
    # 1. Keep three timestamps in CSV format (or None)
    # ------------------------------------------------------------------
    delivered_at = _clean_ts(order.get("order_delivered_customer_date"))
    estimated_at = _clean_ts(order.get("order_estimated_delivery_date"))
    carrier_handoff_at = _clean_ts(order.get("order_delivered_carrier_date"))

    delivered_dt = _parse_dt(order.get("order_delivered_customer_date"))
    estimated_dt = _parse_dt(order.get("order_estimated_delivery_date"))
    carrier_dt = _parse_dt(order.get("order_delivered_carrier_date"))

    # ------------------------------------------------------------------
    # 2. delivery_variance_hours = delivered - estimated
    # ------------------------------------------------------------------
    if delivered_dt is not None and estimated_dt is not None:
        delta_secs = (delivered_dt - estimated_dt).total_seconds()
        delivery_variance_hours: float | None = round(delta_secs / 3600, 2)
    else:
        delivery_variance_hours = None
        if delivered_dt is None:
            missing.append("missing_delivered_customer_date")
        if estimated_dt is None:
            missing.append("missing_estimated_delivery_date")

    # ------------------------------------------------------------------
    # 3. Per-seller handoff analysis
    # ------------------------------------------------------------------
    seller_handoff_analysis: list[dict[str, Any]] = []
    late_handoff_seller_ids: list[str] = []

    if items:
        # Group items by seller_id (preserving source order)
        sellers: dict[str, list[dict]] = {}
        for item in items:
            sid = item["seller_id"]
            sellers.setdefault(sid, []).append(item)

        for seller_id, seller_items in sellers.items():
            # Find earliest shipping_limit_date for this seller
            limit_pairs: list[tuple[str, datetime]] = []
            for item in seller_items:
                raw = item.get("shipping_limit_date")
                limit_str = _clean_ts(raw)
                if limit_str is not None:
                    limit_dt = _parse_dt(raw)
                    if limit_dt is not None:
                        limit_pairs.append((limit_str, limit_dt))

            if limit_pairs:
                # Sort by datetime → earliest first
                limit_pairs.sort(key=lambda x: x[1])
                earliest_limit_str, earliest_limit_dt = limit_pairs[0]

                if carrier_dt is not None:
                    hv_secs = (carrier_dt - earliest_limit_dt).total_seconds()
                    handoff_variance: float | None = round(hv_secs / 3600, 2)
                    late = handoff_variance > 0
                else:
                    handoff_variance = None
                    # ``late_handoff`` is a boolean flag in the published
                    # schema.  Missing timestamps make the variance unknown,
                    # but do not provide evidence of a late handoff.
                    late = False
                    if "missing_carrier_handoff_date" not in missing:
                        missing.append("missing_carrier_handoff_date")
            else:
                earliest_limit_str = None
                handoff_variance = None
                late = False

            seller_handoff_analysis.append({
                "seller_id": seller_id,
                "shipping_limit_at": earliest_limit_str,
                "handoff_variance_hours": handoff_variance,
                "late_handoff": late,
            })

            if late is True:
                late_handoff_seller_ids.append(seller_id)

    # ------------------------------------------------------------------
    # 4. Build Handoff
    # ------------------------------------------------------------------
    return {
        "case_id": case_id,
        "from_agent": "delivery_agent",
        "to_agent": "coordinator",
        "task": "delivery_analysis",
        "facts": {
            "delivered_at": delivered_at,
            "estimated_delivery_at": estimated_at,
            "carrier_handoff_at": carrier_handoff_at,
            "delivery_variance_hours": delivery_variance_hours,
            "seller_handoff_analysis": seller_handoff_analysis,
            "late_handoff_seller_ids": late_handoff_seller_ids,
        },
        "evidence_ids": [],
        "missing_or_conflicting_data": missing,
        "next_task": "policy_evaluation",
    }
