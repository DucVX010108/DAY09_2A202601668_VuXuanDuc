"""Deterministic EC_POLICY_V2 decision engine. Owned by Member 4.

Policy priority order (highest to lowest):
  1. canceled_order_paid
  2. unavailable_order_paid
  3. late_delivery_seller
  4. late_delivery_logistics
  5. valid_split_payment
  6. unsupported_late_claim

Any case that does not match a policy branch raises PolicyDecisionError
so that M1/M5 can handle it as a data error — no invented decisions.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLICY_VERSION = "EC_POLICY_V2"

# Map primary issue → root-cause code
_ROOT_CAUSE_MAP: dict[str, str] = {
    "canceled_order_paid": "ORDER_CANCELED_AFTER_PAYMENT",
    "unavailable_order_paid": "ORDER_UNAVAILABLE_AFTER_PAYMENT",
    "late_delivery_seller": "SELLER_HANDOFF_AFTER_LIMIT",
    "late_delivery_logistics": "CARRIER_DELIVERED_AFTER_ESTIMATE",
    "valid_split_payment": "MULTIPLE_PAYMENTS_RECONCILED",
    "unsupported_late_claim": "DELIVERY_WITHIN_ESTIMATE",
}

# Map primary issue → primary action
_PRIMARY_ACTION_MAP: dict[str, str] = {
    "canceled_order_paid": "issue_full_refund",
    "unavailable_order_paid": "issue_full_refund",
    "late_delivery_seller": "refund_freight",
    "late_delivery_logistics": "refund_freight",
    "valid_split_payment": "explain_valid_split_payment",
    "unsupported_late_claim": "reject_late_refund",
}

# Primary issues that require a refund (case_status = action_required)
_REFUND_ISSUES = frozenset(
    {"canceled_order_paid", "unavailable_order_paid", "late_delivery_seller", "late_delivery_logistics"}
)

# Required top-level keys that must be present in facts dict
_REQUIRED_FIELDS = (
    "order_id",
    "order_status",
    "payment_total_brl",
    "payment_count",
    "reconciled",
    "freight_total_brl",
    "late_handoff",
    "late_handoff_seller_ids",
    "item_count",
    "seller_count",
    "category_count",
    "related_order_ids",
    "evidence_ids",
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PolicyDecisionError(Exception):
    """Raised when facts are insufficient or ambiguous to reach a policy decision.

    M1/M5 must treat this as a data error — never invent a decision.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_required_fields(facts: dict[str, Any]) -> None:
    """Raise PolicyDecisionError if any required field is absent from facts."""
    for field in _REQUIRED_FIELDS:
        if field not in facts:
            raise PolicyDecisionError(f"missing_required_field: {field}")


def _resolve_primary_issue(facts: dict[str, Any]) -> str:
    """Apply EC_POLICY_V2 priority chain and return the matching primary issue.

    Raises PolicyDecisionError if no branch matches (data error, not fallback).
    """
    order_status: str = facts["order_status"]
    payment_total: float = facts["payment_total_brl"]
    delivery_variance: float | None = facts.get("delivery_variance_hours")
    late_handoff: bool | None = facts["late_handoff"]
    late_sellers: list = facts["late_handoff_seller_ids"]
    payment_count: int = facts["payment_count"]
    reconciled: bool | None = facts["reconciled"]

    # P1 — Canceled order that has been paid (highest priority)
    if order_status == "canceled" and payment_total > 0:
        return "canceled_order_paid"

    # P2 — Unavailable order that has been paid
    if order_status == "unavailable" and payment_total > 0:
        return "unavailable_order_paid"

    # P3 — Late delivery caused by seller(s)
    if (
        delivery_variance is not None
        and delivery_variance > 0
        and late_handoff is True
        and len(late_sellers) > 0
    ):
        return "late_delivery_seller"

    # P4 — Late delivery caused by logistics (no seller late)
    if (
        delivery_variance is not None
        and delivery_variance > 0
        and not late_handoff
    ):
        return "late_delivery_logistics"

    # P5 — Split payment that reconciles within 0.10 BRL tolerance
    if payment_count >= 2 and reconciled is True:
        return "valid_split_payment"

    # P6 — Order delivered on time with reconciled payment; late claim unsupported
    if (
        delivery_variance is not None
        and delivery_variance <= 0
        and reconciled is True
    ):
        return "unsupported_late_claim"

    # No branch matched — this is a data problem, not a policy gap
    ctx_parts = []
    if delivery_variance is None:
        ctx_parts.append("delivery_variance_hours=null")
    if reconciled is None:
        ctx_parts.append("reconciled=null")
    ctx = "; ".join(ctx_parts) if ctx_parts else "ambiguous state"
    raise PolicyDecisionError(f"no_policy_branch_matched: {ctx}")


def _resolve_root_cause(primary_issue: str) -> str:
    """Return root-cause code for a given primary issue."""
    return _ROOT_CAUSE_MAP[primary_issue]


def _resolve_responsible_parties(
    primary_issue: str,
    late_handoff_seller_ids: list[str],
) -> list[dict[str, str]]:
    """Return list of responsible party dicts (max 3 per README)."""
    if primary_issue in ("canceled_order_paid", "unavailable_order_paid"):
        return [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]

    if primary_issue == "late_delivery_seller":
        # One entry per late seller, capped at 3
        return [
            {"party_type": "seller", "party_id": sid}
            for sid in late_handoff_seller_ids[:3]
        ]

    if primary_issue == "late_delivery_logistics":
        return [{"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}]

    # valid_split_payment / unsupported_late_claim — no responsible party
    return []


def _resolve_refund(primary_issue: str, facts: dict[str, Any]) -> float:
    """Return recommended refund amount in BRL (0.00 if no refund)."""
    if primary_issue in ("canceled_order_paid", "unavailable_order_paid"):
        return round(float(facts["payment_total_brl"]), 2)

    if primary_issue in ("late_delivery_seller", "late_delivery_logistics"):
        return round(float(facts["freight_total_brl"]), 2)

    return 0.00


def _resolve_case_status(primary_issue: str) -> str:
    """Return 'action_required' if refund is needed, else 'no_action'."""
    return "action_required" if primary_issue in _REFUND_ISSUES else "no_action"


def _resolve_primary_action(primary_issue: str) -> str:
    """Return primary resolution action for the issue."""
    return _PRIMARY_ACTION_MAP[primary_issue]


def _resolve_secondary_issues(facts: dict[str, Any]) -> list[str]:
    """Return secondary issues in fixed business order.

    Order (per README):
      1. multi_item_order
      2. multi_seller_order
      3. split_payment
      4. repeat_customer
      5. multiple_categories
    """
    secondary: list[str] = []

    if facts["item_count"] >= 2:
        secondary.append("multi_item_order")

    if facts["seller_count"] >= 2:
        secondary.append("multi_seller_order")

    if facts["payment_count"] >= 2:
        secondary.append("split_payment")

    if len(facts["related_order_ids"]) > 0:
        secondary.append("repeat_customer")

    if facts["category_count"] >= 2:
        secondary.append("multiple_categories")

    return secondary


def _resolve_actions(
    primary_issue: str,
    secondary_issues: list[str],
) -> list[str]:
    """Build ordered action list: primary first, then additional in spec order.

    Additional actions order:
      1. review_seller_handoff / review_carrier_delay   (if applicable)
      2. verify_refund_completion                        (if refund issued)
      3. coordinate_multi_seller_case                   (if multi_seller_order)
      4. verify_payment_allocation                      (if split_payment AND primary ≠ valid_split_payment)
    """
    actions: list[str] = [_resolve_primary_action(primary_issue)]

    # 1. Delay review — mutually exclusive per primary issue
    if primary_issue == "late_delivery_seller":
        actions.append("review_seller_handoff")
    elif primary_issue == "late_delivery_logistics":
        actions.append("review_carrier_delay")

    # 2. Refund completion check
    if primary_issue in _REFUND_ISSUES:
        actions.append("verify_refund_completion")

    # 3. Multi-seller coordination
    if "multi_seller_order" in secondary_issues:
        actions.append("coordinate_multi_seller_case")

    # 4. Payment allocation — excluded when primary already explains split
    if "split_payment" in secondary_issues and primary_issue != "valid_split_payment":
        actions.append("verify_payment_allocation")

    return actions


def _build_evidence_ids(
    facts: dict[str, Any],
    root_cause_code: str,
) -> list[str]:
    """Assemble evidence list from M2/M3 provided IDs, appending policy evidence.

    Only real IDs from facts are used; policy:<root_cause_code> is appended last.
    Capped at 20 per README.
    """
    base: list[str] = list(facts.get("evidence_ids", []))
    policy_ev = f"policy:{root_cause_code}"
    if policy_ev not in base:
        base.append(policy_ev)
    return base[:20]


def _normalise_aggregated_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Accept Coordinator's typed aggregate without changing the flat M4 API."""

    if "order_product" not in facts or "payment" not in facts or "delivery" not in facts:
        return facts
    order_product = facts["order_product"]
    payment = facts["payment"]
    delivery = facts["delivery"]
    customer = facts["customer"]
    seller_analysis = delivery.get("seller_handoff_analysis", [])
    late_values = [entry.get("late_handoff") for entry in seller_analysis if isinstance(entry, dict)]
    late_handoff = True if any(value is True for value in late_values) else (None if any(value is None for value in late_values) else False)
    return {
        "order_id": order_product["order_id"],
        "order_status": order_product["order_status"],
        "payment_total_brl": payment["payment_total_brl"],
        "payment_count": payment["payment_count"],
        "reconciled": payment["reconciled"],
        "freight_total_brl": payment["freight_total_brl"],
        "late_handoff": late_handoff,
        "late_handoff_seller_ids": delivery["late_handoff_seller_ids"],
        "delivery_variance_hours": delivery["delivery_variance_hours"],
        "item_count": order_product["item_count"],
        "seller_count": order_product["seller_count"],
        "category_count": order_product["category_count"],
        "related_order_ids": customer["related_order_ids"],
        "evidence_ids": facts.get("source_evidence_ids", []),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_policy(facts: dict[str, Any]) -> dict[str, Any]:
    """Apply EC_POLICY_V2 to verified facts and return a policy decision dict.

    Parameters
    ----------
    facts:
        Merged facts dict produced by M2/M3 agents. Must contain all fields
        listed in ``_REQUIRED_FIELDS``. The key ``delivery_variance_hours`` is
        optional (may be ``None`` when timestamps are unavailable).

    Returns
    -------
    dict
        Policy decision object suitable for inclusion in the output schema.
        This function does **not** write any file.

    Raises
    ------
    PolicyDecisionError
        If required fields are missing or no policy branch can be determined.
        M1/M5 must log this and treat the case as a data error.
    """
    facts = _normalise_aggregated_facts(facts)
    _validate_required_fields(facts)

    primary_issue = _resolve_primary_issue(facts)
    root_cause = _resolve_root_cause(primary_issue)
    responsible_parties = _resolve_responsible_parties(
        primary_issue, facts["late_handoff_seller_ids"]
    )
    refund = _resolve_refund(primary_issue, facts)
    case_status = _resolve_case_status(primary_issue)
    secondary_issues = _resolve_secondary_issues(facts)
    actions = _resolve_actions(primary_issue, secondary_issues)
    evidence_ids = _build_evidence_ids(facts, root_cause)

    return {
        "policy_version": POLICY_VERSION,
        "primary_issue": primary_issue,
        "secondary_issues": secondary_issues,
        "case_status": case_status,
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": root_cause, "rank": 1}],
            "responsible_parties": responsible_parties,
        },
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": refund,
        },
        "resolution_actions": actions,
        "evidence_ids": evidence_ids,
    }
