# T12 · Vendor Batch Acceptance · Võ Nguyễn Thành Phát (G08)

Tập dữ liệu offline có 5 batch, mỗi batch 100 mẫu. PoC lấy 20 mẫu mỗi batch theo hai tầng rủi ro. Baseline lấy ngẫu nhiên đơn giản 20 mẫu mỗi batch. Cả hai dùng seed 42 và ngưỡng lỗi 5% từ policy. [Cấu hình G08](../configs/lab_t12.yaml)

Mỗi lượt chạy sinh `audit_sample.csv`, `batch_decisions.csv`, trang review và nhật ký. Bản học viên hiện ở `HOLD / NO_AUDIT_LABELS` trên cả 5 batch và có `n_audited=0`. Các ước lượng để trống khi thiếu bằng chứng. Nhãn giả trong test làm cả 5 batch cần làm lại, và chính sách tổng trả `REWORK`. Ca này chỉ kiểm logic tổng hợp.

PoC ước lượng tỷ lệ lỗi theo trọng số tầng. Khi đánh giá điều kiện ACCEPT, nó lấy xác suất đuôi dưới lớn nhất trong các cách chia số lỗi tối thiểu của một batch xấu vào hai tầng. Khoảng Wilson theo cỡ mẫu hiệu dụng là xấp xỉ. Với 20 mẫu, ngay cả khi chưa thấy lỗi vẫn có thể phải HOLD. Chấm MAE, Bad-batch Recall và guardrail HOLD rate cần nhãn audit/GT thật.

```powershell
.\.venv\Scripts\python.exe -m src.main --config configs/lab_t12.yaml --track t12 --output out/t12/poc --offline --seed 42
.\.venv\Scripts\python.exe -m src.main --config configs/lab_t12.yaml --track t12 --pipeline src.tracks.baselines --output out/t12/baseline --offline --seed 42
```

Người audit có thể nhập cặp `sample_id,is_error` của đúng kế hoạch mỗi phương pháp vào `audit_labels.csv` trong thư mục lượt chạy tương ứng. Giảng viên có thể dùng `AuditOracle` với bộ nhãn của lớp. Trang `contact_sheet.html` hỗ trợ ghi lại quyết định và lý do của người duyệt. [T12](../docs/tracks/T12.md)
