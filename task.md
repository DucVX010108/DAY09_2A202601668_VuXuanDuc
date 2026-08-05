# Phân công nhóm 5 người — Day 09 K4 Multi-Agent A2A

## Quy ước chung

- Cohort duy nhất: **K4**. Chỉ dùng `EC_POLICY_V2` trong `README.md`; không sao chép logic, schema hoặc policy K3.
- Bộ 50 input đã được phát hành trong `input/` và là nguồn yêu cầu bất biến. Chỉ dùng `claimed_order_id` từ các file này để tra `data/`; không tạo, sửa hay thay thế ticket.
- Quy ước nguồn sự thật: CSV → facts agent → policy engine → verifier → `output/`. LLM (nếu có) không được dùng để tính tiền, thời gian hay thay thế policy rule.
- Mọi handoff phải có: `case_id`, `from_agent`, `to_agent`, `task`, `facts`, `evidence_ids`, `missing_or_conflicting_data`, `next_task`.
- Cùng dùng một branch/repo nhưng mỗi người chỉ sửa các file thuộc ownership của mình. Không sửa tay JSON để qua validator.
- Tất cả mốc thời gian dùng đúng format CSV. Tiền và số giờ làm tròn hai chữ số thập phân.

## Luồng tích hợp

```text
Data CSV
  + input/EC_001.json … input/EC_050.json
  → [M2/M3/M4] các agent facts
  → [M1] coordinator + [M4] policy engine
  → [M5] verifier
  → output/ + logging/trace.jsonl + tài liệu
```

`M1` tích hợp cuối; các module không đọc output của module khác mà chỉ nhận/trả contract dữ liệu đã thống nhất.

## Thành viên 1 — Coordinator, kiểm tra input và tích hợp

**Ownership:** `src/coordinator.py`, `src/contracts.py`, `src/run_batch.py`, `logging/input_validation.json`.

1. Tạo contract dataclass/JSON cho ticket, handoff và run result; quy định field bắt buộc và thứ tự serialization ổn định.
2. Kiểm tra đầu vào có đúng 50 file `EC_001` … `EC_050`, `case_id` duy nhất và khớp tên file, `policy_version = EC_POLICY_V2`, hai scope K4 là `true`, và mỗi `claimed_order_id` tồn tại trong `olist_orders_dataset.csv`.
3. Ghi kết quả kiểm tra vào `logging/input_validation.json`; không ghi đè file trong `input/`. Input hiện tại đã xác nhận đủ 50 case ID, 50 order ID duy nhất, ngôn ngữ `vi` và policy V2; script vẫn phải kiểm tra lại ở mỗi run.
4. Dùng ticket phát hành để điều phối các agent và bảo đảm mỗi `claimed_order_id` chỉ tra cứu dữ liệu thực. Nếu ticket/data nguồn lỗi, log lỗi và chặn case đó thay vì thay ticket khác.
5. Điều phối Customer/Order-Product/Payment/Delivery → Policy → Verifier cho từng case; chỉ ghi output sau `verified=true`.
6. Tạo run mới sẽ thay thế (không append) `logging/trace.jsonl`.

**Bàn giao:** Script kiểm tra input, một lệnh batch và coordinator gọi đúng contract.

**Nghiệm thu:** `python -m src.run_batch` đọc trực tiếp 50 input phát hành, chạy end-to-end mà không cần sửa tay file input/output.

## Thành viên 2 — Customer và Order & Product Agent

**Ownership:** `src/agents/customer.py`, `src/agents/order_product.py`, `src/repository.py` (phần orders/customers/items/products/sellers/category translation), unit test liên quan.

1. Load dữ liệu một lần và index theo `order_id`, `customer_id`, `customer_unique_id`, `product_id`, `seller_id`.
2. Customer Agent trả `customer_unique_id` và tối đa 5 order khác cùng `customer_unique_id`, theo thứ tự ổn định; tuyệt đối không đưa các order lịch sử vào `affected_entities`.
3. Order & Product Agent trả order nguồn, tối đa 5 item, 3 seller, 5 product và 5 category theo thứ tự nguồn; join product/category từ CSV.
4. Khi order không có item, trả mảng rỗng; báo `missing_or_conflicting_data` chứ không dựng item/seller/product/category.
5. Tạo evidence thô hợp lệ: `order:<id>`, `item:<order_id>:<item_id>`, `seller:<seller_id>`.

**Bàn giao:** Handoff JSON facts cho M1/M4 và unit tests gồm single/multi-item, multi-seller, repeat customer, multi-category, missing item.

**Nghiệm thu:** Mọi IDs của handoff join ngược được vào raw CSV; không vượt giới hạn output K4.

## Thành viên 3 — Payment và Delivery Agent

**Ownership:** `src/agents/payment.py`, `src/agents/delivery.py`, unit test liên quan.

1. Payment Agent nhóm payment theo order; tính `item_total_brl`, `freight_total_brl`, `expected_total_brl`, `payment_total_brl`, `difference_brl`, `reconciled`, payment types.
2. Nếu order không có item: `expected_total_brl`, `difference_brl`, `reconciled` là `null`; item/seller handoff rỗng theo yêu cầu README. Payment total vẫn chỉ dựa trên payment rows thực tế.
3. Payment Agent tạo evidence `payment:<order_id>:<payment_sequential>`, tối đa 5 payment IDs theo thứ tự nguồn.
4. Delivery Agent giữ nguyên ba timestamp; tính `delivery_variance_hours = delivered_customer - estimated_delivery`.
5. Với mỗi seller, lấy `shipping_limit_date` sớm nhất trong item của seller rồi tính `handoff_variance_hours = delivered_carrier - shipping_limit`; xác định `late_handoff` và `late_handoff_seller_ids`.
6. Không có timestamp cần thiết thì trả `null`, không thay bằng ngày hiện tại và không suy luận tracking checkpoint.

**Bàn giao:** Payment/delivery facts chuẩn hóa cho M4, cùng test case split payment, late seller, late logistics, null timestamp/no-item.

**Nghiệm thu:** Số tiền, timestamp và variance được recompute độc lập từ raw CSV và khớp đến 2 chữ số thập phân.

## Thành viên 4 — Policy Agent và test matrix

**Ownership:** `src/policy.py`, `tests/test_policy.py`, `docs/policy_matrix.md`.

1. Cài đặt policy engine thuần deterministic theo đúng độ ưu tiên `EC_POLICY_V2`:
   `canceled_order_paid` → `unavailable_order_paid` → `late_delivery_seller` → `late_delivery_logistics` → `valid_split_payment` → `unsupported_late_claim`.
2. Map primary issue sang root-cause code, responsible parties, refund, `case_status` và primary action theo README.
3. Thêm secondary issues theo đúng thứ tự nghiệp vụ: `multi_item_order`, `multi_seller_order`, `split_payment`, `repeat_customer`, `multiple_categories`.
4. Sinh action bổ sung đúng thứ tự: review delay phù hợp, `verify_refund_completion`, `coordinate_multi_seller_case`, `verify_payment_allocation`; loại action cuối nếu primary là `valid_split_payment`.
5. Chỉ dùng evidence ID thật được M2/M3 cung cấp, và thêm chính xác `policy:<root_cause_code>`.
6. Viết matrix/test cho mọi nhánh, gồm precedence (canceled/unavailable thắng các điều kiện khác), seller vs logistics, tolerance 0.10 BRL, payment split hợp lệ, delivery within estimate và missing data.

**Bàn giao:** Một policy decision object hoàn chỉnh nhưng chưa ghi file output, test matrix và giải thích rule.

**Nghiệm thu:** 100% test matrix pass; không có nhánh fallback tự bịa quyết định. Case không khớp policy phải được báo rõ để M1/M5 xử lý như lỗi dữ liệu.

## Thành viên 5 — Verifier, trace, tài liệu và đóng gói

**Ownership:** `src/verifier.py`, `src/trace.py`, `architecture.md`, `logging/metadata.json`, `individual_5SoCuoiMHV_HoVaTen.md`, `tests/test_verifier.py`, script đóng gói.

1. Viết verifier độc lập: kiểm tra cấu trúc output theo README K4, type, enum, confidence `[0,1]`, mảng rỗng/null, timestamp format, giới hạn mọi array và số tiền làm tròn 2 chữ số.
2. Từ facts/raw indexes, xác minh từng entity/evidence ID tồn tại và đúng format; recompute tổng tiền, variance, primary decision, root cause, refund và actions trước khi pass.
3. Ghi trace JSONL thực tế theo mỗi case/handoff/verifier result; trace phải có run ID và không chứa secret.
4. Soạn `architecture.md`: sơ đồ, role, quyền chỉ đọc CSV/quyền ghi output, handoff contract, policy decision flow và failure handling.
5. Điền `logging/metadata.json`: cohort `K4`, starter repo `K4-Day9-Multi-Agent-A2A`, policy `EC_POLICY_V2`, model/framework/runtime; nếu không dùng LLM ghi rõ deterministic/rule-based và parameter size `0`.
6. Rà và hoàn thiện báo cáo cá nhân theo phần mình thực làm. Tạo ZIP **chỉ chứa** `EC_001.json`…`EC_050.json` trong `output/`.

**Bàn giao:** Validator chạy được, trace, tài liệu, metadata, báo cáo và `submission_output.zip`.

**Nghiệm thu:** Validator pass 50/50; ZIP không có source, `.env`, cache, virtual environment hay file lạ.

## Mốc phối hợp bắt buộc

1. **Trước khi code:** M1 chốt `contracts.py`; cả nhóm duyệt contract trong cùng một buổi.
2. **Sau 45 phút:** M2/M3 gửi một handoff mẫu từ một order thật; M4 viết policy test theo sample; M5 viết validator skeleton.
3. **Sau 90 phút:** M1 chạy vertical slice với `EC_001` qua đủ sáu agent. Chỉ khi verifier pass mới chạy batch trên 50 input phát hành.
4. **Sau 170 phút:** M5 chạy quality gate; lỗi được trả về đúng owner module, không sửa trực tiếp file JSON.
5. **Trước nộp:** M1 merge/tích hợp, M5 xác nhận artifact; cả nhóm kiểm tra git diff, không commit secret và commit source trước khi zip output.

## Definition of Done toàn nhóm

- Dùng nguyên vẹn 50 input đã phát hành và tạo 50 output tương ứng, cùng `case_id`/tên file.
- 50 output qua validator, tất cả evidence truy xuất được từ 9 CSV và policy V2.
- `architecture.md`, `logging/trace.jsonl`, `logging/metadata.json` và các báo cáo thành viên hoàn chỉnh trong repo.
- `submission_output.zip` chỉ chứa 50 JSON ở cấp gốc của ZIP, không có thư mục lồng hoặc file ngoài phạm vi.
