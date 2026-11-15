# Group Report — Day 06 Data Lab

## 00 · Header

| Mục | Giá trị |
|---|---|
| Group ID | G04 |
| Challenge | T01 · Coverage Planner |
| Thành viên và vai trò | Trương Tuấn Vũ (T01–T02), Hà Thanh Tùng (T03), Đặng Minh Huy (T06–T08), Phạm Trọng Hiện (T09) |
| Git URL | [Repo Day06](https://github.com/Iamfish00/K5-Day06-L2-TruongTuanVu-2A202603078-DataGovernanceAndPrivacy) |
| Tag `demo-180m` (commit) | |
| Tag `final-24h` (commit) | |
| Deadline (giờ VN) | |

## 01 · Problem statement

- Nhóm thu dữ liệu tốn chi phí quay lại nếu chọn nhiều clip cùng kịch bản. Nhóm kiểm thử mô hình cũng thiếu bằng chứng ở các cảnh rủi ro mà bộ dữ liệu chưa phủ.
- Với ngân sách K clip, ưu tiên phủ các ô chưa có mà có tổng trọng số cao, rồi xác định cảnh cần xác minh hoặc quay thêm.
- Đầu vào gồm danh sách clip và bảng ô mục tiêu. Đầu ra gồm `pick.csv`, `coverage.csv`, hàng đợi duyệt, `capture_plan.csv` và `decision.json`.
- Pipeline đưa ra `HOLD` khi còn mục chưa được duyệt, `REWORK` khi duyệt xong nhưng còn ô thiếu và `GO` khi đã duyệt và metadata phủ toàn bộ ô mục tiêu. `GO` chỉ áp dụng cho kế hoạch trong lab.
- Chi phí trong `cost_min` là ước tính của fixture. Việc xin quyền quay và xử lý dữ liệu thật cần xác nhận trước khi thu.

## 02 · Data & provenance

| Split | N | Nguồn | License | Synthetic |
|---|---:|---|---|---|
| Tập chính | 120 clip | `candidates.csv`, fixture Day06, seed 42 | CC0-1.0 | Có |
| Holdout | 25 clip | `holdout_candidates.csv`, fixture Day06, seed 42 | CC0-1.0 | Có |
| Ô mục tiêu | 15 ô | `targets.csv`, cùng bộ ô cho hai pool | CC0-1.0 | Có |

- Clip được mô tả qua `route`, `weather`, `light` và `object`. Trọng số gắn với ô trong `targets.csv`. Ký hiệu `*` trong ô chấp nhận mọi giá trị ở chiều tương ứng. Tag `unknown` chỉ khớp khi chiều mục tiêu là `*`.
- Dùng hai danh sách đã tách sẵn. Giữ trọng số gốc và seed 42 trên mọi lần chạy. Không dùng dữ liệu người thật.
- Đã kiểm SHA-256 của ba file bằng `tools.verify_manifests`. SHA-256 của manifest là `ba2c4ea11bf6a4a1d9ce367cad3967b278ec147f2b754d69304e7a7b812439b0`. [Manifest](../fixtures/t01_coverage/manifest.csv)

## 03 · Research & design

- Kiểm manifest → ghép clip với ô mục tiêu → chọn clip theo trọng số tăng thêm → đưa 3 ô cần xem xét vào hàng đợi → ghi quyết định và kế hoạch quay.
- Greedy set cover ưu tiên clip bổ sung các ô chưa được phủ theo tổng trọng số.
- PoC phá hòa theo thứ tự kịch bản chưa chọn, chi phí thấp hơn và `clip_id`. Cách này giữ mục tiêu tăng độ phủ ở từng bước và có thể giảm trùng hoặc giảm chi phí. Greedy đưa ra một phương án theo từng bước, chưa chứng minh tối ưu toàn cục.
- Chạy offline bằng CPU, đọc CSV và dùng quy tắc cố định. Không có model hay checkpoint.

## 04 · Baseline vs PoC

Ba phương pháp dùng cùng pool, ngân sách, tập mục tiêu và seed 42. Random lấy K clip theo điểm ngẫu nhiên. Greedy cơ sở ưu tiên tổng trọng số ô mới và dùng `clip_id` để phá hòa. PoC dùng cùng tiêu chí độ phủ, thêm ưu tiên kịch bản khác và chi phí. Mã nguồn của lượt chạy ban đầu dựa trên `b0ce8bf`. Bản tích hợp có commit `44a6cd7`.

**Độ phủ trọng số tính từ metadata đầu vào**, lấy từ CSV do script sinh. Bảng điểm xác minh chờ GT của giảng viên.

| Pool và ngân sách | Random | Greedy | PoC |
|---|---:|---:|---:|
| main, K=10 | 8.33% (4/48) | 72.92% (35/48) | 72.92% (35/48) |
| main, K=20 | 27.08% (13/48) | 81.25% (39/48) | 81.25% (39/48) |
| main, K=30 | 37.50% (18/48) | 81.25% (39/48) | 81.25% (39/48) |
| holdout, K=6 | 14.58% (7/48) | 39.58% (19/48) | 39.58% (19/48) |

| Chỉ số trên pool chính, K=30 | Random | Greedy | PoC |
|---|---:|---:|---:|
| Ô được phủ | 7/15 | 13/15 | 13/15 |
| Ô khó được phủ | 1/4 | 4/4 | 4/4 |
| Clip trùng kịch bản | 12 | 7 | 0 |
| Chi phí thu | 232 phút | 266 phút | 138 phút |
| Chi phí / ô được phủ | 33.14 phút/ô | 20.46 phút/ô | 10.62 phút/ô |
| Thời gian pipeline / 100 clip | 29.8 ms | 50.4 ms | 54.4 ms |

PoC và greedy đạt cùng độ phủ metadata ở cả bốn thiết lập đã chạy. PoC giảm chi phí dự kiến trên pool chính K=30 từ 266 xuống 138 phút và số kịch bản trùng từ 7 xuống 0. Trên holdout, PoC phủ 2/4 ô khó theo metadata.

[Biểu đồ so sánh](../demo/t01_coverage.png)

## 05 · Evaluation protocol

- Mỗi đơn vị đánh giá là một ô mục tiêu. Chỉ tính trọng số mỗi ô một lần dù nhiều clip khớp.
- Độ phủ trên metadata dùng tag đầu vào. Weighted Coverage@K chính thức dùng tag đã xác minh. Ghép `clip_id` trong đầu ra với bảng xác minh, rồi so 4 chiều của clip với ô hoặc wildcard.
- Tập chính K=10, 20, 30. Holdout K=6. Hàng đợi người duyệt B=3. Cùng seed và ngân sách cho các phương pháp.
- Thử nghiệm dùng mỗi cấu hình một lượt và giữ nguyên phương pháp khi chạy holdout. Chưa có ước tính khoảng tin cậy. Với 4 ô khó, cần báo cả số đếm và tỷ lệ.
- Chạy `src.evaluate` với `pick.csv` sau khi có GT. Lệnh cho holdout thêm `--gt fixtures/t01_coverage/gt/holdout_verified_tags.csv --k 6` và dùng file chọn clip của holdout. [Metric contract](metric_contract.md)

## 06 · Result slices

| Slice | N ô | Random | Greedy | PoC |
|---|---:|---:|---:|---:|
| Ô khó, pool chính K=30 | 4 | 1/4 | 4/4 | 4/4 |
| Ô khó, holdout K=6 | 4 | 1/4 | 2/4 | 2/4 |

- C13 thiếu cảnh đô thị, sương mù, ban đêm có người đi bộ. `clip_093` có tag ánh sáng `unknown`, vì vậy chưa đủ bằng chứng để tính ô này là đã phủ.
- C15 thiếu cảnh hẻm, trời mưa, chạng vạng có người đi bộ. `clip_027` và `clip_084` cùng có tag `day`, nên tăng số clip tương tự vẫn để sót ô chạng vạng.
- C09 có `clip_108` khớp cả bốn chiều theo CSV. Fixture không có video để xác nhận nội dung và chỉ một clip khớp theo metadata, nên ưu tiên kiểm chứng trước khi dùng làm bằng chứng.
- K=30 PoC chạy 0.0653 giây với 120 clip trên Intel Core i7-10610U, Windows 11, Python 3.14.5. Thời gian là một lần chạy pipeline và chưa gồm công mở Python hoặc thời gian reviewer.

## 07 · Human & decision

Hàng đợi `out/poc/review_queue.csv` đưa ô còn thiếu lên trước, sau đó ưu tiên ô khó và trọng số. Mở `out/poc/contact_sheet.html` để xem các tag, clip khớp và xuất CSV đã duyệt.

| Ô | Đề xuất | Người duyệt xác nhận | Quyết định trong lần chạy |
|---|---|---|---|
| C13 | Xác minh thời điểm `clip_093`. Nếu vẫn thiếu, thu một clip đô thị có sương mù ban đêm và người đi bộ. | | `edit` |
| C15 | Thu clip hẻm trời mưa lúc chạng vạng, có người đi bộ. | | `edit` |
| C09 | Xác minh `clip_108`. Nếu không đạt, thu một clip dự phòng về xe máy trong đô thị trời mưa ban đêm. | | `approve` |

- Lưu quyết định thành `out/poc/review_queue_reviewed.csv`. Với `edit`, nhập hành động mới vào `override_value`. Sau khi chạy lại, pipeline ghi `audit_log.jsonl` và áp dụng thay đổi vào `capture_plan.csv` của ô thiếu.
- Pipeline đã ghi ba quyết định vào `audit_log.jsonl`. Kết quả `REWORK / COVERAGE_GAPS` vì C13 và C15 còn thiếu clip theo metadata. Bản rà thử dựa trên metadata. Các ô xác nhận vẫn để trống. C09 giữ `clip_108` theo CSV và chưa xác minh bằng video. [Bảng review](review_queue.csv) · [Nhật ký quyết định](decision_log.csv)

## 08 · Reproduce & tests

```powershell
.\.venv\Scripts\python.exe -m tools.verify_manifests fixtures/t01_coverage
.\.venv\Scripts\python.exe -m src.main --config configs/lab.yaml --track t01 --output out/poc --run-name poc --offline --seed 42
.\.venv\Scripts\python.exe -m src.main --track t01 --pipeline src.examples.random_baseline --output out/baseline --run-name baseline --offline --seed 42
.\.venv\Scripts\python.exe -m src.evaluate --track t01 --pred out/poc/pick.csv --run-name poc --out reports/metrics_poc.json
.\.venv\Scripts\python.exe -m pytest -o addopts="" -q
```

- Kiểm thử thực tế · 49 pass, 39 skip vì thiếu GT, 0 fail. Hai test T01 kiểm tra đủ K clip, ô thiếu và từ chối dòng `edit` chưa có nội dung thay thế. Bộ test sau tích hợp có 65 đạt, 39 bỏ qua và 0 lỗi.
- `src.evaluate` cần gói nhãn giảng viên. Khi có GT, chạy tương tự cho `out/baseline/pick.csv` với `--run-name baseline --out reports/metrics_baseline.json`, sau đó chạy `src.compare`.

## 09 · Limitations & ethics

- Fixture chỉ gồm CSV tổng hợp. Tag thiếu hoặc sai có thể làm phủ giả hoặc bỏ sót ô. Điểm theo metadata không đo được độ chính xác này.
- Với pool lớn hơn, có thể lập chỉ mục clip theo các chiều kịch bản và chỉ cập nhật điểm tăng thêm của clip liên quan khi phủ một ô mới. Đo lại thời gian và giữ hàng đợi duyệt có ngân sách cố định.
- Với dữ liệu thật, cần xác nhận quyền quay, thời hạn giữ dữ liệu và cách xử lý mặt người, biển số hoặc định danh khác trước khi chia sẻ.

## 10 · Team contribution

| Thành viên | Nhóm | Phần việc | Commit đối chiếu |
|---|---|---|---|
| Trương Tuấn Vũ | G04 | T01–T02 và tích hợp | `44a6cd7` |
| Hà Thanh Tùng | G04 | T03 | `bee7fb7` |
| Đặng Minh Huy | G04 | T06–T08 | `dcbf54b` |
| Phạm Trọng Hiện | G04 | T09 | `620c9e3` |
| Võ Nguyễn Thành Phát | G08 | T12 | `2ebaaaf` |

Phần T12 của G08 có báo cáo riêng. [Báo cáo T12](t12_report.md) · [Bảng đóng góp](team_contributions.csv)

## 11 · References

| URL | Ngày xem | 1–2 dòng ý nghĩa (không chép nguyên văn) |
|---|---|---|
| | | |
