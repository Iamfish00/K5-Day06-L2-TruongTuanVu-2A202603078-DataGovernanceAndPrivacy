# Metric contract · T01

| Mục | Khai báo của nhóm |
|---|---|
| Track | T01 · Coverage Planner |
| Primary metric | Weighted Coverage@K |
| Hướng | Higher |
| Đơn vị đánh giá | Ô mục tiêu |
| Numerator / denominator | Tổng trọng số các ô được phủ / tổng trọng số tất cả 15 ô |
| Ghép prediction với GT | Ghép theo `clip_id`, kiểm `route`, `weather`, `light`, `object` và wildcard `*` của ô. Mỗi ô chỉ tính một lần. |
| Candidate pool cố định | 120 clip tập chính và 25 clip holdout tách sẵn |
| Budget | K=30 trên tập chính, thử K=10/20/30 và K=6 trên holdout. Review B=3 ô. |
| Seed | 42 |
| Threshold policy | So khớp đúng tag hoặc wildcard, trọng số theo `targets.csv`. Giữ nguyên khi chạy holdout. |
| Eval split | Tag xác minh của tập chính và holdout do giảng viên giữ |
| Chỉ số lỗi | Ô thiếu, tỷ lệ ô khó được phủ, clip trùng kịch bản, phút/ô |
| Slices | Ô khó ở tập chính, ô khó ở holdout. Phân tích riêng C13, C15 và C09. |
| Độ bất định | Báo số đếm và mẫu số. Mỗi thiết lập hiện có một lượt, chưa có CI. |
| Ngày giờ chốt và người chốt | |

Bảng metadata nội bộ dùng cùng cách đếm với tag ứng viên. Kết quả xác minh cần chạy evaluator khi có GT.
