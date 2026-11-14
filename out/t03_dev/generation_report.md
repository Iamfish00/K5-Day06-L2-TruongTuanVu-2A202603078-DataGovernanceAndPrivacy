# T03 Synthetic Dev Generation Report

- Dataset: synthetic simulation only; NOT real weather data.
- Base seed: 42
- Original dev images: 20
- Generated images: 240
- Transforms: night, glare, fog, blur
- Severities: 0, 1, 2

## Counts by transform and severity

| transform   |   severity |   images |
|:------------|-----------:|---------:|
| blur        |          0 |       20 |
| blur        |          1 |       20 |
| blur        |          2 |       20 |
| fog         |          0 |       20 |
| fog         |          1 |       20 |
| fog         |          2 |       20 |
| glare       |          0 |       20 |
| glare       |          1 |       20 |
| glare       |          2 |       20 |
| night       |          0 |       20 |
| night       |          1 |       20 |
| night       |          2 |       20 |

## Usability labels

unusable
0    160
1     80

These labels follow the frozen usability rule for generated dev data.
They are not official holdout ground truth or evaluation metrics.

## Provenance

- Manifest: `out/t03_dev/transform_manifest.csv`
- Before/after review: `out/t03_dev/dev_contact_sheet.html`
- Each row records parent, transform, severity, seed, parameters,
  parent SHA-256 and generated-image SHA-256.
