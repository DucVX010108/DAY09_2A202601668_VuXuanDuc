# Lab K4 Day 09 — Context và hướng xử lý

## 1. Bối cảnh

- Cohort: **K4**
- Starter repo: **K4-Day9-Multi-Agent-A2A**
- Policy bắt buộc: **EC_POLICY_V2**
- Input: 50 ticket `input/EC_001.json` đến `input/EC_050.json`
- Kiến trúc: **Supervisor–Worker kết hợp Pipeline**

Luồng thực thi:

```text
Ticket
  → Coordinator
  → Customer Agent
  → Order & Product Agent
  → Payment Agent
  → Delivery Agent
  → Policy Agent
  → Verifier Agent
  → output/EC_xxx.json + trace
```

Coordinator là Supervisor. Các agent chuyên trách là Worker. Mỗi handoff được chuẩn hoá qua `src/contracts.Handoff`; Verifier là hard gate trước khi publish output.

## 2. Kết quả ban đầu

Lần chấm đầu đạt **5.6957/100**.

Unit test nội bộ vẫn pass, nhưng verifier cũ dùng một số projection do agent tạo ra nên có thể đồng ý với chính lỗi của pipeline. Vì vậy đã audit lại trực tiếp từ README, EC_POLICY_V2 và 9 CSV nguồn.

## 3. Nguyên nhân mất điểm chính

### Category bị dịch sai

43/50 output dùng category tiếng Anh như `health_beauty`, trong khi schema yêu cầu `product_category_name` lấy trực tiếp từ `products` CSV, ví dụ `beleza_saude`.

Đã sửa:

- `src/agents/order_product.py`
- `src/coordinator.py`
- `src/verifier.py`

Category hiện giữ nguyên giá trị nguồn và thứ tự xuất hiện trong CSV.

### `late_handoff` bị để `null`

7 case canceled/unavailable không có carrier timestamp. Pipeline cũ ghi:

```json
{
  "handoff_variance_hours": null,
  "late_handoff": null
}
```

Theo format grader, không có bằng chứng handoff trễ phải ghi cờ boolean `false`; variance vẫn là `null`.

Đã sửa:

- `src/agents/delivery.py`
- `src/contracts.py`
- `src/verifier.py`
- `tests/test_delivery.py`

## 4. Viết lại policy

`src/policy.py` đã được refactor theo bảng `POLICY_OUTCOMES`, gom thống nhất:

- primary issue
- root-cause code
- primary action
- nguồn tiền refund
- responsible party
- action review bổ sung

Thứ tự EC_POLICY_V2 được giữ nguyên:

```text
canceled_order_paid
→ unavailable_order_paid
→ late_delivery_seller
→ late_delivery_logistics
→ valid_split_payment
→ unsupported_late_claim
```

Các cải thiện khác:

- Tính tiền bằng `Decimal`, làm tròn 2 chữ số.
- Kiểm tra handoff mâu thuẫn trước khi áp policy.
- Giữ `policy:<root_cause>` ở cuối `evidence_ids`, kể cả khi danh sách bị giới hạn 20 phần tử.
- Không tạo fallback decision khi thiếu dữ liệu hoặc không match policy branch.

## 5. Verifier và trace

Verifier hiện tự đọc lại từ repository:

- order, item, payment
- customer identity và related orders
- product/category nguồn
- delivery và seller handoff

Verifier không còn lấy category/customer projection từ specialist output để tự xác nhận cùng một lỗi.

Trace thực tế có các event:

```text
batch_started
→ case_started
→ handoff*
→ verification_passed|verification_failed
→ case_finished
→ batch_finished
→ output_publish
```

Trace có redaction cho key nhạy cảm như `api_key`, `token`, `secret`, `authorization`.

## 6. Kiểm thử sau khi sửa

- Unit tests: **88/88 passed**
- Input validation: **50/50 valid**
- Batch processing: **50/50 verified**
- Verification failed: **0**
- Output JSON parse lỗi: **0**
- Case ID trùng: **0**
- `action_required`: 34 case
- `no_action`: 16 case

Audit chéo cho 10 nhóm output:

- case assessment
- affected entities
- customer context
- product context
- delivery analysis
- payment reconciliation
- root cause
- evidence
- financial resolution
- resolution actions

Tất cả 50 case đều khớp sau khi sửa category và `late_handoff`.

## 7. Đóng gói

ZIP hiện chứa đúng:

```text
output/EC_001.json
...
output/EC_050.json
```

Không đưa source, trace, metadata, `.env`, cache hoặc virtual environment vào ZIP.

## 8. Kết quả chấm

- Điểm ban đầu: **5.6957/100**
- Điểm sau khi sửa: **79/100**
- Mức cải thiện: **+73.3043 điểm**

Commit chứa phiên bản đã sửa:

```text
389a4d2 fix: align K4 policy output with README grader
```

