"""T03 development-only quality control. Never reads holdout or GT."""
from pathlib import Path

import numpy as np
import pandas as pd

from .common.image_ops import (
    load_rgb, brightness_stats, laplacian_variance, crop
)


def ratio(a, b):
    return float(a / max(float(b), 1e-6))


def main():
    manifest_path = Path("out/t03_dev/transform_manifest.csv")
    manifest = pd.read_csv(manifest_path)
    originals = pd.read_csv("fixtures/t03_synthetic/originals.csv")
    objects = pd.read_csv("fixtures/t03_synthetic/objects.csv")

    original_paths = dict(zip(originals.image_id.astype(str), originals.path))
    bbox_groups = {
        str(image_id): group
        for image_id, group in objects.groupby("image_id")
    }

    results = []

    for row in manifest.itertuples(index=False):
        parent_id = str(row.parent_image)
        before_path = Path("fixtures/t03_synthetic") / original_paths[parent_id]
        after_path = Path(row.path)

        before = load_rgb(before_path)
        after = load_rgb(after_path)

        before_stats = brightness_stats(before)
        after_stats = brightness_stats(after)

        brightness_ratio = ratio(after_stats["mean"], before_stats["mean"])
        std_ratio = ratio(after_stats["std"], before_stats["std"])
        sharpness_ratio = ratio(
            laplacian_variance(after),
            laplacian_variance(before),
        )

        diff = np.maximum(
            after.astype(float) - before.astype(float), 0
        ).mean(axis=2)
        peak_y, peak_x = np.unravel_index(np.argmax(diff), diff.shape)
        glare_peak = float(diff[peak_y, peak_x])

        boxes = bbox_groups.get(parent_id, pd.DataFrame())
        peak_inside_bbox = any(
            box.x <= peak_x <= box.x + box.w
            and box.y <= peak_y <= box.y + box.h
            for box in boxes.itertuples(index=False)
        )

        risks = {
            "night": (
                brightness_ratio < 0.10
                and after_stats["frac_dark"] > 0.95
            ),
            "fog": (
                brightness_ratio > 1.40
                and std_ratio < 0.60
                and sharpness_ratio < 0.18
            ),
            "blur": sharpness_ratio < 0.0027,
            "glare": glare_peak > 162 or (glare_peak > 50 and peak_inside_bbox),
        }

        pred = int(any(risks.values()))
        results.append({
            "image_id": row.image_id,
            "pred_unusable": pred,
            "actual_unusable": int(row.unusable),
            "score": float(pred),
            "reason": ";".join(k for k, v in risks.items() if v)
                      or "no_high_risk_signal",
            "transform": row.transform,
            "severity": row.severity,
        })

    result = pd.DataFrame(results)
    result.to_csv("out/t03_dev/qc.csv", index=False)

    tp = int(((result.pred_unusable == 1) & (result.actual_unusable == 1)).sum())
    fp = int(((result.pred_unusable == 1) & (result.actual_unusable == 0)).sum())
    fn = int(((result.pred_unusable == 0) & (result.actual_unusable == 1)).sum())
    tn = int(((result.pred_unusable == 0) & (result.actual_unusable == 0)).sum())

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall else 0.0
    )

    lines = [
        "# T03 Development QC Report",
        "",
        "- Scope: 240 synthetic development images only.",
        "- Labels: frozen usability rule in dev transform manifest.",
        "- This is not official holdout evaluation.",
        "",
        "## Confusion matrix",
        "",
        f"- TP: {tp}; FP: {fp}; FN: {fn}; TN: {tn}",
        f"- Precision: {precision:.4f}",
        f"- Recall: {recall:.4f}",
        f"- F1: {f1:.4f}",
        "",
        "## Results by transform and severity",
        "",
        result.groupby(["transform", "severity"]).agg(
            images=("image_id", "count"),
            predicted_unusable=("pred_unusable", "sum"),
            actual_unusable=("actual_unusable", "sum"),
        ).to_markdown(),
        "",
        "## Suggested train/hold split",
        "",
        "- Train candidates: images with actual_unusable = 0.",
        "- Hold/review candidates: images with actual_unusable = 1.",
        "- Keep all severity and transform metadata for reproducibility.",
    ]

    Path("out/t03_dev/qc_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    print(f"QC rows: {len(result)}")
    print(f"Precision: {precision:.4f}")
    print(f"Recall:    {recall:.4f}")
    print(f"F1:        {f1:.4f}")
    print("Saved: out/t03_dev/qc.csv")
    print("Saved: out/t03_dev/qc_report.md")


if __name__ == "__main__":
    main()
