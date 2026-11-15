# Day06 · T06–T08 · Đặng Minh Huy (G04)

Ba challenge có 5 bước contract (load → process → rank → review → decide) trong
`src/tracks/` (dispatch từ `src/pipeline.py`):

| Track | PoC module | Thuật toán | Output |
|---|---|---|---|
| T06 | `src/tracks/t06_gate.py` | So invariant semantic với parent `releases/v1.0` + lịch sử label map: trùng ID, category so với label map khai báo (stale_map / class_swap), frame rơi / `frame_index` đứt quãng, bbox dịch chuyển có hệ thống (ngưỡng chốt trước: ≥10% box chung dịch, mean ≥3 px; jitter lành tính <1 px), sha256 parent | `out/t06/poc/release_decisions.csv` (+ `release_report.json` máy đọc được) |
| T07 | `src/tracks/t07_redaction.py` | Detector CPU không weights: khớp palette da tổng hợp (ngày + đêm) → blob mặt (kèm heuristic mắt-thấp = mặt bị che → nới box xuống); rectangle sáng = biển số `SYN-…`; pad 2 px; blur Gaussian vùng che; queue ưu tiên vùng nhỏ/tối/bị che | `out/t07/poc/redactions.csv`, `out/t07/poc/redacted/*.jpg`, `contact_sheet.html` |
| T08 | `src/tracks/t08_deletion.py` | BFS lineage (raw → backup/frame/crop/embedding/export) theo `subject_ids` + `parent_ids`; chuỗi ưu tiên chốt trước: legal hold → retain_legal_hold, unknown origin / ACL thiếu owner → escalate, đa chủ thể → escalate, còn lại delete; mọi hành động ghi `audit_log.jsonl` | `out/t08/poc/actions.csv`, `deletion_evidence.csv` |

Baseline tầm thường (docs `4) trong `src/tracks/baselines.py`: T06 chỉ parse schema (mọi release PASS),
T07 detector màu thô (da YCrCb + pixel sáng, pad cố định — OpenCV 5.0 đã bỏ API Haar cascade) + blur cố định không queue (và mode `blur_all` = baseline tầm thường phải báo: miss rate 0 nhưng over-redaction = 1 → guardrail FAIL),
T08 chỉ xóa 1 raw theo filename, bỏ qua derived/backup.

Lệnh chạy (offline, seed 42):

```bash
.venv\Scripts\python.exe -m src.main --config configs/lab_t06.yaml --track t06 --output out/t06/poc --run-name poc --offline --seed 42
.venv\Scripts\python.exe -m src.main --config configs/lab_t06.yaml --track t07 --output out/t07/poc --run-name poc --offline --seed 42
.venv\Scripts\python.exe -m src.main --config configs/lab_t06.yaml --track t08 --output out/t08/poc --run-name poc --offline --seed 42
.venv\Scripts\python.exe -m src.main --config configs/lab_t06.yaml --track t06 --pipeline src.tracks.baselines --output out/t06/baseline --run-name baseline --offline --seed 42   # t07/t08 tương tự
```

Điểm chính thức cần bộ nhãn của giảng viên. Nhóm tự kiểm bằng chín test của T06–T08. Biểu mẫu review T07 mở offline.

## Kết quả chạy lại

| Track | Đầu ra PoC | Quyết định trong fixture |
|---|---|---|
| T06 | 20 release | HOLD |
| T07 | 407 vùng đề xuất trên 60 ảnh | GO sau khi xuất ảnh đã che |
| T08 | 164 hành động từ 10 yêu cầu | GO cho lượt mô phỏng |

PoC và baseline đều chạy offline với exit 0. T07 cần đối chiếu độ bỏ sót và che dư khi có nhãn. T08 mô phỏng hành động từ đồ thị lineage. Chưa có tệp quyết định của người duyệt kèm lượt chạy này.
