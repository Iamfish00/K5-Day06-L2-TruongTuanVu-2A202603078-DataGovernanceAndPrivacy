# T03 Development QC Report

- Scope: 240 synthetic development images only.
- Labels: frozen usability rule in dev transform manifest.
- This is not official holdout evaluation.

## Confusion matrix

- TP: 80; FP: 26; FN: 0; TN: 134
- Precision: 0.7547
- Recall: 1.0000
- F1: 0.8602

## Results by transform and severity

|              |   images |   predicted_unusable |   actual_unusable |
|:-------------|---------:|---------------------:|------------------:|
| ('blur', 0)  |       20 |                    0 |                 0 |
| ('blur', 1)  |       20 |                   16 |                 0 |
| ('blur', 2)  |       20 |                   20 |                20 |
| ('fog', 0)   |       20 |                    0 |                 0 |
| ('fog', 1)   |       20 |                   10 |                 0 |
| ('fog', 2)   |       20 |                   20 |                20 |
| ('glare', 0) |       20 |                    0 |                 0 |
| ('glare', 1) |       20 |                    0 |                 0 |
| ('glare', 2) |       20 |                   20 |                20 |
| ('night', 0) |       20 |                    0 |                 0 |
| ('night', 1) |       20 |                    0 |                 0 |
| ('night', 2) |       20 |                   20 |                20 |

## Suggested train/hold split

- Train candidates: images with actual_unusable = 0.
- Hold/review candidates: images with actual_unusable = 1.
- Keep all severity and transform metadata for reproducibility.
