"""Payment reconciliation agent. Owned by Member 3."""

import csv
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# ---------------------------------------------------------------------------
# Module-level CSV caches (fallback when OlistRepository is not ready)
# ---------------------------------------------------------------------------
_payments_index: dict[str, list[dict]] | None = None
_items_index: dict[str, list[dict]] | None = None


def _load_payments_index() -> dict[str, list[dict]]:
    """Lazy-load and index olist_order_payments_dataset.csv by order_id."""
    global _payments_index
    if _payments_index is not None:
        return _payments_index
    _payments_index = {}
    csv_path = _DATA_DIR / "olist_order_payments_dataset.csv"
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            oid = row["order_id"]
            row["payment_sequential"] = int(row["payment_sequential"])
            row["payment_installments"] = int(row["payment_installments"])
            row["payment_value"] = float(row["payment_value"])
            _payments_index.setdefault(oid, []).append(row)
    return _payments_index


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
# Repository adapter – works with OlistRepository or falls back to CSV
# ---------------------------------------------------------------------------

def _get_payments(repository: object, order_id: str) -> list[dict]:
    """Retrieve payment rows for an order, using repo or CSV fallback."""
    if hasattr(repository, "get_payments"):
        return repository.get_payments(order_id)
    if hasattr(repository, "payments") and isinstance(
        getattr(repository, "payments", None), dict
    ):
        return repository.payments.get(order_id, [])
    return _load_payments_index().get(order_id, [])


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
# Public API
# ---------------------------------------------------------------------------

def investigate_payment(ticket: dict, repository: object) -> dict:
    """Return payment rows and K4 reconciliation facts.

    Returns a ``Handoff``-compatible dict with keys:
        case_id, from_agent, to_agent, task, facts,
        evidence_ids, missing_or_conflicting_data, next_task.
    """
    case_id: str = ticket["case_id"]
    order_id: str = ticket["customer_request"]["claimed_order_id"]

    payments = _get_payments(repository, order_id)
    items = _get_items(repository, order_id)

    missing: list[str] = []

    # ------------------------------------------------------------------
    # 1. Payment total (always from actual payment rows)
    # ------------------------------------------------------------------
    payment_total_brl = round(
        sum(p["payment_value"] for p in payments), 2
    ) if payments else 0.0

    # ------------------------------------------------------------------
    # 2. Payment types – deduplicated, source-order preserved
    # ------------------------------------------------------------------
    seen_types: list[str] = []
    for p in payments:
        pt = p["payment_type"]
        if pt not in seen_types:
            seen_types.append(pt)
    payment_types = seen_types

    # ------------------------------------------------------------------
    # 3. Evidence IDs – max 5, source order
    # ------------------------------------------------------------------
    evidence_ids: list[str] = [
        f"payment:{order_id}:{p['payment_sequential']}"
        for p in payments[:5]
    ]

    # Payment IDs for affected_entities (without "payment:" prefix)
    payment_ids: list[str] = [
        f"{order_id}:{p['payment_sequential']}"
        for p in payments[:5]
    ]

    # ------------------------------------------------------------------
    # 4. Item / freight totals & reconciliation
    # ------------------------------------------------------------------
    if items:
        item_total_brl: float | None = round(
            sum(i["price"] for i in items), 2
        )
        freight_total_brl: float | None = round(
            sum(i["freight_value"] for i in items), 2
        )
        expected_total_brl: float | None = round(
            item_total_brl + freight_total_brl, 2  # type: ignore[operator]
        )
        difference_brl: float | None = round(
            payment_total_brl - expected_total_brl, 2  # type: ignore[operator]
        )
        reconciled: bool | None = abs(difference_brl) <= 0.10  # type: ignore[arg-type]
    else:
        # No-item order → null per README §4
        item_total_brl = None
        freight_total_brl = None
        expected_total_brl = None
        difference_brl = None
        reconciled = None
        missing.append("no_item_rows_for_order")

    if not payments:
        missing.append("no_payment_rows_for_order")

    # ------------------------------------------------------------------
    # 5. Build Handoff
    # ------------------------------------------------------------------
    return {
        "case_id": case_id,
        "from_agent": "payment_agent",
        "to_agent": "coordinator",
        "task": "payment_reconciliation",
        "facts": {
            "currency": "BRL",
            "item_total_brl": item_total_brl,
            "freight_total_brl": freight_total_brl,
            "expected_total_brl": expected_total_brl,
            "payment_total_brl": payment_total_brl,
            "difference_brl": difference_brl,
            "reconciled": reconciled,
            "payment_types": payment_types,
            "payment_ids": payment_ids,
        },
        "evidence_ids": evidence_ids,
        "missing_or_conflicting_data": missing,
        "next_task": "policy_evaluation",
    }
