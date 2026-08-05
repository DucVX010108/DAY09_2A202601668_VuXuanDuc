# Báo cáo cá nhân — Day 09 Multi-Agent A2A

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
| --- | --- |
| Họ và tên | Nguyễn Tuấn Trường |
| MSSV | 01842 |
| Cohort | K4 |
| Vai trò chính | Verifier, trace, tài liệu, audit và đóng gói |
| Ngày hoàn thành | 2026-08-05 |

## 2. Phạm vi công việc trực tiếp thực hiện

| Hạng mục | File/artifact | Kết quả bàn giao |
| --- | --- | --- |
| Kiểm tra output độc lập | `src/verifier.py`, `src/coordinator.py` | Verifier tái tính các field nghiệp vụ từ CSV nguồn trước khi cho publish. |
| Audit chéo 50 case | `scripts/audit_cross.py` | Script không import pipeline; tự join input và CSV để phát hiện output sai. |
| Trace và metadata | `src/run_batch.py`, `trace.jsonl`, `metadata.json` | Trace JSONL theo run mới nhất; metadata ghi cohort, policy, model, runtime và run ID. |
| Đóng gói submission | `scripts/package_submission.ps1`, `submission_output.zip` | ZIP chỉ có 50 entry `output/EC_001.json` đến `output/EC_050.json`. |
| Tài liệu và preflight | `architecture.md`, `docs/policy_matrix.md`, `context.md` | Mô tả pattern, handoff, policy, checklist và kết quả kiểm tra cuối. |

Tôi cũng hỗ trợ tích hợp Coordinator–Policy–Verifier để đảm bảo candidate chỉ được ghi khi verifier không trả lỗi và cả batch chỉ publish khi đủ 50 case verified.

## 3. Vấn đề kỹ thuật đã giải quyết

Lần chấm đầu đạt 5.6957/100 dù unit test nội bộ pass. Nguyên nhân là một phần verifier lấy lại projection từ agent, nên có khả năng xác nhận cùng lỗi với pipeline. Tôi kiểm tra lại output theo README và 9 CSV nguồn, phát hiện hai sai lệch có ảnh hưởng lớn:

1. `product_context.category_names` bị dịch sang tiếng Anh ở 43/50 case, ví dụ `health_beauty`, thay vì lấy trực tiếp `products.product_category_name` như `beleza_saude`.
2. Khi không có carrier timestamp, `handoff_variance_hours` đúng là `null` nhưng `late_handoff` cần là `false` vì không có bằng chứng seller giao trễ. Pipeline cũ xuất `null` cho cờ này ở 7 case.

Sau khi sửa, kết quả chấm tăng lên **79/100**.

## 4. Cách triển khai

### Verifier độc lập

`_verifier_facts()` trong Coordinator lấy trực tiếp từ repository:

- order, item, payment rows;
- customer identity và tối đa 5 related orders;
- product rows và `product_category_name` gốc;
- timestamp giao hàng và seller handoff.

Verifier kiểm tra schema, giới hạn array, entity/evidence ID, payment reconciliation, delivery variance, primary/secondary issue, root cause, responsible party, refund và action order. Nếu có lỗi, `process_case()` trả `RunResult.blocked`; output candidate không được publish.

### Policy và evidence

Policy được tổ chức theo bảng `POLICY_OUTCOMES` để một primary issue luôn map nhất quán sang root cause, primary action, refund source, responsible party và review action. Thứ tự precedence giữ nguyên `EC_POLICY_V2`:

```text
canceled_order_paid
→ unavailable_order_paid
→ late_delivery_seller
→ late_delivery_logistics
→ valid_split_payment
→ unsupported_late_claim
```

Evidence chỉ sử dụng format có thể dựng từ CSV:

```text
order:<order_id>
item:<order_id>:<order_item_id>
payment:<order_id>:<payment_sequential>
seller:<seller_id>
policy:<root_cause_code>
```

Policy evidence luôn được giữ ở cuối danh sách, kể cả khi giới hạn 20 evidence.

### Trace và đóng gói

`AtomicTraceSink` tạo trace mới thay vì append lịch sử cũ. Mỗi event có `run_id`, `sequence`, `case_id`, `event_type`, `stage` và `status`; các key nhạy cảm như `api_key`, `token`, `secret`, `authorization` được redaction.

Chuỗi event chính:

```text
batch_started → case_started → handoff*
→ verification_passed|verification_failed
→ case_finished → batch_finished → output_publish
```

`run_batch` ghi `trace.jsonl` và `metadata.json` tại root (đúng README), đồng thời giữ bản audit trong `logging/`. Script package từ chối output thiếu, thừa hoặc có file lạ, sau đó tạo ZIP với đúng prefix `output/`.

## 5. Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | Một ticket K4, các raw row được join từ `data/`, và candidate JSON từ Policy/Coordinator. |
| Output | Danh sách lỗi verifier hoặc JSON hợp lệ để publish. |
| Contract nhận | `Ticket`, `AggregatedFacts`, `PolicyDecision`, `Handoff` trong `src/contracts.py`. |
| Contract bàn giao | `RunResult.verified` chỉ chứa output đã pass; `RunResult.blocked` chứa lỗi có stage/agent. |
| Điều kiện lỗi | ID không tồn tại, calculation sai, policy không khớp, vượt array limit, timestamp/null handling sai hoặc evidence sai format. |

## 6. Quyết định kỹ thuật quan trọng

**Bối cảnh:** Có thể để verifier dùng lại facts đã aggregate cho nhanh, hoặc tái truy vấn CSV để độc lập kiểm tra.

**Phương án được chọn:** Verifier tái truy vấn raw source rows.

**Lý do:** Mục tiêu của verifier là phát hiện sai lệch của agent/coordinator. Nếu dùng lại projection của agent, lỗi category hoặc customer context có thể đi qua cả hai lớp. Chi phí của việc đọc index in-memory nhỏ hơn nhiều so với rủi ro output sai.

**Bằng chứng:** `scripts/audit_cross.py` tự recompute toàn bộ 50 case từ CSV và báo `cases with mismatches: 0`; kết quả sau sửa đạt 79/100.

## 7. Cách xác minh đã chạy

```powershell
.venv\Scripts\python.exe -B -m pytest -q
.venv\Scripts\python.exe -B -m src.run_batch
.venv\Scripts\python.exe -B scripts\audit_cross.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\package_submission.ps1 -Force
```

Kết quả thực tế:

- 90/90 unit tests pass.
- Input validation: 50/50 valid.
- Batch: 50 `verification_passed`, 0 `verification_failed`.
- Audit CSV độc lập: 50 case, 0 mismatch.
- ZIP: 50 entry, từ `output/EC_001.json` đến `output/EC_050.json`, không có entry thừa.

## 8. Hiểu biết end-to-end

Coordinator nhận ticket, gọi bốn data agent để tạo handoff facts/evidence, sau đó Policy Agent áp EC_POLICY_V2. Coordinator assemble candidate theo schema và đưa qua Verifier. Verifier không tin kết luận policy một cách mù quáng: nó tái tính tiền, thời gian, seller handoff, entity/evidence và quyết định nghiệp vụ từ CSV. Khi một case fail, Coordinator không tạo file output cho case đó; khi bất kỳ case nào fail, batch không thay bộ output đã publish. Chỉ khi đủ 50 case verified và trace được finalize thì batch mới publish output, sau đó script package kiểm tra danh sách file và tạo ZIP nộp bài.

## 9. Cam kết

- [x] Báo cáo phản ánh phần việc trực tiếp thực hiện.
- [x] Có thể giải thích luồng end-to-end và contract giữa các agent.
- [x] Các kết quả nêu trong báo cáo đã được kiểm thử/audit.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo không sao chép nguyên văn báo cáo của thành viên khác.

**Họ và tên:** Nguyễn Tuấn Trường
**Ngày xác nhận:** 2026-08-05
