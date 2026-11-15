# Day06 · Các track T04, T05, T10, T11, T13 và T14

Cấu hình nộp chính của G04 là T01. Sáu PoC bổ sung chạy với fixture đi kèm, kiểm SHA-256 từ manifest, seed 42 và chế độ offline.

| Track | PoC | Baseline | Kết quả chạy trên fixture |
|---|---|---|---|
| [T04 · Đồng bộ camera và IMU](../docs/tracks/T04.md) | Tương quan sự kiện, ước lượng offset và drift cục bộ, phát hiện reset và ngắt ghép khi quá xa mẫu IMU | Ghép timestamp gần nhất, tolerance 50 ms | 3570 frame, PoC giữ lại 44 frame ở HOLD, baseline 46. Shock có 1 reset, PoC HOLD 0/1200, baseline 450/1200. |
| [T05 · Chống trùng và rò rỉ split](../docs/tracks/T05.md) | SHA-256, pHash, dHash, histogram và graph nối các cặp trùng hoặc burst | Chỉ exact hash, chia ảnh ngẫu nhiên | 120 ảnh, PoC gợi ý 136 cặp trùng. Baseline thấy 20 cặp trùng byte. Split PoC có 84 train, 36 test. 0 cặp PoC dự đoán trùng bị chia đôi theo split. |
| [T10 · Sai lệch miền](../docs/tracks/T10.md) | Histogram, độ sắc nét, confidence và mức bất định so với source | Chênh lệch sáng trung bình | 6 batch, 240 ảnh. PoC gắn cờ 4 batch, baseline 2. |
| [T11 · Ưu tiên rà lỗi nhãn](../docs/tracks/T11.md) | IoU, bất đồng lớp, dấu hiệu box trùng và dự đoán chưa có box | Xếp ngẫu nhiên với seed 42 | 260 mục trên 55 ảnh, 10 mục heldout vào hàng đợi. |
| [T13 · Tìm mẫu hiếm](../docs/tracks/T13.md) | Novelty theo láng giềng embedding, ưu tiên đa dạng và giảm mẫu nhiễu/trùng | Chọn ngẫu nhiên 20 ảnh | Chọn 20/200 ảnh. Cả hai có 0 cặp gần trùng theo ngưỡng pHash đã dùng. Cờ chất lượng từ rule là PoC 0, baseline 0. |
| [T14 · Auto-annotation và export](../docs/tracks/T14.md) | Ánh xạ taxonomy, kiểm biên box, ưu tiên duyệt và xuất YOLO/COCO | Cùng ngưỡng mặc định, xuất trực tiếp | Từ 234 prediction mô phỏng, xuất 227 box trên 60 ảnh. Baseline xuất 227 box. Trước khi có sửa của reviewer, hai bản có cùng số box. |

Số lượt HOLD của T04 mô tả quyết định gán cặp, chưa đo độ chính xác timestamp. Số cặp trùng của T05 và số cờ của T10/T13 là kết quả của rule trên input. Điểm benchmark cần nhãn giảng viên để đối chiếu.

## Cách chạy trên PowerShell

Giữ bộ thư mục chạy theo track để chạy song song với T01. Đổi giá trị `$Track` theo bảng trên.

```powershell
$Track = 't04'
.\.venv\Scripts\python.exe -m src.main --track $Track --output "out/$Track/poc" --run-name poc --offline --seed 42
.\.venv\Scripts\python.exe -m src.main --track $Track --pipeline src.tracks.baselines --output "out/$Track/baseline" --run-name baseline --offline --seed 42
.\.venv\Scripts\python.exe -m src.main --config configs/lab_t04_shock.yaml --offline --seed 42
.\.venv\Scripts\python.exe -m pytest -q
```

Các file để chấm lần lượt là `pairs.csv`, `suspect_pairs.csv`, `shift_scores.csv`, `ranking.csv`, `selected.csv` và `predictions.csv`. Khi có GT, chạy `python -m src.evaluate --track <mã> --pred <CSV>` cho cả hai phương pháp. Với T10, báo Spearman kèm khoảng tin cậy bootstrap và bảng 6 batch. T14 dùng prediction của fixture có nguồn và SHA-256 ghi trong `export_provenance.json`.

## Review và điểm còn phải xem

Mỗi PoC sinh `review_queue.csv` và `contact_sheet.html`. Sau khi có quyết định thật, lưu `review_queue_reviewed.csv` vào cùng thư mục rồi chạy lại. CSV được kiểm khớp với hàng đợi hiện tại. Audit log ghi người duyệt, lý do và thay đổi. Các lượt thử với `TEST_FIXTURE` chỉ dùng để kiểm chương trình.

- **T04.** Xem `offset.png` và năm cặp được đưa lên đầu. Đổi sang HOLD khi không xác nhận được cặp. Trong shock, reset xuất hiện ở `f00750`. Kiểm bằng sóng tổng hợp có offset 85 ms xác nhận sai số khôi phục trung vị không quá 5 ms trong ca thử.
- **T05.** Xem cặp quanh ngưỡng thay vì chỉ xem exact hash. `im_011` và `im_078` có tông nền giống nhau nhưng vị trí xe và biển báo khác khi xem ảnh. Cần xác nhận hoặc bác bỏ cặp này trước khi dùng group để chia dữ liệu thật. Không có cặp dự đoán trùng băng split chỉ kiểm được tính nhất quán của graph.
- **T10.** `b3_camera` có điểm mô tả cao nhất trong PoC. Baseline theo sáng không gắn cờ batch này. Muốn biết sự thay đổi ảnh có thực sự làm tụt model phải đối chiếu hiệu năng thật. `slice_dashboard.csv` giữ cả các thành phần điểm và hành động review.
- **T11.** `ann_d00` lên đầu do tín hiệu box trùng, `img:lbl_18` lên hàng đợi do dự đoán chưa có box khớp. Người xem ảnh cần xác nhận trước khi xóa hoặc thêm box. `corrected_annotations.csv` xuất nhãn đã sửa, `label_changes.csv` lưu diff. Không tính sửa được năm lỗi khi chưa có reviewer xác nhận.
- **T13.** Đối chiếu ảnh tối hữu ích với ảnh tối do nhiễu. Trang duyệt cho phép LABEL, SKIP hoặc HOLD. Hành động được lưu trong `label_queue.csv`. `selected.csv` giữ danh sách top 20 ban đầu để so sánh công bằng với baseline.
- **T14.** ID model 0 là `person`, nhưng ID dự án 0 là `car`. Export ánh xạ qua tên `pedestrian` rồi mới lấy ID dự án. Bản trước review là `predictions_before_review.csv`. Các file `labels/*.txt` và `annotations.json` lấy từ cùng bản nhãn. Thử nghiệm sửa box đã xác nhận tọa độ và lớp đồng nhất giữa hai định dạng.

T11 và T14 dùng giá trị JSON khi chọn `edit` trong bảng review. Ví dụ `{"class":"car","x":5,"y":6,"w":15,"h":20}`. Với T11, box có sẵn được sửa theo ID; dòng `img:<id>` thêm box. Với T14, `reject` của một box sẽ bỏ box đó khỏi export.
