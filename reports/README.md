# reports/

| File | Ai tạo | Cách tạo |
|---|---|---|
| `group_report.md` | nhóm | điền template |
| `metric_contract.md` | nhóm | điền ở phút 30 |
| `metrics_baseline.json` | **script** | `python -m src.evaluate --track tXX --pred out/baseline/<file> --run-name baseline --out reports/metrics_baseline.json` |
| `metrics_poc.json` | **script** | `python -m src.evaluate --track tXX --pred out/poc/<file> --run-name poc --out reports/metrics_poc.json` |
| `table1_baseline_vs_poc.md` | **script** | `python -m src.compare reports/metrics_baseline.json reports/metrics_poc.json --out reports/table1_baseline_vs_poc.md` |
| `review_queue.csv` | pipeline + reviewer | copy từ `out/poc/review_queue.csv` sau khi review (có cột reviewer/decision) |
| `decision_log.csv` | pipeline | copy từ `out/poc/decision_log.csv` |
| `team_contributions.csv` | nhóm | khớp với `git shortlog -sne --all` |

Các file metrics/table **không được sửa tay** — giảng viên sẽ chạy lại lệnh để đối chiếu.

## Các track lưu cùng repo

Hồ sơ chính của G04 dùng T01, gồm `group_report.md`, `metric_contract.md`, `review_queue.csv` và `decision_log.csv`. Các báo cáo bên dưới lưu kèm để theo dõi phần việc theo từng người. Đầu ra chạy lại sinh trong `out/`.

- G04 · [T02](t02_report.md), [T03](t03_report.md), [T06–T08](t06_t07_t08_report.md) và [T09](t09_report.md).
- G08 · [T12 của Võ Nguyễn Thành Phát](t12_report.md).

- [T04, T05, T10, T11, T13 và T14](additional_tracks.md) · PoC, baseline và các bước review bổ sung.
