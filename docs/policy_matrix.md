# EC_POLICY_V2 test matrix

Owned by Member 4. Add a reproducible fixture and expected decision for every row before implementing the policy engine.

| Case | Required condition | Expected primary issue | Test status |
| --- | --- | --- | --- |
| P01 | Canceled order with payment total > 0 | `canceled_order_paid` | TODO |
| P02 | Unavailable order with payment total > 0 | `unavailable_order_paid` | TODO |
| P03 | Late delivery and at least one seller handoff after limit | `late_delivery_seller` | TODO |
| P04 | Late delivery with no late seller handoff | `late_delivery_logistics` | TODO |
| P05 | At least two payment rows and reconciled within 0.10 BRL | `valid_split_payment` | TODO |
| P06 | Delivery within estimate and reconciled payment | `unsupported_late_claim` | TODO |
| P07 | Multiple matching conditions | Highest-priority applicable issue | TODO |
| P08 | Missing source facts | Explicit validation failure; no invented decision | TODO |

