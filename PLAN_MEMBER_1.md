# Kế hoạch Member 1 — Coordinator, Input Validation và Tích hợp  
## Bản cải tiến toàn diện, deterministic và production-grade trong phạm vi bài K4

## 1. Mục tiêu

Hoàn thiện pipeline điều phối cho đúng 50 ticket K4 bằng Python standard library, bảo đảm:

- Dùng nguyên vẹn `input/EC_001.json` đến `input/EC_050.json`.
- Chỉ dùng `claimed_order_id` từ ticket để truy xuất dữ liệu Olist.
- Luồng nguồn sự thật cố định: CSV → domain facts → policy → verifier → output.
- Mỗi agent trao đổi qua contract chung, được kiểm tra runtime.
- Repository CSV chỉ được khởi tạo và index đúng một lần cho mỗi batch run.
- Mỗi case được xử lý độc lập, nhưng chỉ publish bộ `output/` mới khi đủ 50 case verified.
- Không sửa ticket, không tự thay order, không tạo dữ kiện fallback và không sửa tay JSON.
- Trace và input-validation log của lần chạy mới thay thế hoàn toàn lần chạy cũ bằng thao tác atomic.
- Kết quả deterministic: cùng input và CSV phải tạo cùng output bytes, ngoại trừ run ID và operational timestamps trong trace/log.
- `main()` trả `0` khi đủ 50/50 case thành công; trả `1` cho mọi trạng thái thất bại.

Đây là “production-grade” trong phạm vi một batch processor local, single-process và deterministic; không mở rộng thành distributed system hoặc thêm dependency ngoài yêu cầu bài.

---

## 2. Phạm vi ownership

Member 1 chỉ sửa:

- `src/contracts.py`
- `src/coordinator.py`
- `src/run_batch.py`
- `tests/test_contracts.py`
- `tests/test_coordinator.py`
- `tests/test_run_batch.py`
- `logging/input_validation.json` do chương trình sinh

Member 1 không sửa source thuộc ownership của M2–M5. Mọi thay đổi interface liên nhóm phải được chốt trong `src/contracts.py` trước khi code song song.

Các dependency Member 1 sử dụng:

- M2:
  - `OlistRepository`
  - `investigate_customer(ticket, repository)`
  - `investigate_order_and_product(ticket, repository)`
- M3:
  - `investigate_payment(ticket, repository)`
  - `investigate_delivery(ticket, repository)`
- M4:
  - `apply_policy(facts)`
- M5:
  - `verify_output(output, facts, repository) -> list[str]`
  - trace lifecycle API đã thống nhất

---

## 3. Các quyết định kỹ thuật được chốt

### 3.1. Runtime và dependency

- Dùng Python 3 và chỉ dùng standard library.
- Không dùng LLM trong pipeline.
- Không dùng Pydantic, pandas hoặc framework orchestration.
- Không thêm concurrency ở phiên bản nộp bài.
- Chạy tuần tự theo filename để giữ tính deterministic, đơn giản hóa trace và tránh race condition.

### 3.2. Tiền tệ

- Parse mọi giá trị tiền trực tiếp từ chuỗi CSV bằng `decimal.Decimal`.
- Không đi qua `float` trước khi tạo `Decimal`.
- Tính tổng bằng precision đầy đủ.
- Tolerance reconciliation là `Decimal("0.10")`.
- Điều kiện pass:
  - `abs(difference_brl) <= Decimal("0.10")`
- Khi đưa ra output, quantize về `Decimal("0.01")`.
- Chế độ làm tròn thống nhất: `ROUND_HALF_UP`.
- Chỉ chuyển `Decimal` thành JSON number tại serialization boundary.
- Không làm tròn từng row trước khi cộng; cộng đầy đủ trước, sau đó mới quantize kết quả cuối.

### 3.3. Timestamp

- Business timestamp từ CSV giữ nguyên chuỗi `YYYY-MM-DD HH:MM:SS`.
- Parse bằng `datetime.strptime(value, "%Y-%m-%d %H:%M:%S")`.
- Không đổi timezone.
- Không thay timestamp thiếu bằng thời gian hiện tại.
- Variance:
  - `total_seconds() / 3600`
  - làm tròn 2 chữ số bằng cùng quy tắc `ROUND_HALF_UP`.
- Giá trị variance âm là hợp lệ.
- Thiếu một timestamp bắt buộc cho phép tính thì variance là `null`.
- Operational timestamp của trace/log dùng UTC ISO 8601, ví dụ `2026-08-05T08:20:00.123456Z`, và không được dùng làm business fact.

### 3.4. Confidence

- Mọi output đã qua deterministic policy và independent verifier dùng:
  - `confidence = 1.0`
- Không tự dựng công thức xác suất.
- Case không đủ dữ liệu để quyết định không được hạ confidence rồi vẫn xuất; case đó phải bị block.

### 3.5. Publish output

- Không xóa output cũ ở đầu run.
- Output verified được ghi vào staging directory.
- Chỉ publish sang `output/` khi đủ 50 case verified.
- Nếu batch fail, `output/` giữ nguyên bộ thành công gần nhất.
- Publish phải có backup và rollback để tránh trạng thái nửa cũ, nửa mới.
- Chỉ thao tác chính xác với `EC_001.json` đến `EC_050.json`; không glob và xóa nhầm artifact khác.

### 3.6. Exit code

- `0`: đủ 50 input hợp lệ, 50 case verified, publish thành công.
- `1`: mọi loại lỗi còn lại, gồm validation, repository, agent, policy, verifier, trace hoặc publish.

---

## 4. Contract freeze trước khi triển khai

Không bắt đầu viết coordinator hoàn chỉnh cho đến khi M1–M5 duyệt contract.

`src/contracts.py` là nguồn duy nhất định nghĩa:

- Constant và enum.
- Ticket schema.
- Handoff envelope.
- Domain facts schema.
- Policy decision schema.
- Aggregated case facts.
- Structured error.
- Run result.
- Input validation report.
- JSON serialization và runtime validation.

Không module nào được tự định nghĩa một bản sao contract.

---

## 5. Constants và enum

Dùng `Enum` kế thừa `str` hoặc constant immutable cho:

### 5.1. Policy

```text
POLICY_VERSION = "EC_POLICY_V2"
```

### 5.2. Agent name

```text
coordinator
customer
order_product
payment
delivery
policy
verifier
```

### 5.3. Case status nội bộ

```text
verified
blocked
```

Không nhầm với output `case_status`:

```text
action_required
no_action
```

### 5.4. Stage

```text
input_validation
customer
order_product
payment
delivery
policy
assembly
verification
output_staging
output_publish
trace_finalize
```

### 5.5. Primary issue

```text
canceled_order_paid
unavailable_order_paid
late_delivery_seller
late_delivery_logistics
valid_split_payment
unsupported_late_claim
```

### 5.6. Secondary issue theo thứ tự nghiệp vụ

```text
multi_item_order
multi_seller_order
split_payment
repeat_customer
multiple_categories
```

### 5.7. Root cause

```text
ORDER_CANCELED_AFTER_PAYMENT
ORDER_UNAVAILABLE_AFTER_PAYMENT
SELLER_HANDOFF_AFTER_LIMIT
CARRIER_DELIVERED_AFTER_ESTIMATE
MULTIPLE_PAYMENTS_RECONCILED
DELIVERY_WITHIN_ESTIMATE
```

---

## 6. Ticket contract

### 6.1. Dataclass

Tạo các dataclass immutable hoặc coi như immutable sau parse:

```text
CustomerRequest
- language: str
- message: str
- claimed_order_id: str

InvestigationScope
- include_customer_history: bool
- include_product_context: bool

Ticket
- case_id: str
- customer_request: CustomerRequest
- investigation_scope: InvestigationScope
- policy_version: str
```

### 6.2. Validation

Ticket hợp lệ khi:

- JSON root là object.
- Không thiếu field bắt buộc.
- Không chấp nhận field sai kiểu.
- `case_id` khớp regex `^EC_[0-9]{3}$`.
- `case_id` khớp filename.
- `language == "vi"`.
- `message` là chuỗi không rỗng sau `strip()`.
- `claimed_order_id` là chuỗi không rỗng.
- Hai scope là boolean thật, không nhận `1`, `"true"` hoặc giá trị truthy khác.
- Hai scope đều bằng `True`.
- `policy_version == "EC_POLICY_V2"`.

Unknown field:

- Ticket và nested ticket object dùng strict mode.
- Unknown field bị báo lỗi để phát hiện schema drift sớm.
- Không tự bỏ qua field lạ.

### 6.3. Không mutation

- `from_dict()` không giữ reference tới mutable input dict/list.
- `to_dict()` trả object mới.
- Agent không được sửa ticket.
- Test phải xác nhận input dict ban đầu không đổi sau pipeline.

---

## 7. Handoff envelope

Mọi domain agent và policy trả đúng tám field:

```text
Handoff
- case_id: str
- from_agent: str
- to_agent: str
- task: str
- facts: dict
- evidence_ids: list[str]
- missing_or_conflicting_data: list[str]
- next_task: str
```

### 7.1. Routing được phép

Các agent domain và policy luôn trả về coordinator:

```text
customer -> coordinator
order_product -> coordinator
payment -> coordinator
delivery -> coordinator
policy -> coordinator
```

Verifier không trả Handoff; verifier giữ interface:

```text
verify_output(output, facts, repository) -> list[str]
```

### 7.2. Validation chung

- Đủ đúng tám field.
- Không có unknown field.
- Mọi field đúng type runtime.
- `case_id` phải bằng ticket hiện tại.
- `from_agent` đúng agent đang được gọi.
- `to_agent == "coordinator"`.
- `task` và `next_task` là chuỗi không rỗng, nằm trong task catalog đã chốt.
- `facts` là object.
- `evidence_ids` là list string, không có string rỗng.
- `missing_or_conflicting_data` là list string, không có string rỗng.
- Không cho agent trả evidence policy, trừ Policy Agent.
- Không cho Policy Agent tạo evidence CSV chưa có từ domain agents.

### 7.3. Task catalog

```text
customer:
  task = "investigate_customer"
  next_task = "aggregate_customer_facts"

order_product:
  task = "investigate_order_and_product"
  next_task = "aggregate_order_product_facts"

payment:
  task = "investigate_payment"
  next_task = "aggregate_payment_facts"

delivery:
  task = "investigate_delivery"
  next_task = "aggregate_delivery_facts"

policy:
  task = "apply_ec_policy_v2"
  next_task = "assemble_and_verify_output"
```

---

## 8. Domain fact schema

`facts: dict` trong envelope phải được parse tiếp thành dataclass domain cụ thể. Envelope hợp lệ không đồng nghĩa payload hợp lệ.

### 8.1. CustomerFacts

```text
claimed_order_id: str
customer_id: str
customer_unique_id: str
related_order_ids: list[str]       # projection tối đa 5
related_order_count: int           # full count, không truncate
has_related_orders: bool
```

Invariant:

- `claimed_order_id` bằng ticket.
- `related_order_ids` không chứa claimed order.
- Không đưa order lịch sử vào `affected_entities`.
- List giữ thứ tự nguồn ổn định.
- `has_related_orders == (related_order_count > 0)`.
- `len(related_order_ids) <= 5`.
- Mọi related order join ngược được vào repository và cùng `customer_unique_id`.

### 8.2. OrderProductFacts

```text
order_id: str
order_status: str
customer_id: str

item_ids: list[str]                # projection tối đa 5
seller_ids: list[str]              # projection tối đa 3
product_ids: list[str]             # projection tối đa 5
category_names: list[str]          # projection tối đa 5

item_count: int                    # full count
seller_count: int                  # full distinct count
product_count: int                 # full distinct count
category_count: int                # full distinct count

items: list[OrderItemFact]         # full rows cần tính toán
```

`OrderItemFact` tối thiểu gồm:

```text
order_id
order_item_id
product_id
seller_id
price_raw
freight_value_raw
shipping_limit_date
category_name
```

Invariant:

- `order_id` bằng claimed order.
- `item_ids` có format `<order_id>:<order_item_id>`.
- Projection giữ thứ tự nguồn.
- Distinct list deduplicate theo lần xuất hiện đầu tiên.
- `items` có thể rỗng.
- Khi không có item:
  - item/seller/product/category projection đều rỗng.
  - các count tương ứng bằng `0`.
- Không truncate `items` trước khi payment/delivery/policy tính nghiệp vụ.

### 8.3. PaymentFacts

```text
order_id: str
payment_ids: list[str]             # projection tối đa 5
payment_count: int                 # full count
payment_types: list[str]           # distinct, source order

item_total_brl: Decimal
freight_total_brl: Decimal
expected_total_brl: Decimal | None
payment_total_brl: Decimal
difference_brl: Decimal | None
reconciled: bool | None
```

Invariant:

- `payment_total_brl` tính từ toàn bộ payment row.
- `item_total_brl` và `freight_total_brl` tính từ toàn bộ item row.
- Nếu không có item:
  - `expected_total_brl = None`
  - `difference_brl = None`
  - `reconciled = None`
- Nếu có item:
  - `expected_total_brl = item_total_brl + freight_total_brl`
  - `difference_brl = payment_total_brl - expected_total_brl`
  - `reconciled = abs(difference_brl) <= 0.10`
- `payment_count` dùng full row count, không dùng độ dài projection.
- `payment_ids` theo source order.

### 8.4. DeliveryFacts

```text
order_id: str
delivered_at: str | None
estimated_delivery_at: str | None
carrier_handoff_at: str | None
delivery_variance_hours: Decimal | None

seller_handoff_analysis: list[SellerHandoffFact]
late_handoff_seller_ids: list[str]
```

`SellerHandoffFact`:

```text
seller_id: str
shipping_limit_at: str | None
handoff_variance_hours: Decimal | None
late_handoff: bool | None
```

Invariant:

- `delivery_variance_hours` chỉ có khi đủ delivered và estimated.
- Với mỗi seller, `shipping_limit_at` là timestamp sớm nhất trong item của seller.
- `handoff_variance_hours` chỉ có khi đủ carrier handoff và shipping limit.
- `late_handoff = handoff_variance_hours > 0`.
- Nếu thiếu dữ liệu tính, `late_handoff = None`, không mặc định `False`.
- `late_handoff_seller_ids` chỉ chứa seller có `late_handoff is True`.
- Thứ tự seller theo lần xuất hiện đầu tiên trong item source.
- Khi không có item, seller analysis và late seller IDs đều rỗng.

### 8.5. PolicyDecision

```text
primary_issue: str
secondary_issues: list[str]
case_status: str
confidence: float                  # luôn 1.0
ranked_causes: list[RankedCause]
responsible_parties: list[ResponsibleParty]
recommended_refund_brl: Decimal
resolution_actions: list[str]
policy_evidence_id: str
```

Invariant:

- Chỉ dùng `EC_POLICY_V2`.
- Primary precedence đúng README.
- Secondary issues đúng thứ tự nghiệp vụ.
- Actions đúng thứ tự nghiệp vụ.
- `confidence == 1.0`.
- `policy_evidence_id == "policy:<root_cause_code_rank_1>"`.
- Không có fallback decision tự bịa.
- Không đủ dữ liệu để match một nhánh hợp lệ thì policy phải báo lỗi có cấu trúc, không trả decision giả.

### 8.6. AggregatedFacts

Không merge dict phẳng. Dùng namespace:

```text
AggregatedFacts
- ticket: Ticket
- customer: CustomerFacts
- order_product: OrderProductFacts
- payment: PaymentFacts
- delivery: DeliveryFacts
- source_evidence_ids: list[str]
- missing_or_conflicting_data: list[str]
```

Mục tiêu:

- Tránh field collision.
- Verifier có đủ ticket gốc, full counts và raw facts.
- Policy nhận một schema duy nhất.
- Coordinator không phải đoán field.

---

## 9. Missing và conflicting data

`missing_or_conflicting_data` dùng chuỗi có code ổn định:

```text
<CODE>: <human-readable detail>
```

Ví dụ:

```text
NO_ORDER_ITEMS: claimed order has no item rows
NO_PAYMENT_ROWS: claimed order has no payment rows
MISSING_DELIVERED_AT: order_delivered_customer_date is null
MISSING_ESTIMATED_DELIVERY_AT: order_estimated_delivery_date is null
MISSING_CARRIER_HANDOFF_AT: order_delivered_carrier_date is null
MISSING_SHIPPING_LIMIT: seller has no valid shipping_limit_date
PRODUCT_NOT_FOUND: item product_id cannot be joined
CATEGORY_TRANSLATION_MISSING: category translation is unavailable
CUSTOMER_NOT_FOUND: order customer_id cannot be joined
ORDER_NOT_FOUND: claimed order does not exist
```

### 9.1. Blocking rule

Coordinator không block chỉ vì list này không rỗng.

Coordinator block ngay khi:

- Contract payload không hợp lệ.
- `ORDER_NOT_FOUND`.
- `CUSTOMER_NOT_FOUND`.
- Cross-agent identity conflict:
  - order ID khác nhau.
  - customer ID khác nhau.
  - case ID khác nhau.
- Không thể kiểm chứng required source identity.
- Policy không thể đưa ra decision hợp lệ.
- Verifier trả lỗi.

Các issue có thể hợp lệ theo README như no-item hoặc missing timestamp được chuyển tiếp cho policy/verifier. Policy chỉ quyết định nếu branch có đủ dữ liệu.

Ví dụ:

- Order canceled và có payment vẫn có thể quyết định `canceled_order_paid` dù không có item.
- Order no-item, không canceled/unavailable và không thể reconcile thì không được rơi vào unsupported fallback; phải block.

---

## 10. Structured error và RunResult

### 10.1. StructuredError

```text
code: str
stage: str
message: str
agent: str | None
details: dict
```

Error code tối thiểu:

```text
INPUT_DIRECTORY_UNAVAILABLE
INPUT_FILE_MISSING
INPUT_FILE_UNEXPECTED
INPUT_JSON_INVALID
INPUT_SCHEMA_INVALID
DUPLICATE_CASE_ID
DUPLICATE_CLAIMED_ORDER_ID
ORDER_NOT_FOUND
REPOSITORY_INITIALIZATION_FAILED
AGENT_EXECUTION_FAILED
HANDOFF_CONTRACT_INVALID
DOMAIN_FACT_CONTRACT_INVALID
CROSS_AGENT_FACT_CONFLICT
POLICY_EXECUTION_FAILED
POLICY_NO_DECISION
POLICY_CONTRACT_INVALID
OUTPUT_ASSEMBLY_FAILED
VERIFICATION_FAILED
OUTPUT_STAGING_FAILED
OUTPUT_PUBLISH_FAILED
TRACE_FINALIZATION_FAILED
UNEXPECTED_INTERNAL_ERROR
```

### 10.2. RunResult

```text
case_id: str
status: "verified" | "blocked"
output: dict | None
errors: list[StructuredError]
stage_durations_ms: dict[str, float]
```

Invariant:

- `verified`:
  - output không `None`
  - errors rỗng
- `blocked`:
  - output `None`
  - errors không rỗng
- Không tạo fallback output cho blocked case.

---

## 11. Input validation architecture

Tách validation thành các pha.

### 11.1. Phase A — Infrastructure validation

Fatal cho toàn batch:

- `input/` không tồn tại hoặc không đọc được.
- `data/` hoặc CSV bắt buộc không tồn tại.
- Không tạo được logging/staging directory.
- Repository không khởi tạo được.
- Không bắt đầu được trace temp run.

Nếu Phase A fail:

- Không xử lý case.
- Không đụng `output/`.
- Ghi được log nào thì ghi.
- Exit `1`.

### 11.2. Phase B — Release manifest discovery

Expected filenames:

```text
EC_001.json ... EC_050.json
```

Quy tắc:

- Missing expected file tạo release error.
- Extra file khớp `EC_*.json` tạo release error và không được xử lý.
- `.gitkeep` và artifact không khớp pattern được giữ nguyên và bỏ qua.
- Sai casing hoặc sai zero-padding không được coi là expected file.
- Manifest error làm batch cuối cùng fail, nhưng hệ thống vẫn tiếp tục parse và xử lý các expected file hiện có để cung cấp diagnostics đầy đủ.
- Không publish output nếu manifest không sạch.

### 11.3. Phase C — Ticket parse và schema validation

- Đọc UTF-8 strict.
- JSON syntax error chỉ block file tương ứng.
- Root không phải object chỉ block file tương ứng.
- Validate Ticket contract.
- Không sửa input.
- Không tự đổi case ID hoặc claimed order.

### 11.4. Phase D — Duplicate detection

Sau khi parse tất cả ticket hợp lệ về schema:

- Duplicate `case_id`:
  - block tất cả file liên quan.
- Duplicate `claimed_order_id`:
  - block tất cả case liên quan theo release invariant K4.
- Không chọn “first wins”.
- Log danh sách toàn bộ file/case liên quan.

### 11.5. Phase E — Source existence validation

- Repository đã được khởi tạo một lần.
- Dùng `repository.has_order(claimed_order_id)` hoặc interface tương đương đã chốt với M2.
- Không scan orders CSV lần hai trong M1.
- Order không tồn tại:
  - block case.
  - không thay bằng order khác.

### 11.6. InputValidationReport

Ghi atomically vào `logging/input_validation.json`:

```text
run_id
started_at
finished_at
success
summary:
  expected_file_count
  discovered_expected_file_count
  valid_case_count
  invalid_case_count
  missing_file_count
  unexpected_file_count
fatal_errors
release_errors
valid_case_ids
cases:
  <case_or_filename>:
    valid
    claimed_order_id
    errors
```

- `success` chỉ true khi manifest sạch và đủ 50 case valid.
- File mới thay thế hoàn toàn file cũ.
- Không append.
- UTF-8, `ensure_ascii=False`, stable key serialization.

---

## 12. Batch lifecycle

`run_batch.main()` thực hiện đúng thứ tự:

1. Resolve root paths.
2. Tạo `run_id` bằng UUID4.
3. Bắt đầu monotonic timer.
4. Validate infrastructure tối thiểu để mở temp trace/log.
5. Discover manifest.
6. Parse và validate ticket.
7. Khởi tạo repository đúng một lần.
8. Validate order existence.
9. Hoàn thiện và atomic-write `input_validation.json`.
10. Tạo staging directory riêng cho run.
11. Xử lý các case valid theo filename ascending.
12. Thu `RunResult` cho cả 50 expected case:
    - missing/invalid case có blocked result.
13. Nếu và chỉ nếu:
    - manifest sạch,
    - 50 ticket valid,
    - 50 RunResult verified,
    - 50 staged outputs pass final file audit,
    thì publish transactionally.
14. Finalize trace hiện tại, kể cả batch fail.
15. Dọn staging/backup an toàn.
16. Trả `0` nếu publish thành công; ngược lại `1`.

Repository không được khởi tạo lại trong `process_case()`.

---

## 13. `process_case(ticket, repository)` chi tiết

### 13.1. Signature

```text
process_case(ticket: Ticket, repository: OlistRepository, trace: TraceSink) -> RunResult
```

Nếu cần giữ compatibility ban đầu, trace có thể được inject qua context object, nhưng không dùng global mutable state.

### 13.2. Luồng

1. Ghi `case_started`.
2. Gọi Customer Agent.
3. Validate Handoff envelope.
4. Parse và validate `CustomerFacts`.
5. Ghi trace handoff đã scrub.
6. Gọi Order & Product Agent.
7. Validate Handoff.
8. Parse `OrderProductFacts`.
9. Cross-check:
   - claimed order.
   - customer ID.
10. Gọi Payment Agent.
11. Validate Handoff.
12. Parse `PaymentFacts`.
13. Cross-check order ID.
14. Gọi Delivery Agent.
15. Validate Handoff.
16. Parse `DeliveryFacts`.
17. Cross-check order ID và seller IDs.
18. Tạo `AggregatedFacts`.
19. Dedupe evidence theo thứ tự xuất hiện.
20. Validate mọi source evidence format.
21. Gọi Policy Agent.
22. Validate policy Handoff.
23. Parse `PolicyDecision`.
24. Assemble output bằng pure function.
25. Validate output JSON-serializable.
26. Gọi verifier:
    - `verify_output(output, aggregated_facts, repository)`
27. Nếu verifier trả list không rỗng:
    - trả blocked.
    - không ghi output.
28. Nếu pass:
    - ghi output vào staging bằng atomic file write.
    - trả verified.
29. Ghi `case_finished`.

### 13.3. Exception handling

Mỗi stage có wrapper riêng để:

- Đo duration bằng `time.perf_counter()`.
- Gắn đúng error code/stage/agent.
- Ghi trace failure.
- Không để exception một case dừng case khác.

Không chỉ dùng một `except Exception` chung cho toàn pipeline. Vẫn có outermost catch để chuyển programmer bug thành `UNEXPECTED_INTERNAL_ERROR`, nhưng giữ stage context và sanitized exception class/message.

---

## 14. Output assembly

Coordinator assemble output, nhưng không tính lại nghiệp vụ.

### 14.1. Mapping

```text
case_id
  <- ticket.case_id

case_assessment
  <- PolicyDecision

affected_entities.order_ids
  <- [ticket.claimed_order_id]

affected_entities.item_ids
  <- OrderProductFacts.item_ids

affected_entities.seller_ids
  <- OrderProductFacts.seller_ids

affected_entities.payment_ids
  <- PaymentFacts.payment_ids

customer_context
  <- CustomerFacts

product_context
  <- OrderProductFacts.product_ids/category_names

delivery_analysis
  <- DeliveryFacts

payment_reconciliation
  <- PaymentFacts

root_cause_analysis
  <- PolicyDecision.ranked_causes/responsible_parties

financial_resolution
  <- PolicyDecision.recommended_refund_brl

resolution_actions
  <- PolicyDecision.resolution_actions
```

### 14.2. Evidence order

Dedupe ổn định theo thứ tự:

1. `order:<order_id>`
2. Item evidence theo item source order, tối đa 5.
3. Payment evidence theo payment source order, tối đa 5.
4. Seller evidence chỉ cho responsible seller, theo responsible-party order, tối đa 3.
5. Một policy evidence cuối cùng.

Với caps trên, tổng tối đa là 15 và không vượt limit 20.

Không đưa evidence seller không chịu trách nhiệm chỉ để tăng số lượng evidence.

### 14.3. Array limits

Projection limits:

```text
order IDs: 5
item IDs: 5
seller IDs: 3
payment IDs: 5
related order IDs: 5
product IDs: 5
categories: 5
ranked causes: 3
responsible parties: 3
evidence: 20
actions: 5
```

Mọi business decision dùng full counts/full rows, không dùng list projection đã truncate.

### 14.4. Stable ordering

- Case processing: filename ascending.
- Raw row-derived arrays: source CSV order.
- Distinct values: lần xuất hiện đầu tiên.
- Related orders: order source order, loại claimed order.
- Secondary issues: business order.
- Actions: business order.
- Evidence: order → items → payments → responsible sellers → policy.
- JSON keys: stable serialization.

---

## 15. Cross-agent consistency checks

Trước policy, coordinator kiểm tra:

- Mọi `case_id` giống ticket.
- Mọi `order_id` giống `claimed_order_id`.
- Customer order/customer IDs join nhất quán.
- Payment và Delivery không tham chiếu seller ngoài OrderProduct facts.
- Item/seller/product/payment IDs đúng format.
- Projection là prefix/deduplicated projection hợp lệ của full facts.
- Count không nhỏ hơn projection length.
- `has_related_orders` khớp count.
- Payment formulas tự nhất quán ở mức contract.
- Delivery flags tự nhất quán với variance.
- Evidence IDs agent cung cấp chỉ thuộc domain agent đó.
- Không có policy evidence trước Policy Agent.

Coordinator chỉ kiểm tra invariant và identity; verifier vẫn recompute độc lập từ raw repository.

---

## 16. Trace lifecycle

Chốt API với M5 trước khi code.

Interface đề xuất:

```text
TraceRun.start(path, run_id) -> TraceRun
TraceRun.write(event) -> None
TraceRun.finalize() -> None
TraceRun.abort_and_finalize(error) -> None
```

Implementation ownership thuộc M5.

### 16.1. Event schema

```text
run_id
sequence
recorded_at_utc
event_type
case_id | null
stage
from_agent | null
to_agent | null
status
duration_ms | null
payload_summary
errors
```

### 16.2. Quy tắc

- Ghi vào temp trace cùng filesystem.
- Sequence tăng đơn điệu.
- Không append trace run cũ.
- Finalize bằng `os.replace(temp, trace.jsonl)`.
- Batch fail vẫn finalize trace hiện tại.
- Không ghi:
  - API key.
  - `.env`.
  - full customer message nếu không cần.
  - toàn bộ raw CSV row.
  - stack trace chứa path/secret không scrub.
- Handoff trace chỉ ghi summary và evidence IDs cần audit.
- `run_id` không chứa thông tin nhạy cảm.

---

## 17. Atomic file I/O

Tạo helper dùng chung trong M1:

```text
atomic_write_json(path, data)
```

Quy trình:

1. Tạo temp file trong cùng directory.
2. Ghi UTF-8.
3. Flush.
4. `os.fsync()` khi platform hỗ trợ.
5. `os.replace(temp, target)`.
6. Cleanup temp khi lỗi.

Áp dụng cho:

- `logging/input_validation.json`
- mỗi staged output
- batch summary nội bộ nếu có

### 17.1. Transactional publish

Target chính xác:

```text
output/EC_001.json ... output/EC_050.json
```

Quy trình:

1. Audit staging có đúng 50 target, không file lạ.
2. Parse lại toàn bộ staged JSON.
3. Xác nhận filename ↔ `case_id`.
4. Tạo backup directory cùng filesystem.
5. Di chuyển target cũ vào backup bằng `os.replace`.
6. Di chuyển staged target vào `output/`.
7. Nếu bất kỳ bước nào fail:
   - xóa file mới đã publish trong transaction.
   - restore target cũ từ backup.
   - báo `OUTPUT_PUBLISH_FAILED`.
8. Nếu success:
   - xóa backup.
9. Không đụng `.gitkeep` hoặc artifact ngoài danh sách target.

---

## 18. Performance và complexity

### 18.1. Mục tiêu

- Load 9 CSV đúng một lần.
- Không đọc lại orders CSV trong input validator.
- Lookup theo index.
- Không deepcopy repository.
- Không serialize handoff nhiều lần nếu không cần.
- Không parallel hóa ở phiên bản nộp.

### 18.2. Complexity kỳ vọng

Gọi:

- `R`: tổng số dòng CSV.
- `C = 50`.
- `I_c`: item rows của case.
- `P_c`: payment rows của case.
- `H_c`: order history của customer.

Thời gian:

```text
O(R + Σ(I_c + P_c + H_c))
```

Memory:

```text
O(R)
```

Batch phải tránh:

```text
O(C × R)
```

do scan CSV cho từng case.

### 18.3. Benchmark instrumentation

Trace hoặc batch summary ghi:

- repository load duration.
- input validation duration.
- mỗi stage duration theo case.
- total batch duration.
- verified/blocked count.

Không đặt SLA giả khi chưa có dataset benchmark. Sau vertical slice và full batch, ghi số đo thực tế vào báo cáo, không ước lượng như số liệu đã đo.

---

## 19. Security và integrity

- Không log secret.
- Không đọc `.env` trong Member 1 nếu pipeline không dùng model.
- Chỉ dùng path resolve từ root repo.
- Không nhận filename từ ticket để tạo output path; output filename được derive từ validated `case_id`.
- Chặn path traversal.
- Mở input read-only.
- Không sửa raw CSV.
- Không eval code hoặc deserialize object không an toàn.
- JSON parser standard library.
- Error message được scrub trước trace.
- ZIP submission được tạo bởi M5 từ exact allowlist 50 output file.

---

## 20. Test plan

## 20.1. `tests/test_contracts.py`

### Ticket

- Valid round-trip.
- Unicode tiếng Việt.
- Missing top-level field.
- Missing nested field.
- Unknown field.
- Wrong type.
- Bool impostor `1`/`"true"`.
- Empty message/order ID.
- Invalid case pattern.
- Wrong language/scope/policy.
- Input dict không bị mutate.

### Handoff

- Valid envelope cho từng agent.
- Thiếu từng field.
- Unknown field.
- Wrong `case_id`.
- Wrong `from_agent`.
- Wrong `to_agent`.
- Invalid task/next task.
- Facts không phải object.
- Array có phần tử không phải string.
- Empty evidence.
- Stable serialization.

### Domain facts

- Customer full count vs projection.
- Historical order không chứa claimed order.
- Order no-item.
- Projection cap.
- Payment no-item null rules.
- Decimal tolerance `0.10`.
- Delivery missing timestamp.
- Seller earliest shipping limit.
- Policy confidence luôn 1.0.
- AggregatedFacts namespaced và không collision.

### Serialization

- `ensure_ascii=False`.
- Stable key order.
- Stable output bytes.
- Decimal conversion đúng 2 chữ số.
- Unsupported object bị từ chối rõ ràng.

---

## 20.2. `tests/test_run_batch.py`

### Infrastructure

- Missing input directory.
- Missing data directory.
- Repository init failure.
- Không tạo được staging/log.
- Fatal error không đụng output cũ.

### Manifest

- Đủ đúng 50 file.
- Missing file.
- Extra `EC_051.json`.
- Wrong casing.
- Wrong zero-padding.
- `.gitkeep` được giữ.
- File ngoài pattern được bỏ qua.

### Input

- Malformed UTF-8.
- Invalid JSON.
- JSON root là list.
- Filename/case mismatch.
- Duplicate case ID block tất cả liên quan.
- Duplicate order ID block tất cả liên quan.
- Order không tồn tại.
- Validation report thay thế, không append.
- Không thay đổi bytes/mtime input.

### Lifecycle

- Repository init đúng một lần.
- Case chạy filename order.
- Một case fail, case sau vẫn chạy.
- Không publish partial batch.
- Output cũ giữ nguyên khi fail.
- Publish 50/50 thành công.
- Publish failure rollback đầy đủ.
- Exit `0` chỉ khi full success.
- Exit `1` cho mọi failure.
- Temp/backup được cleanup.
- Không xóa artifact ngoài allowlist.

---

## 20.3. `tests/test_coordinator.py`

Dùng fake repository, agents, policy, verifier và trace.

### Happy path

- Gọi đúng thứ tự:
  - Customer
  - OrderProduct
  - Payment
  - Delivery
  - Policy
  - Verifier
- Mỗi agent gọi đúng một lần.
- Repository object giống nhau ở mọi agent.
- Policy nhận AggregatedFacts đúng.
- Verifier nhận output, facts và repository đúng.
- Verified result chỉ khi verifier trả `[]`.

### Contract failure

- Envelope invalid.
- Domain facts invalid.
- Wrong case/order/customer.
- Seller ngoài order facts.
- Invalid evidence.
- Invalid policy decision.
- Không gọi stage sau khi stage trước block.

### Runtime failure

- Mỗi agent raise exception.
- Policy raise exception.
- Verifier raise exception.
- Output serialization fail.
- Trace write fail được ghi nhận theo policy đã chốt.
- Unexpected error thành structured error.

### Gate behavior

- Verifier có một lỗi → blocked.
- Blocked result không có output.
- Không ghi staging trước verifier pass.
- Không tạo fallback output.
- Historical order không lọt affected entities.
- Full counts điều khiển secondary issues dù projection bị cap.

---

## 20.4. Vertical-slice integration

Sau khi M2–M5 có implementation tối thiểu:

1. Chạy `EC_001`.
2. Inspect handoff từng stage.
3. Recompute thủ công:
   - join identity.
   - total tiền.
   - variance.
   - primary precedence.
   - refund.
   - evidence.
4. Chỉ chạy 50 case sau khi EC_001 verifier pass.

Sau đó chạy:

```bash
python3 -m unittest discover -s tests -v
python3 -m src.run_batch
```

Xác nhận:

- Exit `0`.
- 50 output.
- 50/50 verifier pass.
- Không output unverified.
- Trace là run hiện tại.
- Input validation report success.
- Chạy lại tạo output bytes giống lần trước.

---

## 21. Thứ tự triển khai

### Phase 0 — Contract freeze

1. Viết constants/enums.
2. Viết Ticket contract.
3. Viết Handoff contract.
4. Viết domain fact contracts.
5. Viết PolicyDecision.
6. Viết StructuredError/RunResult/ValidationReport.
7. Viết contract examples.
8. M2–M5 review và ký nhận interface.

Không chuyển Phase 1 khi contract chưa được duyệt.

### Phase 1 — Contract tests

- Viết test trước cho toàn bộ validation và serialization.
- Chạy pass độc lập, chưa cần agent implementation.

### Phase 2 — Input validator và batch shell

- Manifest discovery.
- Ticket parsing.
- Duplicate detection.
- Atomic input-validation report.
- Repository factory injection.
- Staging lifecycle.
- Chưa cần output thật.

### Phase 3 — Coordinator bằng fakes

- Implement orchestration.
- Implement handoff/domain validation.
- Implement consistency checks.
- Implement output assembly.
- Implement verifier gate.
- Test toàn bộ bằng fake dependencies.

### Phase 4 — Cross-member vertical slice

- Nhận một sample handoff thật từ M2/M3.
- Nhận policy decision thật từ M4.
- Nhận verifier/trace implementation từ M5.
- Chạy EC_001.

### Phase 5 — Full batch và quality gate

- 50 cases.
- Deterministic rerun.
- Transactional publish.
- Final trace.
- ZIP audit do M5.

---

## 22. Yêu cầu bàn giao cho thành viên khác

### M2 phải xác nhận

- Repository load/index một lần.
- Có method kiểm tra order tồn tại không scan CSV lại.
- Agent trả đúng CustomerFacts và OrderProductFacts.
- Full rows/count không bị truncate trước tính toán.
- Thứ tự source được giữ.

### M3 phải xác nhận

- Dùng Decimal và rounding đã chốt.
- Payment tổng từ full rows.
- No-item null semantics.
- Delivery seller limit dùng minimum timestamp.
- Missing timestamp không đổi thành False/0.

### M4 phải xác nhận

- Policy nhận AggregatedFacts namespaced.
- Precedence đúng V2.
- Không fallback.
- Confidence 1.0.
- Policy evidence chính xác.
- Action/secondary order ổn định.

### M5 phải xác nhận

- Verifier signature giữ nguyên.
- Verifier recompute độc lập.
- Trace lifecycle API.
- Trace atomic replace.
- ZIP exact allowlist.

---

## 23. Definition of Done của Member 1

Member 1 hoàn thành khi:

- `contracts.py` là nguồn contract duy nhất.
- Runtime validation bắt được schema drift.
- Repository khởi tạo đúng một lần mỗi batch.
- Input validator kiểm tra đủ 50 filename và toàn bộ ticket invariant.
- Duplicate case/order block tất cả case liên quan.
- Case lỗi không dừng case hợp lệ khác.
- Coordinator gọi đúng thứ tự sáu stage.
- Không business rule bị copy vào coordinator.
- Không output nào được stage trước verifier pass.
- Không publish partial batch.
- Publish có rollback.
- Output cũ không bị xóa khi run fail.
- `input_validation.json` và trace thay thế atomically.
- Mọi lỗi có code/stage rõ ràng.
- Cùng input/CSV tạo output deterministic.
- Unit/integration tests pass.
- `python3 -m src.run_batch` trả `0`.
- Có đúng 50 file `EC_001.json`–`EC_050.json`.
- Không sửa input, CSV hoặc source M2–M5.
- Không secret trong trace/log/output.
- 50 output qua independent verifier.

---

## 24. Acceptance checklist trước merge

- [ ] Contract được M2–M5 review.
- [ ] Không còn `dict[str, Any]` không được parse tiếp ở boundary quan trọng.
- [ ] Không dùng `float` để tính tiền.
- [ ] Không dùng `set` trực tiếp làm mất thứ tự output.
- [ ] Không glob xóa output.
- [ ] Không scan CSV theo từng case.
- [ ] Không global mutable repository/trace.
- [ ] Không catch exception rồi bỏ qua.
- [ ] Không fallback policy/output.
- [ ] Không publish khi 49/50.
- [ ] Test tolerance ±0.10.
- [ ] Test no-item.
- [ ] Test missing timestamp.
- [ ] Test policy precedence.
- [ ] Test publish rollback.
- [ ] Test deterministic rerun.
- [ ] Test input bytes không đổi.
- [ ] Test trace không chứa secret.
- [ ] Full batch 50/50 pass.

---

## 25. Pseudocode tham chiếu

```python
def main() -> int:
    run_id = new_run_id()
    trace = None
    staging = None

    try:
        paths = resolve_repo_paths()
        validate_infrastructure(paths)

        trace = start_trace_run(paths.trace_file, run_id)
        manifest = discover_manifest(paths.input_dir)

        parsed = parse_and_validate_tickets(manifest)
        repository = OlistRepository(paths.data_dir)  # exactly once

        validation_report = validate_release(
            run_id=run_id,
            manifest=manifest,
            parsed_tickets=parsed,
            repository=repository,
        )
        atomic_write_json(paths.input_validation_file, validation_report.to_dict())

        staging = create_staging_directory(paths, run_id)
        results = []

        for case_id in expected_case_ids():
            ticket_or_error = validation_report.case_entry(case_id)

            if not ticket_or_error.valid:
                results.append(blocked_result_from_validation(ticket_or_error))
                continue

            result = process_case(
                ticket=ticket_or_error.ticket,
                repository=repository,
                trace=trace,
            )
            results.append(result)

        success = (
            validation_report.success
            and len(results) == 50
            and all(result.status == "verified" for result in results)
            and audit_staging(staging)
        )

        if not success:
            trace.write(batch_failed_event(results))
            return 1

        publish_outputs_transactionally(staging, paths.output_dir)
        trace.write(batch_succeeded_event(results))
        return 0

    except Exception as exc:
        if trace is not None:
            trace.write(sanitized_fatal_event(exc))
        return 1

    finally:
        cleanup_staging_safely(staging)
        finalize_trace_safely(trace)
```

```python
def process_case(ticket, repository, trace) -> RunResult:
    try:
        customer = call_validate_and_parse_customer(ticket, repository, trace)
        order_product = call_validate_and_parse_order_product(ticket, repository, trace)
        payment = call_validate_and_parse_payment(ticket, repository, trace)
        delivery = call_validate_and_parse_delivery(ticket, repository, trace)

        facts = aggregate_and_cross_validate(
            ticket,
            customer,
            order_product,
            payment,
            delivery,
        )

        policy = call_validate_and_parse_policy(facts, trace)
        output = assemble_output(ticket, facts, policy)

        verifier_errors = verify_output(output, facts, repository)
        if verifier_errors:
            return RunResult.blocked(
                ticket.case_id,
                verification_errors(verifier_errors),
            )

        atomic_write_json(staging_path(ticket.case_id), output)
        return RunResult.verified(ticket.case_id, output)

    except PipelineError as exc:
        return RunResult.blocked(ticket.case_id, [exc.to_structured_error()])

    except Exception as exc:
        return RunResult.blocked(
            ticket.case_id,
            [unexpected_internal_error(exc)],
        )
```

---

## 26. Kết quả kỳ vọng

Sau khi plan này được triển khai:

- Pipeline có độ phức tạp tuyến tính theo dữ liệu nguồn cộng dữ liệu liên quan của 50 case.
- Mọi boundary liên nhóm được kiểm tra runtime.
- Không có ambiguity về tiền, timestamp, confidence, null hoặc ordering.
- Không mất output tốt do run mới thất bại.
- Không có partial submission vô tình được publish.
- Lỗi được khoanh vùng đúng owner và đúng stage.
- Verifier vẫn là independent hard gate.
- Repo có thể chạy lại nhiều lần với kết quả ổn định và audit được.
