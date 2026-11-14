# T03 – Baseline vs PoC comparison

## Phạm vi
- 240 ảnh dev synthetic; hai phương pháp được đánh giá trên cùng tập ảnh.
- Nhãn đối chiếu: `actual_unusable` trong `qc.csv`.
- Không sử dụng holdout và không công bố đây là kết quả đánh giá holdout.

## Kết quả

| Metric | Baseline | PoC |
|---|---:|---:|
| TP | 40 | 80 |
| FP | 40 | 26 |
| FN | 40 | 0 |
| TN | 120 | 134 |
| Precision | 0.5000 | 0.7547 |
| Recall | 0.5000 | 1.0000 |
| F1 | 0.5000 | 0.8602 |
| Accuracy | 0.6667 | 0.8917 |

## Sai số theo phương pháp

| Transform | Baseline FP | Baseline FN | PoC FP | PoC FN |
|---|---:|---:|---:|---:|
| blur | 20 | 0 | 16 | 0 |
| fog | 0 | 20 | 10 | 0 |
| glare | 0 | 20 | 0 | 0 |
| night | 20 | 0 | 0 | 0 |

## Lưu ý
- Đây là kết quả trên tập dev synthetic, không đại diện cho dữ liệu thực tế.
- Không suy diễn kết quả holdout từ báo cáo này.
- Usability rule đã được cố định trước đánh giá; cần giữ nguyên khi diễn giải kết quả.
