# Member Role Report — Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin       | Nội dung                         |
| --------------- | -------------------------------- |
| Họ và tên       | Vũ Xuân Đức                      |
| MSSV            | 2A202601668                      |
| Khóa/Lớp        | K4                               |
| Vai trò chính   | Policy Agent và test matrix |
| Ngày hoàn thành | 2026-08-05                       |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| ------------------ | ------------------ | -------------- | --------------- | ---------- |
| Policy Engine (EC_POLICY_V2) | `src/policy.py` — `apply_policy()`, `_resolve_primary_issue()`, `_resolve_secondary_issues()`, `_resolve_actions()`, `_build_evidence_ids()`, `_normalise_aggregated_facts()` | Merged facts dict từ M2/M3 (order_status, payment facts, delivery facts, customer facts, evidence_ids thô) | Policy decision object (primary_issue, secondary_issues, case_status, root_cause_analysis, financial_resolution, resolution_actions, evidence_ids) — **chưa ghi file** | Hoàn thành |
| Test matrix | `tests/test_policy.py` — 17 test cases P01–P17 | Synthetic facts fixtures (không cần CSV) | 17/17 test PASS | Hoàn thành |
| Tài liệu policy | `docs/policy_matrix.md` | Đặc tả EC_POLICY_V2 trong README | Bảng mapping 6 primary issues + ma trận test đầy đủ 17 fixtures + ngữ nghĩa source field | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --------- | ----------------------------- | ------- |
| Trao đổi contract để M1 có thể truyền aggregate dict trực tiếp sang Policy Engine mà không cần flatten thủ công | M1 (coordinator.py) | Hàm `_normalise_aggregated_facts()` được tích hợp trong `policy.py`; M1 truyền thẳng output aggregate, không cần bước trung gian |
| Cung cấp format `payment:<order_id>:<payment_sequential>` để M3 biết cách sinh evidence ID hợp lệ | M3 (payment.py) | M3 áp dụng đúng format; `_build_evidence_ids()` của M4 nhận và dedup bình thường |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --------------------- | --------------------------- | ---------------- | ------------- |
| Cài đặt 6 nhánh policy theo đúng thứ tự ưu tiên EC_POLICY_V2 | `src/policy.py` — `_resolve_primary_issue()` | Hàm trả đúng primary issue hoặc raise `PolicyDecisionError` nếu không khớp nhánh nào | `pytest tests/test_policy.py -v` — P01–P08, P11, P12, P16, P17 |
| Xây dựng bảng mapping `POLICY_OUTCOMES` (root cause, refund, responsible party, action) | `src/policy.py` — dict `POLICY_OUTCOMES` | Mỗi primary issue → 1 bộ hậu quả cố định, không bị drift | So sánh với bảng mapping trong `docs/policy_matrix.md` |
| Sinh secondary issues theo thứ tự cố định | `src/policy.py` — `_resolve_secondary_issues()` | List đúng thứ tự: multi_item_order → multi_seller_order → split_payment → repeat_customer → multiple_categories | `pytest tests/test_policy.py::test_p13_secondary_issues_order -v` |
| Sinh action list đúng thứ tự và loại action khi cần | `src/policy.py` — `_resolve_actions()` | Actions đúng thứ tự; loại `verify_payment_allocation` khi primary là `valid_split_payment` | `pytest tests/test_policy.py::test_p14_actions_late_seller_multi_seller -v` và P15 |
| Validate consistency facts đầu vào | `src/policy.py` — `_validate_required_fields()`, `_validate_consistency()` | Raise `PolicyDecisionError` với message rõ field bị thiếu/mâu thuẫn | `pytest tests/test_policy.py::test_p16_missing_required_field -v` |
| Lập tài liệu policy matrix | `docs/policy_matrix.md` | Bảng mapping + ma trận 17 fixtures + ngữ nghĩa source field | Đối chiếu trực tiếp với README |

**Output cụ thể:** `apply_policy()` là contract bắt buộc giữa M4 và M5. Ví dụ với `canceled_order_paid`:

```json
{
  "policy_version": "EC_POLICY_V2",
  "primary_issue": "canceled_order_paid",
  "secondary_issues": ["split_payment"],
  "case_status": "action_required",
  "root_cause_analysis": {
    "ranked_causes": [{"cause_code": "ORDER_CANCELED_AFTER_PAYMENT", "rank": 1}],
    "responsible_parties": [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
  },
  "financial_resolution": {
    "currency": "BRL",
    "recommended_refund_brl": 150.00
  },
  "resolution_actions": ["issue_full_refund", "verify_refund_completion"],
  "evidence_ids": ["order:abc", "payment:abc:1", "policy:ORDER_CANCELED_AFTER_PAYMENT"]
}
```

M5 recompute `primary_issue` và `recommended_refund_brl` độc lập để xác minh trước khi `verified=true`.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Policy Engine phải áp dụng đúng 6 nhánh EC_POLICY_V2 theo thứ tự ưu tiên cố định, dựa hoàn toàn vào facts từ M2/M3. Thách thức cốt lõi: cùng một đơn có thể thỏa nhiều điều kiện (vừa bị cancel vừa giao trễ vừa split payment), nhưng chỉ được chọn đúng **một** primary issue theo thứ tự P1–P6. Nếu không khớp nhánh nào (dữ liệu thiếu hoặc trạng thái bất định), engine phải **raise error** thay vì tự bịa quyết định.

### Cách triển khai

**Priority chain trong `_resolve_primary_issue()`:**

Engine kiểm tra 6 điều kiện tuần tự, trả về issue đầu tiên thỏa mãn:

1. `canceled_order_paid`: `order_status == "canceled"` AND `payment_total > 0`
2. `unavailable_order_paid`: `order_status == "unavailable"` AND `payment_total > 0`
3. `late_delivery_seller`: `delivery_variance > 0` AND `late_handoff == True` AND `len(late_sellers) > 0`
4. `late_delivery_logistics`: `delivery_variance > 0` AND `late_handoff == False`
5. `valid_split_payment`: `payment_count >= 2` AND `reconciled == True`
6. `unsupported_late_claim`: `delivery_variance <= 0` AND `reconciled == True`

Nếu không nhánh nào khớp → `raise PolicyDecisionError("no_policy_branch_matched: <context>")`

**Bảng `POLICY_OUTCOMES` — single source of truth:**

Thay vì viết rải rác root cause / refund / action trong nhiều hàm, toàn bộ hậu quả cố định của mỗi primary issue được đặt trong một `dict[str, PolicyOutcome]` duy nhất. Điều này đảm bảo 4 thành phần (root_cause, primary_action, refund_source, responsible_party) không bao giờ drift khỏi nhau.

**Tolerant input — `_normalise_aggregated_facts()`:**

Coordinator truyền aggregate dict có sub-keys (`order_product`, `payment`, `delivery`, `customer`). Thay vì yêu cầu M1 flatten trước, policy engine tự detect và flatten nội bộ. Backward-compatible với cả flat dict (dùng trong tests) lẫn aggregated dict (dùng trong batch).

**Evidence integrity:**

`_build_evidence_ids()` dedup bằng `dict.fromkeys()` (giữ thứ tự nguồn), cắt tại 19 slot, sau đó append `policy:<root_cause_code>` vào cuối — policy evidence luôn là phần tử cuối và không vượt quá 20 phần tử.

### Input, output và contract

| Thành phần | Mô tả |
| ---------- | ----- |
| Input | `facts: dict` — flat hoặc aggregated; các field bắt buộc: `order_id`, `order_status`, `payment_total_brl`, `payment_count`, `reconciled`, `freight_total_brl`, `late_handoff`, `late_handoff_seller_ids`, `item_count`, `seller_count`, `category_count`, `related_order_ids`, `evidence_ids`; optional: `delivery_variance_hours` (None nếu không có timestamp) |
| Output | `dict` — policy decision object gồm `policy_version`, `primary_issue`, `secondary_issues`, `case_status`, `root_cause_analysis`, `financial_resolution`, `resolution_actions`, `evidence_ids`; **không ghi file** |
| Module phụ thuộc | M2 (`customer.py`, `order_product.py`) và M3 (`payment.py`, `delivery.py`) — cung cấp facts và evidence IDs thô |
| Module sử dụng output | M1 (`coordinator.py`) — nhận decision object để chuyển sang M5; M5 (`verifier.py`) — recompute và xác minh |
| Điều kiện lỗi cần xử lý | Field bắt buộc thiếu → `PolicyDecisionError("missing_required_field: <field>")`; `late_handoff` không nhất quán với `late_handoff_seller_ids` → `PolicyDecisionError("conflicting_fields: ...")`; không khớp nhánh nào → `PolicyDecisionError("no_policy_branch_matched: <context>")` |

### Cách xác minh

```bash
pytest tests/test_policy.py -v
```

- **Kết quả mong đợi:** 17 test cases (P01–P17) tất cả PASS, bao phủ mọi nhánh policy, precedence rule, secondary issues, action ordering, tolerance 0.10 BRL và missing data.
- **Kết quả thực tế:** 17 PASSED, 0 FAILED, 0 ERROR.
- **Artifact/log:** Output `pytest` trong terminal; không có secret trong test fixtures.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Coordinator tổng hợp output từ 4 agent thành aggregate dict có nested sub-keys. Policy Engine cần nhận facts từ Coordinator, nhưng test fixtures của M4 dùng flat dict thuần túy. Nếu Policy Engine chỉ chấp nhận một định dạng, hoặc M1 phải flatten thủ công, hoặc tests M4 phải mock toàn bộ cấu trúc aggregate.
- **Các phương án đã cân nhắc:**
  1. **Yêu cầu M1 flatten trước khi gọi `apply_policy()`** — đơn giản cho M4 nhưng tăng coupling, M1 phải biết chi tiết field của từng agent.
  2. **Policy Engine tự detect và flatten nội bộ qua `_normalise_aggregated_facts()`** — transparent với caller, backward-compatible.
- **Phương án đã chọn:** Phương án 2 — `_normalise_aggregated_facts()` là bước đầu tiên trong `apply_policy()`.
- **Lý do:** Giảm coupling giữa M1 và M4; tests của M4 vẫn dùng flat dict mà không cần mock coordinator; M1 truyền thẳng aggregate. Logic flatten nằm trong M4 như nội bộ module, không ảnh hưởng public contract.
- **Bằng chứng quyết định phù hợp:** 17 test cases dùng flat dict vẫn PASS sau khi thêm `_normalise_aggregated_facts()`; batch qua Coordinator với aggregate dict cũng cho output đúng.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** Test P15 (`valid_split_payment` + `split_payment` trong secondary) bị fail: action list chứa `verify_payment_allocation`, trong khi spec yêu cầu action này phải bị loại khi primary là `valid_split_payment`.
- **Lệnh hoặc bước tái hiện:**
  ```bash
  pytest tests/test_policy.py::test_p15_valid_split_payment_no_verify_allocation -v
  ```
  Output: `AssertionError: 'verify_payment_allocation' in actions`
- **Nguyên nhân gốc:** Trong `_resolve_actions()`, điều kiện thêm `verify_payment_allocation` chỉ kiểm tra `"split_payment" in secondary_issues` mà chưa có guard `primary_issue != "valid_split_payment"`. Khi primary là `valid_split_payment`, secondary vẫn chứa `split_payment` (vì `payment_count >= 2`), dẫn đến action bị thêm nhầm.
- **Cách xử lý:** Sửa điều kiện trong `_resolve_actions()`:
  ```python
  if "split_payment" in secondary_issues and primary_issue != "valid_split_payment":
      actions.append("verify_payment_allocation")
  ```
- **Cách xác minh sau khi sửa:**
  ```bash
  pytest tests/test_policy.py -v
  ```
  Kết quả: 17 PASSED.
- **Điều học được:** Khi primary issue đã giải thích trực tiếp một secondary symptom, action liên quan đến symptom đó phải bị suppress. Pattern "suppress additional action when primary already addresses it" cần được test tường minh từ đầu thiết kế, không nên để phát hiện muộn khi integration.

## 7. Hiểu biết về luồng end-to-end

**1. Ticket K4 được Coordinator chuyển qua các agent như thế nào?**

Coordinator (`src/coordinator.py`) đọc từng file `input/EC_XXX.json`, lấy `claimed_order_id`, rồi gọi tuần tự: Customer Agent → Order&Product Agent → Payment Agent → Delivery Agent. Mỗi agent trả facts dict với `evidence_ids` thô. Coordinator merge thành aggregated dict rồi gọi `apply_policy()` (M4). Sau khi nhận policy decision, Coordinator chuyển toàn bộ sang Verifier (M5). Chỉ sau khi `verified=true`, output mới được ghi vào `output/EC_XXX.json`.

**2. Vì sao Verifier phải tái tính thay vì tin Policy Agent?**

Policy Agent là module deterministic nhưng không đọc CSV trực tiếp — chỉ nhận facts từ M2/M3. Nếu M2/M3 truyền facts sai, Policy Agent vẫn ra quyết định nhất quán nhưng dựa trên dữ liệu lỗi. Verifier (M5) có quyền truy cập raw index và recompute độc lập: tính lại `payment_total_brl`, `delivery_variance_hours`, xác định `primary_issue` từ điều kiện gốc, recompute `recommended_refund_brl`. Nếu không khớp → reject, không ghi output. Đây là defense-in-depth: M4 không phải nguồn sự thật cuối cùng.

**3. Evidence ID nào được phép xuất hiện và cách kiểm chứng từ CSV?**

Chỉ evidence ID tồn tại trong CSV mới được phép:
- `order:<order_id>` — tra `order_id` trong `olist_orders_dataset.csv`
- `item:<order_id>:<item_id>` — tra `order_id` + `order_item_id` trong `olist_order_items_dataset.csv`
- `seller:<seller_id>` — tra `seller_id` trong `olist_sellers_dataset.csv`
- `payment:<order_id>:<payment_sequential>` — tra `order_id` + `payment_sequential` trong `olist_order_payments_dataset.csv`
- `policy:<root_cause_code>` — được thêm bởi M4, luôn là phần tử cuối; không phải row CSV nhưng được phép vì là policy identifier

**4. Trace JSONL cần thể hiện những handoff/event nào để audit được một case?**

Mỗi case trong `logging/trace.jsonl` phải có: `(1) input_received` — Coordinator nhận ticket; `(2) agent_handoff` từ mỗi agent (Customer, Order&Product, Payment, Delivery) với các field `from_agent`, `to_agent`, `task`, `facts`, `evidence_ids`, `missing_or_conflicting_data`, `next_task`; `(3) policy_decision` — M4 trả policy object; `(4) verifier_result` — M5 xác nhận `verified=true/false` kèm lý do; `(5) output_written` hoặc `output_blocked`. Trace phải có `run_id` và không chứa secret.

**5. Điều kiện nào cho phép ghi output và điều kiện nào cho phép đóng gói ZIP?**

Output được ghi khi Verifier trả `verified=true` cho case đó (recompute khớp với policy decision). Nếu `verified=false`, Coordinator log lỗi và không ghi file — không được ghi decision chưa được xác minh. ZIP được tạo khi: tất cả 50 case xử lý xong, 50 file output tồn tại trong `output/`, Verifier pass 50/50. ZIP chỉ chứa 50 file `EC_001.json`…`EC_050.json` ở cấp gốc, không chứa source code, `.env`, cache, virtual environment hoặc file ngoài phạm vi.

## 8. Cam kết của thành viên

Đánh dấu sau khi tự kiểm tra:

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi "đã chạy thành công" cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Vũ Xuân Đức
**Ngày xác nhận:** 2026-08-05
