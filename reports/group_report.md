# Group Report — Day 06 Data Lab (Field-to-Release PoC)

> Điền đầy đủ, cụ thể, không viết chung chung. Mọi con số phải lấy từ file do script sinh ra
> (`reports/metrics_*.json`, `reports/table1_baseline_vs_poc.md`) — không gõ tay, không làm tròn mất đơn vị.
> 3–5 trang (hoặc README + report.md tương đương).

## 00 · Header

| Mục | Giá trị |
|---|---|
| Group ID | G04 |
| Challenge | T06 · Semantic Integrity & Provenance Gate · T07 · Face/Plate Redaction + Review Queue · T08 · Retention & Delete Request Simulator |
| Thành viên (5–6) + vai trò | 1. Đặng Minh Huy — MSSV 2A202603162 (thực thi toàn bộ: Lead/Integrator, core algorithm T06–T08, baseline, tests, review + report) |
| Git URL | https://github.com/Iamfish00/K5-Day06-L2-TruongTuanVu-2A202603078-DataGovernanceAndPrivacy |
| Tag `demo-180m` (commit) | ____ |
| Tag `final-24h` (commit) | ____ |
| Deadline (giờ VN) | ____ |

## 01 · Problem statement

- Ai bị ảnh hưởng / ai chịu hậu quả khi hệ thống sai? ____
- Điểm nghẽn cụ thể: ____
- Đầu vào → đầu ra: ____
- Tiêu chí GO / HOLD / REWORK (chốt trước khi chạy eval): ____
- Giả định của nhóm (cần DPO/legal/domain owner xác nhận): ____

## 02 · Data & provenance

| Split | N | Nguồn / link | License | Synthetic? |
|---|---|---|---|---|
| dev | | | | |
| eval / heldout | | | | |

- Schema & class mapping: ____
- Seed: 42 · Cách chia split (theo scene/group/segment?): ____
- `manifest.csv` verified (sha256): có / không — lệnh: `python -m tools.verify_manifests`
- Dữ liệu thật dùng (nếu có) và cách xây GT cho nó: ____

## 03 · Research & design

- 2–3 references (URL + ngày xem) và **vì sao** chọn kỹ thuật: ____
- Sơ đồ data flow (input → process → rank/QC → review → decision → report): ____
- Thành phần offline (model/weights + checksum + license nếu có): ____
- Trade-offs đã cân nhắc: ____

## 04 · Baseline vs PoC

- Baseline (mô tả, version/config, commit): ____
- PoC (mô tả, version/config, commit): ____
- Cùng budget / cùng heldout / cùng seed: xác nhận ____

**Bảng 1 · Baseline vs PoC & hard slices** — dán output của
`python -m src.compare reports/metrics_baseline.json reports/metrics_poc.json`:

| Metric (unit) | Baseline | PoC | Δ & chú thích |
|---|---|---|---|
| Primary: ______ | ____ | ____ | ____; higher/lower? |
| Hard-slice: ______ | ____ | ____ | ____; N=__ |
| Review@B / miss / FP | ____ | ____ | ____; B=__ |
| Runtime / 100 items | ____ | ____ | ____; CPU=__ |

## 05 · Evaluation protocol

- Công thức primary metric, đơn vị đánh giá, TP/FP/FN, denominator: ____ (xem `reports/metric_contract.md`)
- Threshold / budget B / K và lúc chốt: ____
- Độ bất định (CI/bootstrap/variance) khi N nhỏ: ____
- Lệnh evaluate chính xác: `python -m src.evaluate --track t__ --pred ... --out ...`

## 06 · Result slices

| Slice | N | Baseline | PoC | Ghi chú |
|---|---|---|---|---|
| | | | | |
| | | | | |

- Failure case 1 (ảnh/metadata + root cause): ____
- Failure case 2: ____
- Latency / compute (máy, CPU, ms/item): ____

## 07 · Human & decision

- Review queue (file, B, cách ưu tiên): ____
- Cách override + audit log (`out/audit_log.jsonl`): ____
- Decision policy + `reason_code` + owner: ____
- Khi nào HOLD/REWORK thay vì tự động release: ____

## 08 · Reproduce & tests

```bash
make setup          # hoặc: python -m venv .venv && pip install -r requirements.txt
python -m src.main --input fixtures/<track> --config configs/lab.yaml --output out/ --offline --seed 42
python -m src.evaluate --track t__ --pred out/<file> --out reports/metrics_poc.json
pytest -q
```

- Output của `pytest -q`: ____
- Máy / CPU / OS / Python: ____ · git sha: ____

## 09 · Limitations & ethics

- Rủi ro pháp lý / PII / license: ____
- Điều chưa biết; false alarms; nếu scale 10× thì sao: ____
- Không tuyên bố "đạt GDPR / luật Việt Nam" từ checklist tự động; giả định cần DPO/legal xác nhận: ____
- Kế hoạch cải tiến: ____

## 10 · Team contribution

**Bảng 2 · Individual contribution evidence** (đối chiếu với `git shortlog -sne --all` và `reports/team_contributions.csv`)

| Học viên | Git author/email | Module / vai trò | Commit SHA / diff chứng minh | Trình bày? |
|---|---|---|---|---|
| Member 1 | | Integration / CLI | | |
| Member 2 | | Data + GT | | |
| Member 3 | | Core algorithm | | |
| Member 4 | | Metric + tests | | |
| Member 5 | | Review queue + report | | |
| Member 6 (nếu có) | | Research / ablation / debate | | |

## 11 · References

| URL | Ngày xem | 1–2 dòng ý nghĩa (không chép nguyên văn) |
|---|---|---|
| | | |

---

**Bảng 3 · Checklist chốt khi nộp**

- [ ] Link repo và tag cuối cùng trong README; input mẫu không chứa PII thật.
- [ ] Đúng 1 lệnh run, 1 lệnh evaluate, 1 lệnh tests; không cần download khi chạy.
- [ ] Có baseline/PoC metrics, ≥1 failure case, reviewer evidence, decision log.
- [ ] Có license/source provenance, data splits, seed, offline environment, GT/eval N.
- [ ] Mỗi thành viên có ≥1 commit thực chất và được nêu minh bạch trong bảng trên.

## 12 · Execution summary — người thực thi: Đặng Minh Huy (Dang Minh Huy), MSSV 2A202603162, nhóm G04

Ba challenge đã cài đầy đủ 5 bước contract (load → process → rank → review → decide) trong
`src/tracks/` (dispatch từ `src/pipeline.py`):

| Track | PoC module | Thuật toán | Output |
|---|---|---|---|
| T06 | `src/tracks/t06_gate.py` | So invariant semantic với parent `releases/v1.0` + lịch sử label map: trùng ID, category so với label map khai báo (stale_map / class_swap), frame rơi / `frame_index` đứt quãng, bbox dịch chuyển có hệ thống (ngưỡng chốt trước: ≥10% box chung dịch, mean ≥3 px; jitter lành tính <1 px), sha256 parent | `out/t06/poc/release_decisions.csv` (+ `release_report.json` máy đọc được) |
| T07 | `src/tracks/t07_redaction.py` | Detector CPU không weights: khớp palette da tổng hợp (ngày + đêm) → blob mặt (kèm heuristic mắt-thấp = mặt bị che → nới box xuống); rectangle sáng = biển số `SYN-…`; pad 2 px; blur Gaussian vùng che; queue ưu tiên vùng nhỏ/tối/bị che | `out/t07/poc/redactions.csv`, `out/t07/poc/redacted/*.jpg`, `contact_sheet.html` |
| T08 | `src/tracks/t08_deletion.py` | BFS lineage (raw → backup/frame/crop/embedding/export) theo `subject_ids` + `parent_ids`; chuỗi ưu tiên chốt trước: legal hold → retain_legal_hold, unknown origin / ACL thiếu owner → escalate, đa chủ thể → escalate, còn lại delete; mọi hành động ghi `audit_log.jsonl` | `out/t08/poc/actions.csv`, `deletion_evidence.csv` |

Baseline tầm thường (docs §4) trong `src/tracks/baselines.py`: T06 chỉ parse schema (mọi release PASS),
T07 detector màu thô (da YCrCb + pixel sáng, pad cố định — OpenCV 5.0 đã bỏ API Haar cascade) + blur cố định không queue (và mode `blur_all` = baseline tầm thường phải báo: miss rate 0 nhưng over-redaction = 1 → guardrail FAIL),
T08 chỉ xóa 1 raw theo filename, bỏ qua derived/backup.

Lệnh chạy (offline, seed 42):

```bash
.venv\Scripts\python.exe -m src.main --track t06 --output out/t06/poc --run-name poc --offline --seed 42
.venv\Scripts\python.exe -m src.main --track t07 --output out/t07/poc --run-name poc --offline --seed 42
.venv\Scripts\python.exe -m src.main --track t08 --output out/t08/poc --run-name poc --offline --seed 42
.venv\Scripts\python.exe -m src.main --track t06 --pipeline src.tracks.baselines --output out/t06/baseline --run-name baseline --offline --seed 42   # t07/t08 tương tự
```

Metrics chấm chính thức (`src.evaluate`) cần `fixtures/*/gt/` của overlay giảng viên — repo học viên
không có gt/ nên số liệu primary sẽ do giảng viên chạy; nhóm tự kiểm bằng test đơn vị
(`tests/test_group_pipelines.py`: 9 case success + failure/edge cho cả 3 track) và review thủ công
contact sheet T07. Ngưỡng/tham số đều chốt trước khi có bất kỳ kết quả eval nào (không có gt để tune).
