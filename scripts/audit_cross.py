"""Independent cross-audit: recompute every output field from the CSVs per README.

Deliberately does NOT import src/ — it re-derives the expected output straight
from input/ + data/ so a bug in the pipeline cannot approve itself.

Run: python scripts/audit_cross.py
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA, INPUT, OUTPUT = ROOT / "data", ROOT / "input", ROOT / "output"
TS = "%Y-%m-%d %H:%M:%S"


def load(name, key, multi=False):
    rows = {}
    with open(DATA / name, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if multi:
                rows.setdefault(row[key], []).append(row)
            else:
                rows.setdefault(row[key], row)
    return rows


orders = load("olist_orders_dataset.csv", "order_id")
customers = load("olist_customers_dataset.csv", "customer_id")
items = load("olist_order_items_dataset.csv", "order_id", multi=True)
payments = load("olist_order_payments_dataset.csv", "order_id", multi=True)
products = load("olist_products_dataset.csv", "product_id")

# orders grouped by customer_unique_id, in orders-CSV order
by_unique: dict[str, list[str]] = {}
for oid, o in orders.items():
    cu = customers.get(o["customer_id"], {}).get("customer_unique_id")
    if cu:
        by_unique.setdefault(cu, []).append(oid)


def ts(v):
    v = (v or "").strip()
    return v or None


def dt(v):
    v = ts(v)
    return datetime.strptime(v, TS) if v else None


def hours(a, b):
    return round((a - b).total_seconds() / 3600, 2)


def uniq(seq):
    return list(dict.fromkeys(seq))


def expected(case_id: str, order_id: str) -> dict:
    o = orders[order_id]
    its = items.get(order_id, [])
    pays = payments.get(order_id, [])

    # --- delivery -------------------------------------------------------
    delivered, estimated = dt(o["order_delivered_customer_date"]), dt(o["order_estimated_delivery_date"])
    carrier = dt(o["order_delivered_carrier_date"])
    dvar = hours(delivered, estimated) if delivered and estimated else None

    handoff, late_sellers = [], []
    for sid in uniq(i["seller_id"] for i in its):
        limits = sorted(dt(i["shipping_limit_date"]) for i in its if i["seller_id"] == sid and dt(i["shipping_limit_date"]))
        limit = limits[0] if limits else None
        hvar = hours(carrier, limit) if carrier and limit else None
        late = hvar is not None and hvar > 0
        handoff.append({
            "seller_id": sid,
            "shipping_limit_at": limit.strftime(TS) if limit else None,
            "handoff_variance_hours": hvar,
            "late_handoff": late,
        })
        if late:
            late_sellers.append(sid)
    handoff = handoff[:3]
    late_sellers = [h["seller_id"] for h in handoff if h["late_handoff"]]

    # --- payment --------------------------------------------------------
    pay_total = round(sum(float(p["payment_value"]) for p in pays), 2)
    # README §4 names exactly three fields that go null for an item-less order:
    # expected_total_brl, difference_brl, reconciled. The two sums stay 0.00.
    item_total = round(sum(float(i["price"]) for i in its), 2)
    freight_total = round(sum(float(i["freight_value"]) for i in its), 2)
    if its:
        exp_total = round(item_total + freight_total, 2)
        diff = round(pay_total - exp_total, 2)
        reconciled = abs(diff) <= 0.10
    else:
        exp_total = diff = reconciled = None

    # --- policy (EC_POLICY_V2 priority order) ---------------------------
    status = o["order_status"]
    if status == "canceled" and pay_total > 0:
        issue = "canceled_order_paid"
    elif status == "unavailable" and pay_total > 0:
        issue = "unavailable_order_paid"
    elif dvar is not None and dvar > 0 and late_sellers:
        issue = "late_delivery_seller"
    elif dvar is not None and dvar > 0:
        issue = "late_delivery_logistics"
    elif len(pays) >= 2 and reconciled:
        issue = "valid_split_payment"
    elif dvar is not None and dvar <= 0 and reconciled:
        issue = "unsupported_late_claim"
    else:
        issue = "UNMATCHED"

    cause, action, party, refund = {
        "canceled_order_paid": ("ORDER_CANCELED_AFTER_PAYMENT", "issue_full_refund", ("platform", "OLIST_PLATFORM"), pay_total),
        "unavailable_order_paid": ("ORDER_UNAVAILABLE_AFTER_PAYMENT", "issue_full_refund", ("platform", "OLIST_PLATFORM"), pay_total),
        "late_delivery_seller": ("SELLER_HANDOFF_AFTER_LIMIT", "refund_freight", None, freight_total),
        "late_delivery_logistics": ("CARRIER_DELIVERED_AFTER_ESTIMATE", "refund_freight", ("logistics_provider", "LOGISTICS_PROVIDER"), freight_total),
        "valid_split_payment": ("MULTIPLE_PAYMENTS_RECONCILED", "explain_valid_split_payment", None, 0.0),
        "unsupported_late_claim": ("DELIVERY_WITHIN_ESTIMATE", "reject_late_refund", None, 0.0),
    }.get(issue, ("?", "?", None, 0.0))

    if issue == "late_delivery_seller":
        parties = [{"party_type": "seller", "party_id": s} for s in late_sellers[:3]]
    elif party:
        parties = [{"party_type": party[0], "party_id": party[1]}]
    else:
        parties = []

    seller_ids = uniq(i["seller_id"] for i in its)
    product_ids = uniq(i["product_id"] for i in its)
    cats = uniq(c for c in (products.get(p, {}).get("product_category_name") for p in product_ids) if c)
    cu = customers.get(o["customer_id"], {}).get("customer_unique_id")
    related = [x for x in by_unique.get(cu, []) if x != order_id][:5]

    secondary = []
    if len(its) >= 2:
        secondary.append("multi_item_order")
    if len(seller_ids) >= 2:
        secondary.append("multi_seller_order")
    if len(pays) >= 2:
        secondary.append("split_payment")
    if related:
        secondary.append("repeat_customer")
    if len(cats) >= 2:
        secondary.append("multiple_categories")

    actions = [action]
    if issue == "late_delivery_seller":
        actions.append("review_seller_handoff")
    elif issue == "late_delivery_logistics":
        actions.append("review_carrier_delay")
    if refund and refund > 0:
        actions.append("verify_refund_completion")
    if "multi_seller_order" in secondary:
        actions.append("coordinate_multi_seller_case")
    if "split_payment" in secondary and issue != "valid_split_payment":
        actions.append("verify_payment_allocation")

    evidence = [f"order:{order_id}"]
    evidence += [f"item:{order_id}:{i['order_item_id']}" for i in its[:5]]
    evidence += [f"payment:{order_id}:{p['payment_sequential']}" for p in pays[:5]]
    evidence += [f"seller:{p['party_id']}" for p in parties if p["party_type"] == "seller"]
    evidence.append(f"policy:{cause}")

    return {
        "case_id": case_id,
        "case_assessment": {
            "primary_issue": issue,
            "secondary_issues": secondary,
            "case_status": "action_required" if refund and refund > 0 else "no_action",
        },
        "affected_entities": {
            "order_ids": [order_id],
            "item_ids": [f"{order_id}:{i['order_item_id']}" for i in its[:5]],
            "seller_ids": seller_ids[:3],
            "payment_ids": [f"{order_id}:{p['payment_sequential']}" for p in pays[:5]],
        },
        "customer_context": {"customer_unique_id": cu, "related_order_ids": related},
        "product_context": {"product_ids": product_ids[:5], "category_names": cats[:5]},
        "delivery_analysis": {
            "delivered_at": ts(o["order_delivered_customer_date"]),
            "estimated_delivery_at": ts(o["order_estimated_delivery_date"]),
            "carrier_handoff_at": ts(o["order_delivered_carrier_date"]),
            "delivery_variance_hours": dvar,
            "seller_handoff_analysis": handoff,
            "late_handoff_seller_ids": late_sellers,
        },
        "payment_reconciliation": {
            "currency": "BRL",
            "item_total_brl": item_total,
            "freight_total_brl": freight_total,
            "expected_total_brl": exp_total,
            "payment_total_brl": pay_total,
            "difference_brl": diff,
            "reconciled": reconciled,
            "payment_types": uniq(p["payment_type"] for p in pays),
        },
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": cause, "rank": 1}],
            "responsible_parties": parties,
        },
        "evidence_ids": evidence[:20],
        "financial_resolution": {"currency": "BRL", "recommended_refund_brl": round(refund or 0.0, 2)},
        "resolution_actions": actions[:5],
        # audit-only context, stripped before diffing
        "_ctx": {"status": status, "n_items": len(its), "n_pays": len(pays), "n_sellers": len(seller_ids)},
    }


def diff(path, exp, got):
    """Yield (path, expected, actual) for every leaf mismatch."""
    if isinstance(exp, dict) and isinstance(got, dict):
        for k in exp.keys() | got.keys():
            yield from diff(f"{path}.{k}" if path else k, exp.get(k, "<missing>"), got.get(k, "<missing>"))
    elif isinstance(exp, list) and isinstance(got, list):
        if len(exp) != len(got):
            yield (path, exp, got)
        else:
            for n, (a, b) in enumerate(zip(exp, got)):
                yield from diff(f"{path}[{n}]", a, b)
    elif isinstance(exp, float) and isinstance(got, (int, float)):
        if abs(exp - got) > 0.011:
            yield (path, exp, got)
    elif exp != got:
        yield (path, exp, got)


def main() -> int:
    findings: dict[str, list] = {}
    ctxs = {}
    for f in sorted(INPUT.glob("EC_*.json")):
        ticket = json.loads(f.read_text(encoding="utf-8"))
        cid = ticket["case_id"]
        oid = ticket["customer_request"]["claimed_order_id"]
        if oid not in orders:
            findings.setdefault(cid, []).append(("<order_not_in_csv>", oid, None))
            continue
        exp = expected(cid, oid)
        ctxs[cid] = exp.pop("_ctx")
        out = OUTPUT / f.name
        if not out.exists():
            findings.setdefault(cid, []).append(("<output_missing>", str(out), None))
            continue
        got = json.loads(out.read_text(encoding="utf-8"))
        got.pop("confidence", None)
        got.get("case_assessment", {}).pop("confidence", None)
        d = list(diff("", exp, got))
        if d:
            findings[cid] = d

    print(f"cases audited: {len(ctxs)}   cases with mismatches: {len(findings)}\n")

    # group mismatches by field path so systemic bugs stand out from one-offs
    by_field: dict[str, list[str]] = {}
    for cid, ds in findings.items():
        for p, _, _ in ds:
            by_field.setdefault(p.split("[")[0], []).append(cid)
    if by_field:
        print("=== mismatches grouped by field ===")
        for field, cids in sorted(by_field.items(), key=lambda kv: -len(kv[1])):
            print(f"{len(cids):3d}  {field:52s} {' '.join(sorted(set(cids))[:8])}")
        print()

    print("=== per-case detail ===")
    for cid in sorted(findings):
        print(f"\n-- {cid}  {ctxs.get(cid, {})}")
        for p, e, g in findings[cid][:12]:
            print(f"   {p}\n     expected: {e!r}\n     actual  : {g!r}")

    # distribution sanity
    print("\n=== issue distribution (independent recompute) ===")
    dist: dict[str, int] = {}
    for f in sorted(INPUT.glob("EC_*.json")):
        t = json.loads(f.read_text(encoding="utf-8"))
        oid = t["customer_request"]["claimed_order_id"]
        if oid in orders:
            e = expected(t["case_id"], oid)
            dist[e["case_assessment"]["primary_issue"]] = dist.get(e["case_assessment"]["primary_issue"], 0) + 1
    for k, v in sorted(dist.items(), key=lambda kv: -kv[1]):
        print(f"  {v:3d}  {k}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
