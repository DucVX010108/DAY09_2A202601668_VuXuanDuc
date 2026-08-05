# EC_POLICY_V2 — Policy Decision Matrix

Owned by Member 4. Mỗi row là một fixture deterministic với input cụ thể và expected output.  
Engine phải match chính xác — không có nhánh fallback tự bịa.

---

## Bảng mapping nhanh

| Primary issue | Root-cause code | Refund | case_status | Primary action |
|---|---|---|---|---|
| `canceled_order_paid` | `ORDER_CANCELED_AFTER_PAYMENT` | Tổng payment | `action_required` | `issue_full_refund` |
| `unavailable_order_paid` | `ORDER_UNAVAILABLE_AFTER_PAYMENT` | Tổng payment | `action_required` | `issue_full_refund` |
| `late_delivery_seller` | `SELLER_HANDOFF_AFTER_LIMIT` | Tổng freight | `action_required` | `refund_freight` |
| `late_delivery_logistics` | `CARRIER_DELIVERED_AFTER_ESTIMATE` | Tổng freight | `action_required` | `refund_freight` |
| `valid_split_payment` | `MULTIPLE_PAYMENTS_RECONCILED` | 0 | `no_action` | `explain_valid_split_payment` |
| `unsupported_late_claim` | `DELIVERY_WITHIN_ESTIMATE` | 0 | `no_action` | `reject_late_refund` |

---

## Ma trận test đầy đủ

| Case | Điều kiện input | Expected primary issue | Expected root cause | Expected refund BRL | Expected case_status | Test status |
|---|---|---|---|---|---|---|
| **P01** | `order_status=canceled`, `payment_total=150.00 > 0` | `canceled_order_paid` | `ORDER_CANCELED_AFTER_PAYMENT` | 150.00 | `action_required` | ✅ PASS |
| **P02** | `order_status=unavailable`, `payment_total=80.50 > 0` | `unavailable_order_paid` | `ORDER_UNAVAILABLE_AFTER_PAYMENT` | 80.50 | `action_required` | ✅ PASS |
| **P03** | `canceled` + `delivery_variance=+72h` + `late_handoff=True` | `canceled_order_paid` (P1 thắng P3) | `ORDER_CANCELED_AFTER_PAYMENT` | payment_total | `action_required` | ✅ PASS |
| **P04** | `canceled` + `payment_count=2` + `reconciled=True` | `canceled_order_paid` (P1 thắng P5) | `ORDER_CANCELED_AFTER_PAYMENT` | payment_total | `action_required` | ✅ PASS |
| **P05** | `unavailable` + `delivery_variance=+48h` + `late_handoff=True` | `unavailable_order_paid` (P2 thắng P3) | `ORDER_UNAVAILABLE_AFTER_PAYMENT` | payment_total | `action_required` | ✅ PASS |
| **P06** | `order_status=delivered`, `delivery_variance=+36h`, `late_handoff=True`, `late_handoff_seller_ids=[seller_A]` | `late_delivery_seller` | `SELLER_HANDOFF_AFTER_LIMIT` | freight_total | `action_required` | ✅ PASS |
| **P07** | `order_status=delivered`, `delivery_variance=+24h`, `late_handoff=False`, `late_handoff_seller_ids=[]` | `late_delivery_logistics` | `CARRIER_DELIVERED_AFTER_ESTIMATE` | freight_total | `action_required` | ✅ PASS |
| **P08** | `delivered`, `variance=+10h`, `late_handoff=True` vs `late_handoff=False` (2 fixture) | P06→`late_delivery_seller` / P07→`late_delivery_logistics` | Khác nhau theo flag | freight | `action_required` | ✅ PASS |
| **P09** | `payment_count=3`, `reconciled=True`, `delivery_variance=-5h` (on-time) | `valid_split_payment` | `MULTIPLE_PAYMENTS_RECONCILED` | 0 | `no_action` | ✅ PASS |
| **P10** | `payment_count=2`, `difference_brl=0.10`, `reconciled=True` (đúng biên tolerance) | `valid_split_payment` | `MULTIPLE_PAYMENTS_RECONCILED` | 0 | `no_action` | ✅ PASS |
| **P11** | `payment_count=2`, `difference_brl=0.11`, `reconciled=False` (vượt tolerance) | Không khớp nhánh nào → `PolicyDecisionError("no_policy_branch_matched")` | — | — | data error | ✅ PASS |
| **P12** | `delivery_variance=-2h` (on-time), `reconciled=True`, `payment_count=1` | `unsupported_late_claim` | `DELIVERY_WITHIN_ESTIMATE` | 0 | `no_action` | ✅ PASS |
| **P13** | `item_count=3`, `seller_count=2`, `payment_count=2`, `related_orders=1`, `category_count=2` | secondary: `multi_item_order, multi_seller_order, split_payment, repeat_customer, multiple_categories` (đúng thứ tự) | — | — | — | ✅ PASS |
| **P14** | `late_delivery_seller` + `seller_count=2` + `payment_count=2` + split | actions: `refund_freight, review_seller_handoff, verify_refund_completion, coordinate_multi_seller_case, verify_payment_allocation` | — | freight | `action_required` | ✅ PASS |
| **P15** | `valid_split_payment` + `payment_count=3` + secondary includes `split_payment` | actions: `explain_valid_split_payment` **không có** `verify_payment_allocation` | — | 0 | `no_action` | ✅ PASS |
| **P16** | Facts thiếu field bắt buộc (ví dụ thiếu `order_status`) | `PolicyDecisionError` với message chứa tên field | — | — | — | ✅ PASS |
| **P17** | `delivery_variance_hours=None`, `order_status=delivered`, không canceled/unavailable | `PolicyDecisionError("no_policy_branch_matched")` | — | — | — | ✅ PASS |

---

## Thứ tự secondary issues (bất biến)

```
1. multi_item_order      — item_count >= 2
2. multi_seller_order    — seller_count >= 2
3. split_payment         — payment_count >= 2
4. repeat_customer       — len(related_order_ids) > 0
5. multiple_categories   — category_count >= 2
```

## Thứ tự additional actions (sau primary action)

```
1. review_seller_handoff    — chỉ khi primary = late_delivery_seller
   review_carrier_delay     — chỉ khi primary = late_delivery_logistics
2. verify_refund_completion — khi có refund (canceled/unavailable/late delivery)
3. coordinate_multi_seller_case — khi multi_seller_order trong secondary
4. verify_payment_allocation    — khi split_payment trong secondary VÀ primary ≠ valid_split_payment
```

## Missing data policy

- Field bắt buộc thiếu → `PolicyDecisionError("missing_required_field: <field_name>")`
- Không khớp nhánh nào → `PolicyDecisionError("no_policy_branch_matched: <mô tả ngữ cảnh>")`
- **Không bao giờ** trả output với primary_issue giả tạo

## Source-field semantics used by the grader

- `product_context.category_names` dùng trực tiếp `products.product_category_name`; không thay bằng bản dịch tiếng Anh.
- Nếu thiếu carrier timestamp hoặc shipping limit thì `handoff_variance_hours = null` và `late_handoff = false`.
- `late_handoff_seller_ids` chỉ chứa seller có variance xác định và lớn hơn 0.
- Policy evidence luôn là phần tử cuối của `evidence_ids`, kể cả khi phải cắt danh sách ở giới hạn 20.
