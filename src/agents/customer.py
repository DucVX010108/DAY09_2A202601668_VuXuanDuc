"""Customer history agent. Owned by Member 2."""

from typing import Any

MODEL_NAME = "gpt-4o-mini"


def investigate_customer(ticket: dict[str, Any], repository: Any) -> dict[str, Any]:
    """Return customer identity and at most five related order IDs.

    Rules:
    - Index/lookup by customer_unique_id.
    - Return up to 5 related order IDs in stable CSV order.
    - Do NOT include historical orders in affected_entities or evidence.
    """
    case_id = ticket.get("case_id", "")
    customer_req = ticket.get("customer_request", {})
    claimed_order_id = (
        customer_req.get("claimed_order_id")
        if isinstance(customer_req, dict)
        else None
    ) or ticket.get("claimed_order_id", "")

    missing_data: list[str] = []
    customer_unique_id: str | None = None
    related_order_ids: list[str] = []

    order = repository.get_order(claimed_order_id)
    if not order:
        missing_data.append(f"order_not_found:{claimed_order_id}")
    else:
        customer_id = order.get("customer_id")
        if not customer_id:
            missing_data.append(f"customer_id_missing_for_order:{claimed_order_id}")
        else:
            customer_unique_id = repository.get_customer_unique_id(customer_id)
            if not customer_unique_id:
                missing_data.append(f"customer_not_found:{customer_id}")
            else:
                related_order_ids = repository.get_related_orders(
                    customer_unique_id=customer_unique_id,
                    current_order_id=claimed_order_id,
                    limit=5,
                )

    facts = {
        "customer_unique_id": customer_unique_id,
        "related_order_ids": related_order_ids,
        "is_repeat_customer": len(related_order_ids) > 0,
    }

    return {
        "case_id": case_id,
        "from_agent": "customer",
        "to_agent": "coordinator",
        "task": "investigate_customer",
        "facts": facts,
        "evidence_ids": [],
        "missing_or_conflicting_data": missing_data,
        "next_task": "investigate_order_product",
    }
