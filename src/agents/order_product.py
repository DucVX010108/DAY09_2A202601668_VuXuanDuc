"""Order, seller and product context agent. Owned by Member 2."""

from typing import Any


def investigate_order_and_product(ticket: dict[str, Any], repository: Any) -> dict[str, Any]:
    """Return raw order/item/seller/product facts and valid evidence IDs.

    Limits & Rules:
    - Items: max 5, in source CSV order.
    - Sellers: max 3 unique, in source appearance order.
    - Products: max 5 unique, in source appearance order.
    - Categories: max 5 unique ``product_category_name`` values from the
      products CSV.  The output schema asks for the source category name; the
      translation table is not part of this field's contract.
    - Missing items: return empty arrays, report missing_or_conflicting_data.
    - Evidence IDs: order:<id>, item:<order_id>:<item_id>, seller:<seller_id>.
    """
    case_id = ticket.get("case_id", "")
    customer_req = ticket.get("customer_request", {})
    claimed_order_id = (
        customer_req.get("claimed_order_id")
        if isinstance(customer_req, dict)
        else None
    ) or ticket.get("claimed_order_id", "")

    missing_data: list[str] = []
    evidence_ids: list[str] = []

    order = repository.get_order(claimed_order_id)
    if not order:
        missing_data.append(f"order_not_found:{claimed_order_id}")

    # Primary order evidence ID if order exists
    if order:
        evidence_ids.append(f"order:{claimed_order_id}")

    raw_items = repository.get_order_items(claimed_order_id) if order else []

    items: list[dict[str, Any]] = []
    item_ids: list[str] = []
    seller_ids: list[str] = []
    product_ids: list[str] = []
    category_names: list[str] = []

    if order and not raw_items:
        missing_data.append(f"order_has_no_items:{claimed_order_id}")
    else:
        # 1. Items (max 5)
        selected_items = raw_items[:5]
        for it in selected_items:
            items.append(it)
            item_id_str = f"{claimed_order_id}:{it['order_item_id']}"
            item_ids.append(item_id_str)
            evidence_ids.append(f"item:{item_id_str}")

        # 2. Sellers (max 3 unique in source order)
        seen_sellers: set[str] = set()
        for it in raw_items:
            sid = it.get("seller_id")
            if sid and sid not in seen_sellers:
                seen_sellers.add(sid)
                seller_ids.append(sid)
                if len(seller_ids) == 3:
                    break

        for sid in seller_ids:
            evidence_ids.append(f"seller:{sid}")

        # 3. Products (max 5 unique in source order)
        seen_products: set[str] = set()
        for it in raw_items:
            pid = it.get("product_id")
            if pid and pid not in seen_products:
                seen_products.add(pid)
                product_ids.append(pid)
                if len(product_ids) == 5:
                    break

        # 4. Categories (max 5 unique in source order)
        seen_categories: set[str] = set()
        for pid in product_ids:
            product_data = repository.get_product(pid)
            if product_data:
                category_name = product_data.get("product_category_name")
                if category_name:
                    if category_name not in seen_categories:
                        seen_categories.add(category_name)
                        category_names.append(category_name)
                        if len(category_names) == 5:
                            break

    facts = {
        "order": order,
        "order_id": claimed_order_id,
        "items": items,
        "item_ids": item_ids,
        "seller_ids": seller_ids,
        "product_ids": product_ids,
        "category_names": category_names,
        "has_multiple_items": len(raw_items) >= 2,
        "has_multiple_sellers": len(seller_ids) >= 2,
        "has_multiple_categories": len(category_names) >= 2,
    }

    return {
        "case_id": case_id,
        "from_agent": "order_product",
        "to_agent": "coordinator",
        "task": "investigate_order_product",
        "facts": facts,
        "evidence_ids": evidence_ids,
        "missing_or_conflicting_data": missing_data,
        "next_task": "investigate_payment",
    }
