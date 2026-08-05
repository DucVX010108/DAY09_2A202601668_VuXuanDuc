# K4 Multi-Agent E-commerce Dispute Resolution

## Mục tiêu và nguyên tắc

Pipeline xử lý đúng 50 ticket đã phát hành trong `input/` theo `EC_POLICY_V2`. CSV là nguồn sự thật duy nhất; mọi số tiền, thời gian, entity ID và evidence phải được truy xuất từ dữ liệu nguồn. Hệ thống không tạo refund ledger, transaction ID hoặc tracking checkpoint vì Olist không có các dữ liệu này.

Quyết định chấm điểm được thực hiện bằng rule xác định. Nếu sau này có model để tóm tắt nội dung tiếng Việt, model đó chỉ là lớp diễn giải ngoài decision path, phải được khai báo trong source/metadata và thỏa giới hạn ≤10B của đề.

## Luồng xử lý

```text
input/EC_xxx.json
        │
        ▼
Coordinator ──► Input validation
        │
        ├──► Customer Agent
        ├──► Order & Product Agent
        ├──► Payment Agent
        └──► Delivery Agent
                 │
                 ▼
            Policy Agent
                 │
                 ▼
            Verifier Agent ──fail──► trace + owner module
                 │ pass
                 ▼
        output/EC_xxx.json + trace.jsonl
```

Coordinator chỉ ghi output khi Verifier trả danh sách lỗi rỗng. `AtomicTraceSink` trong batch ghi vào file tạm rồi thay thế trace cũ ở cuối run, nên không append history cũ.

## Vai trò, quyền truy cập và ownership

| Thành phần | Ownership | Đọc | Ghi | Trách nhiệm |
| --- | --- | --- | --- | --- |
| Coordinator | M1 | `input/`, handoff, repository | `output/`, input-validation log | Điều phối một case và chặn output không pass. |
| Customer Agent | M2 | customers, orders | handoff | Customer unique ID và tối đa 5 related orders. |
| Order & Product Agent | M2 | orders, items, products, sellers | handoff | Entity chính, `product_category_name` gốc và raw evidence. |
| Payment Agent | M3 | payments, items | handoff | Reconciliation từ raw rows. |
| Delivery Agent | M3 | orders, items | handoff | Delivery/handoff variance từ timestamp CSV. |
| Policy Agent | M4 | merged facts | policy handoff | Áp đúng precedence `EC_POLICY_V2`. |
| Verifier + Trace + Package | M5 | output candidate, merged facts, repository | trace, metadata, ZIP | Tái tính, kiểm tra hard gate, audit trail và đóng gói. |

## Handoff contract

Mọi handoff đi qua Coordinator dùng envelope trong `src/contracts.Handoff`. Các module specialist giữ API riêng nhưng được adapter của Coordinator chuẩn hoá thành envelope này trước khi facts được tổng hợp:

```json
{
  "case_id": "EC_001",
  "from_agent": "payment",
  "to_agent": "policy",
  "task": "reconcile_payment",
  "facts": {},
  "evidence_ids": [],
  "missing_or_conflicting_data": [],
  "next_task": "apply_policy"
}
```

Coordinator hợp nhất các `facts` thành object chuẩn để policy/verifier sử dụng:

```text
facts = {
  case_id, order, items[], payments[], customer{customer_unique_id, related_order_ids[]},
  products[], category_names[], policy_decision
}
```

`order`, `items` và `payments` là raw row CSV đã parse; danh sách giữ thứ tự nguồn. `category_names` lấy trực tiếp từ `products.product_category_name`, không dịch sang `product_category_name_english`. Khi không tính được handoff variance do thiếu timestamp, variance là `null` nhưng `late_handoff` là `false` vì không có bằng chứng handoff trễ. Không agent nào được bỏ qua field thiếu bằng giá trị suy đoán: trường hợp đó phải ghi vào `missing_or_conflicting_data`.

`OlistRepository` phải cung cấp các predicate read-only cho Verifier:

```text
has_order(order_id)
has_item(order_id, order_item_id)
has_payment(order_id, payment_sequential)
has_seller(seller_id)
```

## Decision và validation flow

Policy Agent và Verifier đều áp precedence sau; Verifier tái tính để phát hiện policy output sai:

```text
canceled_order_paid
→ unavailable_order_paid
→ late_delivery_seller
→ late_delivery_logistics
→ valid_split_payment
→ unsupported_late_claim
```

Secondary issues cố định thứ tự:

```text
multi_item_order → multi_seller_order → split_payment
→ repeat_customer → multiple_categories
```

Verifier tự đọc lại customer, product/category, item và payment từ repository thay vì dùng projection của specialist. Nó kiểm tra: output schema/type/enum, giới hạn array, timestamp/null handling, raw entity/evidence IDs, payment and delivery calculations, policy/root cause/responsibility/refund/actions và consistency giữa fields. Errors có prefix `schema`, `limit`, `reference`, `calculation`, `policy` hoặc `consistency` để Coordinator trả đúng owner module.

## Trace và failure handling

Mỗi dòng `logging/trace.jsonl` là JSON event có `run_id`, `case_id`, `sequence`, `event_type`, `status` và context handoff. Event tối thiểu:

```text
batch_started → case_started → handoff* (bao gồm Policy) → verification_passed|verification_failed
→ case_finished → batch_finished → output_publish (chỉ khi toàn bộ case pass)
```

Trace redacts value của key chứa `api_key`, `authorization`, `token` hoặc `secret`. Nếu verifier fail, Coordinator không ghi candidate vào `output/`; trace ghi error codes và case được trả về module sở hữu logic lỗi.

## Chạy và đóng gói

```powershell
.venv\Scripts\activate
python -m pytest
python -m src.run_batch
.\scripts\package_submission.ps1
```

Script đóng gói chỉ tạo ZIP khi có chính xác `output/EC_001.json` đến `output/EC_050.json`. ZIP chỉ chứa 50 JSON dưới thư mục `output/`; source, trace, metadata, `.env`, cache và virtual environment không được đưa vào ZIP.
