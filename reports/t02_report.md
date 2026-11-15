# Day06 · T02 Raw QC Triage

## Dữ liệu và mục tiêu

T02 xếp hạng ảnh nghi mờ, thiếu sáng, lóa cục bộ hoặc nén mạnh để người duyệt mở tối đa 20 ảnh. Fixture có 96 ảnh tổng hợp CC0-1.0 từ 24 cảnh. Tập dev có 56 ảnh, tập eval có 40 ảnh. Các ảnh của một cảnh gốc thuộc cùng split. Kiểm SHA-256 từ [manifest](../fixtures/t02_raw_qc/manifest.csv) đã đạt.

Đầu ra là `ranked_review.csv` gồm `image_id` và `score` cho toàn bộ ảnh, kèm loại lỗi và hành động gợi ý. Hàng đợi của cả hai phương pháp lấy 20 ảnh điểm cao nhất trong eval.

## Baseline và PoC

- **Baseline Laplacian** xếp ảnh theo nghịch đảo độ sắc nét của toàn ảnh. Ngưỡng dựa trên phân vị 25% của độ sắc nét trên dev.
- **PoC** lấy tín hiệu mạnh nhất trong bốn tín hiệu. Mờ dùng độ sắc nét so với trung vị dev của cùng camera. Tối dùng độ sáng so với trung vị dev của cùng điều kiện ánh sáng. Lóa dùng mức bão hòa trong ô 16 × 16 pixel so với dev cùng điều kiện ánh sáng. Nén dùng chênh lệch biên ở chu kỳ JPEG 8 pixel.
- Ngưỡng điểm 1 dùng để gợi ý `KEEP` hoặc `RECAPTURE`. Gợi ý vẫn cần người duyệt. Hai phương pháp chạy offline, seed 42, trên cùng ảnh và budget. Thống kê tham chiếu chỉ lấy từ dev.

## Kết quả nội bộ

| Kiểm tra trên eval | Laplacian | PoC |
|---|---:|---:|
| Số ảnh trong pool | 40 | 40 |
| Số ảnh đưa vào hàng đợi | 20 | 20 |
| Ảnh ngày / chạng vạng / đêm trong hàng đợi | 9 / 3 / 8 | 10 / 2 / 8 |
| Ảnh cam_A / cam_B trong hàng đợi | 11 / 9 | 14 / 6 |
| Thời gian một lượt pipeline, gồm đọc ảnh và tạo HTML | 0,7524 s | 0,4832 s |

Hai hàng đợi có 15 ID ảnh chung. Các số trên mô tả thứ hạng và phân bố theo metadata. Thời gian mỗi phương pháp chỉ đo một lần. Cả hai dùng chung bước trích đặc trưng và tạo HTML, nên số đo chưa đủ để kết luận phương pháp nào nhanh hơn.

| Ảnh đã xem | Hạng Laplacian → PoC | Quan sát |
|---|---:|---|
| [img_05_3](../fixtures/t02_raw_qc/images/img_05_3.jpg) | 31 → 10 | Vùng sáng mạnh quanh xe khiến PoC đưa ảnh vào top 20 và gợi ý `glare`. |
| [img_06_3](../fixtures/t02_raw_qc/images/img_06_3.jpg), [img_03_2](../fixtures/t02_raw_qc/images/img_03_2.jpg) | 8 → 39, 11 → 40 | Hai ảnh đêm có quầng sáng rõ bị PoC bỏ khỏi top 20. Tín hiệu điểm ảnh bão hòa có thể bỏ qua quầng sáng rộng nhưng chưa đạt ngưỡng pixel. |
| [img_03_0](../fixtures/t02_raw_qc/images/img_03_0.jpg) | 13 → 32 | Ảnh đêm tối tự nhiên vẫn thấy được xe và người đi bộ. Xếp theo độ nét toàn ảnh có thể đưa cảnh đêm hữu ích lên cao. |
| [img_15_0](../fixtures/t02_raw_qc/images/img_15_0.jpg) | 10 → 37 | Ảnh chạng vạng của cam_B vẫn nhìn rõ các hình chính. Ưu tiên độ sắc nét tuyệt đối có thể đưa ảnh camera mềm hơn lên cao. |
| [img_01_3](../fixtures/t02_raw_qc/images/img_01_3.jpg) | 27 → 14 | PoC đưa vào top 20 với gợi ý `compression`. Hạng cao là tín hiệu ưu tiên duyệt, chưa xác nhận loại lỗi. |

## Review và quyết định

Trang `out/t02/poc/contact_sheet.html` hiển thị 20 ảnh, điểm, tín hiệu chính và hành động đề xuất. Reviewer có thể duyệt, yêu cầu thu lại hoặc ghi đè quyết định với `KEEP` / `RECAPTURE`. Lưu CSV xuất từ trang này vào `out/t02/poc/review_queue_reviewed.csv`, rồi chạy lại PoC.

- `HOLD` khi chưa có đủ 20 quyết định xử lý.
- `RECAPTURE` khi đã duyệt đủ và còn ảnh được yêu cầu thu lại.
- `KEEP` khi đã duyệt đủ và mọi ảnh trong hàng đợi được giữ.

Bản rà thử trên 20 ảnh trong hàng đợi ghi nhận **17 `RECAPTURE` và 3 `KEEP`**. Có ba ảnh được đổi từ `KEEP` sang `RECAPTURE` vì nhiễu khối hoặc mất chi tiết ở biên đối tượng (`img_13_2`, `img_16_3`, `img_03_3`). Hai ảnh đêm `img_06_0`, `img_06_1` và ảnh ngày `img_23_0` được giữ vì các đối tượng chính còn rõ.

Chạy lại offline đạt `status=0`, ghi 20 lượt duyệt vào `audit_log.jsonl` và trả quyết định `RECAPTURE / CONFIRMED_IMAGE_DEFECTS` cho hàng đợi. Các quyết định và lý do từng ảnh được lưu trong báo cáo dưới dạng CSV. Nhật ký quyết định gồm trạng thái trước và sau khi nạp bản rà thử. [Bảng rà ảnh](t02_review_queue.csv) · [Nhật ký quyết định](t02_decision_log.csv) Lượt rà này đánh giá độ rõ của 20 ảnh ưu tiên. Recall@20 và Precision@20 chính thức chờ bộ nhãn xác minh.

## Kiểm tra 10 ảnh ngoài miền

Chọn 10 ảnh đầu theo `image_id` từ split eval của fixture T07, chạy ở kích thước gốc 640 × 360. Đây là ảnh tổng hợp CC0 có mặt và biển số giả từ một bài khác. Hai phương pháp dùng tham chiếu từ 56 ảnh dev T02 và giữ nguyên ngưỡng. T07 thiếu tag camera và ánh sáng, vì vậy PoC dùng thống kê chung của dev T02. Đã kiểm hash manifest T07. [Nguồn ảnh](../fixtures/t07_redaction/manifest.csv)

| Mẫu T07 | Baseline | PoC | Quan sát |
|---|---|---|---|
| `pii_01`, `pii_03`, `pii_20` | 3 KEEP | 3 RECAPTURE, tín hiệu `dark` | Các cảnh đêm có vùng nền tối nhưng xe và người chính vẫn thấy rõ. Cần xem lại khi điều kiện ánh sáng bị thiếu trong metadata. |
| `pii_04`, `pii_05`, `pii_06`, `pii_09`, `pii_13`, `pii_17`, `pii_18` | 7 KEEP | 7 KEEP | Các cảnh ngày hoặc chạng vạng vẫn nhận ra đối tượng chính khi xem ảnh. |

Kết quả mô tả hành động gợi ý trên 10 mẫu kiểm tra. Điểm lỗi được xác minh chỉ tính với bộ nhãn dành cho bài QC.

## Chạy lại và chấm

```powershell
.\.venv\Scripts\python.exe -m src.main --config configs/lab.yaml --track t02 --pipeline src.examples.laplacian_baseline --output out/t02/baseline --run-name baseline --offline --seed 42
.\.venv\Scripts\python.exe -m src.main --config configs/lab.yaml --track t02 --output out/t02/poc --run-name poc --offline --seed 42
.\.venv\Scripts\python.exe -m pytest -o addopts="" -q
```

Để tái lập lượt rà sau khi chạy PoC lần đầu, chép `reports/t02_review_queue.csv` vào `out/t02/poc/review_queue_reviewed.csv` rồi chạy lại lệnh PoC ở trên. Bản rà thử chưa có xác nhận của người duyệt.

Kiểm thử trên máy gồm 51 đạt, 39 bỏ qua và 0 lỗi. Hai test T02 xác minh đầu ra, ngân sách theo eval và lỗi ảnh hỏng.

Metric chính thức là Defect Recall@20, tính số ảnh lỗi thật trong top 20 chia cho tổng số ảnh lỗi thật của eval. Precision@20 dùng cùng tử số chia cho 20. Khi có `fixtures/t02_raw_qc/gt/labels.csv`, chạy `src.evaluate --track t02 --pred <ranked_review.csv> --split eval --budget 20` cho từng phương pháp. Lần chạy evaluator trên máy dừng với exit 2 vì thiếu file nhãn.

Đề bài và hợp đồng đầu ra. [T02](../docs/tracks/T02.md)
