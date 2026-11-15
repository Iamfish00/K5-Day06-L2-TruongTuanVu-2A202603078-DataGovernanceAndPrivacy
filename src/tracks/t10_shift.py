"""Compare visual and prediction-score distributions to the source batch."""

import numpy as np
import pandas as pd
from scipy.stats import wasserstein_distance

from .review_stage import finalize, make_queue
from ..common.image_ops import color_histogram, laplacian_variance, load_rgb, to_gray
from ..pipeline import verify_inputs


def run(ctx, *, baseline=False):
    verify_inputs(ctx)
    images = pd.read_csv(ctx.input_dir / "images.csv")
    scores = pd.read_csv(ctx.input_dir / "model_predictions.csv")
    table = images.merge(scores, on=["image_id", "batch_id"], validate="one_to_one")
    if len(table) != len(images) or not scores.pred_score.between(0, 1).all():
        raise ValueError("Model predictions must cover images once, with scores in [0, 1]")
    reference = str(ctx.param("reference_batch", "b0_source"))
    rows = []
    for row in table.itertuples(index=False):
        img = load_rgb(ctx.input_dir / row.path)
        confidence = float(row.pred_score)
        rows.append({"batch_id": row.batch_id, "path": row.path,
                     "brightness": float(to_gray(img).mean()) / 255,
                     "sharpness": float(np.log1p(laplacian_variance(img))),
                     "histogram": color_histogram(img), "confidence": confidence,
                     "margin": abs(confidence - .5) * 2})
    features = pd.DataFrame(rows)
    anchor = features.loc[features.batch_id.eq(reference)]
    if anchor.empty:
        raise ValueError(f"Missing reference batch {reference}")
    ref_hist = np.stack(anchor.histogram).mean(axis=0)
    results = []
    for batch, subset in features.groupby("batch_id"):
        brightness = abs(float(subset.brightness.mean() - anchor.brightness.mean()))
        hist = float(np.abs(np.stack(subset.histogram).mean(axis=0) - ref_hist).sum() / 2)
        sharp = min(1.0, wasserstein_distance(subset.sharpness, anchor.sharpness) / 4)
        conf = wasserstein_distance(subset.confidence, anchor.confidence)
        margin_drop = max(0.0, float(anchor.margin.mean() - subset.margin.mean()))
        score = brightness if baseline else .25 * hist + .15 * sharp + .35 * conf + .25 * margin_drop
        results.append({"item_id": batch, "batch_id": batch, "n_images": len(subset),
                        "brightness_shift": brightness, "histogram_shift": hist, "sharpness_shift": sharp,
                        "confidence_shift": conf, "margin_drop": margin_drop, "score": score,
                        "reason": "source" if batch == reference else "compare_to_source",
                        "example_path": subset.path.iloc[0]})
    result = pd.DataFrame(results).sort_values(["score","batch_id"], ascending=[False, True])
    limit = float(ctx.param("shift_review_threshold", .10))
    result["pred_high_risk"] = result.score.ge(limit).astype(int)
    result["suggested_action"] = np.where(result.pred_high_risk.eq(1), "HOLD", "KEEP")
    result["review_action"] = result.suggested_action
    reviewed, events = [], []
    if not baseline:
        targets = result[~result.batch_id.eq(reference)]
        _, reviewed = make_queue(ctx, targets, len(targets), "T10 - Batch shift dashboard",
                                 image_col="example_path", fields=("brightness_shift", "histogram_shift",
                                                                   "sharpness_shift", "confidence_shift", "margin_drop"))
        for row in reviewed:
            mask = result.batch_id.eq(row.item_id)
            before = result.loc[mask,"review_action"].item()
            after = {"reject": "HOLD", "escalate": "HOLD"}.get(row.reviewer_decision, before)
            if row.reviewer_decision == "edit":
                after = row.override_value.strip().upper()
                if after not in {"KEEP","HOLD"}:
                    raise ValueError("T10 edits use KEEP or HOLD")
            result.loc[mask,"review_action"] = after
            events.append((row, before, after))
    result.rename(columns={"score":"shift_score"})[["batch_id","shift_score","pred_high_risk"]].to_csv(
        ctx.out("shift_scores.csv"), index=False)
    result.drop(columns="item_id").to_csv(ctx.out("slice_dashboard.csv"), index=False)
    ctx.stats.update(n_images=len(images), n_flagged=int(result.pred_high_risk.sum()))
    held = result.loc[result.review_action.eq("HOLD"), "batch_id"].tolist()
    reviewed_all = (len(reviewed) == len(result) - 1
                    and all(row.reviewer_decision in {"approve", "edit", "reject"} for row in reviewed))
    finalize(ctx, len(result), reviewed, events, ["shift_scores.csv", "slice_dashboard.csv"],
             hold_reason=f"Batches held: {', '.join(held)}." if held else None,
             keep=reviewed_all and not held, baseline=baseline)
