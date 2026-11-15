# T09 · Multi-Market Privacy Decision Gate · Phạm Trọng Hiện (G04)

Policy YAML phiên bản `t09-1.0` có nguồn điều khoản và ngày hiệu lực được khai báo trong cấu hình. Pipeline kiểm tra manifest, ghi giải thích và đưa cả 12 case giả định vào hàng đợi. Kết quả là quyết định trong bài tập. [Policy](../configs/policies/t09.yaml)

| Phương án trên 12 case | GO đề xuất | HOLD | REWORK |
|---|---:|---:|---:|
| Consent-only | 4 | 8 | 0 |
| HOLD tất cả | 0 | 12 | 0 |
| PoC | 2 | 9 | 1 |

Hai đề xuất GO của PoC là CASE-06 và CASE-10, cùng đang `PENDING`. Quyết định cấp lượt chạy là `HOLD`. CASE-02 minh họa lý do cần xem hồ sơ chuyển dữ liệu ngay cả khi case có thông tin đồng ý. Đối chiếu chính thức trên 4 case eval cần GT của giảng viên. Báo Critical-miss Rate kèm guardrail false HOLD ≤ 0,5, trong đó baseline HOLD tất cả cần được chấm riêng.

```powershell
.\.venv\Scripts\python.exe -m src.main --config configs/lab_t09.yaml --track t09 --output out/t09/poc --offline --seed 42
.\.venv\Scripts\python.exe -m src.main --config configs/lab_t09.yaml --track t09 --pipeline src.examples.t09_baseline --output out/t09/baseline --offline --seed 42
.\.venv\Scripts\python.exe -m src.main --config configs/lab_t09.yaml --track t09 --pipeline src.examples.t09_all_hold_baseline --output out/t09/all-hold --offline --seed 42
```

Người phụ trách xem `out/t09/poc/review_cases.html` và lưu CSV đã duyệt thành `review_queue_reviewed.csv` cùng thư mục. Audit checklist và nhật ký truy vết quyết định sau khi chạy lại. [T09](../docs/tracks/T09.md)
