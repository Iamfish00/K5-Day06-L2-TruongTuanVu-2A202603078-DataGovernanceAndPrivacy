
"""T03 Synthetic-vs-Real pipeline."""
from __future__ import annotations

import pandas as pd

from .common.context import RunContext
from .common.decision import Decision, write_decision
from .common.image_ops import (
    load_rgb,
    brightness_stats,
    laplacian_variance,
    crop,
)
from .common.manifest import verify_manifest
from .common.review import (
    load_review_decisions,
    make_review_queue,
    write_review_queue,
    apply_overrides,
)
from .metrics.tracks import get_track


# ------------------------------------------------------------
# 01 LOAD
# ------------------------------------------------------------
def load(ctx: RunContext) -> dict:
    root = ctx.input_dir

    manifest_path = root / "manifest.csv"
    problems = (
        verify_manifest(manifest_path)
        if manifest_path.exists()
        else [{"problem": "no_manifest"}]
    )

    if problems:
        ctx.audit.log(
            actor="pipeline",
            action="provenance_problems",
            item_id=str(manifest_path),
            after=problems[:20],
        )
        if ctx.config.get("strict_provenance", True):
            raise ValueError(
                f"provenance check failed: {problems[:3]}"
            )

    holdout = pd.read_csv(root / "holdout_transformed.csv")
    originals = pd.read_csv(root / "originals.csv")
    objects = pd.read_csv(root / "objects.csv")

    required_holdout = {"image_id", "path", "parent_image"}
    required_originals = {"image_id", "path"}
    required_objects = {"image_id", "x", "y", "w", "h"}

    if not required_holdout.issubset(holdout.columns):
        raise ValueError("holdout_transformed.csv thiếu cột bắt buộc")

    if not required_originals.issubset(originals.columns):
        raise ValueError("originals.csv thiếu cột bắt buộc")

    if not required_objects.issubset(objects.columns):
        raise ValueError("objects.csv thiếu cột bbox")

    if holdout["image_id"].duplicated().any():
        raise ValueError("holdout_transformed.csv có image_id trùng")

    return {
        "manifest": pd.read_csv(manifest_path),
        "holdout": holdout,
        "originals": originals,
        "objects": objects,
    }


# ------------------------------------------------------------
# 02 PROCESS
# ------------------------------------------------------------
def _ratio(a: float, b: float) -> float:
    return float(a / max(float(b), 1e-6))


def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    holdout = data["holdout"]
    originals = data["originals"]
    objects = data["objects"]

    original_paths = dict(
        zip(originals["image_id"].astype(str), originals["path"])
    )

    bbox_groups = {
        str(image_id): group
        for image_id, group in objects.groupby("image_id")
    }

    results = []
    provenance = []

    for row in holdout.itertuples(index=False):
        image_id = str(row.image_id)
        parent_id = str(row.parent_image)

        if parent_id not in original_paths:
            raise ValueError(
                f"Không tìm thấy ảnh gốc {parent_id} của {image_id}"
            )

        before_path = ctx.input_dir / original_paths[parent_id]
        after_path = ctx.input_dir / row.path

        if not before_path.exists() or not after_path.exists():
            raise FileNotFoundError(
                f"Không tìm thấy ảnh gốc hoặc ảnh biến đổi của {image_id}"
            )

        before = load_rgb(before_path)
        after = load_rgb(after_path)

        if before.shape != after.shape:
            raise ValueError(
                f"Kích thước ảnh không khớp: {image_id}"
            )

        before_stats = brightness_stats(before)
        after_stats = brightness_stats(after)

        brightness_ratio = _ratio(
            after_stats["mean"], before_stats["mean"]
        )
        std_ratio = _ratio(
            after_stats["std"], before_stats["std"]
        )
        sharpness_ratio = _ratio(
            laplacian_variance(after),
            laplacian_variance(before),
        )

        # Phân tích vùng bbox của ảnh gốc trên ảnh biến đổi.
        bbox_saturations = []

        for box in bbox_groups.get(parent_id, []).itertuples(index=False):
            patch = crop(after, box.x, box.y, box.w, box.h)

            if patch.size == 0:
                continue

            bbox_saturations.append(
                brightness_stats(patch)["frac_saturated"]
            )

        max_bbox_sat = (
            max(bbox_saturations) if bbox_saturations else 0.0
        )

        # Các tín hiệu rủi ro dựa trên hiệu chuẩn tập dev.
        night_risk = (
            brightness_ratio < 0.10
            and after_stats["frac_dark"] > 0.95
        )

        fog_risk = (
            brightness_ratio > 1.40
            and std_ratio < 0.60
            and sharpness_ratio < 0.18
        )

        blur_risk = sharpness_ratio < 0.002

        # Tín hiệu nghi ngờ glare trong bbox.
        # Không khẳng định được chính xác tâm glare.
        glare_risk = max_bbox_sat >= 0.15

        reasons = []

        if night_risk:
            reasons.append("night_severe_like")

        if fog_risk:
            reasons.append("fog_severe_like")

        if blur_risk:
            reasons.append("blur_severe_like")

        if glare_risk:
            reasons.append("bbox_glare_risk")

        # Điểm ưu tiên review, không phải xác suất đã hiệu chuẩn.
        night_score = (
            max(0.0, min(1.0, (0.15 - brightness_ratio) / 0.15))
            if after_stats["frac_dark"] > 0.80
            else 0.0
        )

        fog_score = (
            max(0.0, min(1.0, (0.80 - std_ratio) / 0.80))
            if brightness_ratio > 1.20
            else 0.0
        )

        blur_score = max(
            0.0,
            min(1.0, (0.01 - sharpness_ratio) / 0.01),
        )

        glare_score = max(
            0.0,
            min(1.0, max_bbox_sat / 0.50),
        )

        score = max(
            night_score,
            fog_score,
            blur_score,
            glare_score,
        )

        pred_unusable = int(
            night_risk or fog_risk or blur_risk or glare_risk
        )

        results.append({
            "image_id": image_id,
            "pred_unusable": pred_unusable,
            "score": float(score),
            "reason": (
                ";".join(reasons)
                if reasons
                else "no_high_risk_signal"
            ),
            "brightness_ratio": brightness_ratio,
            "std_ratio": std_ratio,
            "sharpness_ratio": sharpness_ratio,
            "dark_after": after_stats["frac_dark"],
            "max_bbox_saturation": max_bbox_sat,
        })

        # Chỉ ghi thông tin đã biết.
        # Không tự bịa transform, severity hoặc seed.
        provenance.append({
            "image_id": image_id,
            "parent_image": parent_id,
            "transform": pd.NA,
            "severity": pd.NA,
            "seed": pd.NA,
        })

    pd.DataFrame(provenance).to_csv(
        ctx.out("transform_manifest.csv"),
        index=False,
    )

    ctx.stats["transform_provenance_complete"] = 0.0

    return pd.DataFrame(results)


# ------------------------------------------------------------
# 03 RANK
# ------------------------------------------------------------
def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    budget = int(ctx.param("budget", 20))

    queue = make_review_queue(
        scored,
        id_col="image_id",
        score_col="score",
        budget=budget,
        reason_col="reason",
    )

    # Không ghi đè review_queue.csv cũ.
    queue_path = ctx.output_dir / "review_queue.csv"

    if not queue_path.exists():
        write_review_queue(queue, ctx.out("review_queue.csv"))

    return queue


# ------------------------------------------------------------
# 04 REVIEW
# ------------------------------------------------------------
def review(
    ctx: RunContext,
    scored: pd.DataFrame,
    queue: pd.DataFrame,
) -> pd.DataFrame:

    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"

    if not reviewed_path.exists():
        ctx.stats["n_reviewed"] = 0
        ctx.stats["n_escalated"] = 0
        ctx.stats["n_unreviewed"] = len(scored)
        return scored

    reviewed = load_review_decisions(reviewed_path)

    reviewed["item_id"] = reviewed["item_id"].astype(str)

    ctx.stats["n_reviewed"] = int(
        reviewed["item_id"].nunique()
    )

    ctx.stats["n_escalated"] = int(
        reviewed["reviewer_decision"].eq("escalate").sum()
    )

    ctx.stats["n_unreviewed"] = max(
        0,
        len(scored) - reviewed["item_id"].nunique(),
    )

    # FIX: đổi sang object trước khi áp dụng override.
    # File CSV đọc override_value dưới dạng chuỗi "0"/"1".
    # Sau khi áp dụng, chuyển lại thành int.
    scored = scored.copy()
    scored["pred_unusable"] = scored["pred_unusable"].astype(object)

    final = apply_overrides(
        scored,
        reviewed,
        id_col="image_id",
        value_col="pred_unusable",
        audit=ctx.audit,
    )

    final["pred_unusable"] = (
        pd.to_numeric(final["pred_unusable"], errors="raise")
        .astype(int)
    )

    return final


# ------------------------------------------------------------
# 05 DECIDE
# ------------------------------------------------------------
def decide(ctx: RunContext, final: pd.DataFrame) -> Decision:
    owner = str(ctx.config.get("owner", "")).strip()

    escalated = int(ctx.stats.get("n_escalated", 0))
    unreviewed = int(ctx.stats.get("n_unreviewed", len(final)))
    provenance = float(
        ctx.stats.get("transform_provenance_complete", 0.0)
    )

    problems = []

    if escalated:
        problems.append(f"{escalated} trường hợp đang escalate")

    if unreviewed:
        problems.append(f"{unreviewed} trường hợp chưa review")

    if provenance < 1.0:
        problems.append(
            "chưa xác minh đầy đủ transform, severity và seed"
        )

    if not owner or "TODO" in owner.upper():
        problems.append("owner chưa được xác nhận")

    if problems:
        status = "HOLD"
        reason_code = "UNRESOLVED_REVIEW_OR_PROVENANCE"
        reason = "; ".join(problems)
    else:
        status = "GO"
        reason_code = "REVIEW_AND_PROVENANCE_COMPLETE"
        reason = "Review và provenance đã hoàn tất."

    return Decision(
        status=status,
        reason_code=reason_code,
        reason=reason,
        owner=owner or "UNASSIGNED",
        scope="t03",
        evidence=[
            "out/qc.csv",
            "out/review_queue_reviewed.csv",
            "out/transform_manifest.csv",
            "out/audit_log.jsonl",
        ],
        metrics={
            "n_items": len(final),
            "n_reviewed": ctx.stats.get("n_reviewed", 0),
            "n_escalated": escalated,
            "n_unreviewed": unreviewed,
            "transform_provenance_complete": provenance,
        },
    )


# ------------------------------------------------------------
# RUN
# ------------------------------------------------------------
def run(ctx: RunContext) -> None:
    # This implementation is only for T03.
    if ctx.track != "t03":
        raise NotImplementedError(
            f"Pipeline is implemented only for T03, not {ctx.track}"
        )

    spec = get_track(ctx.track)

    data = load(ctx)
    scored = process(ctx, data)

    ctx.stats["n_items"] = len(scored)

    queue = rank(ctx, scored)
    final = review(ctx, scored, queue)

    columns = (
        spec.pred_columns
        + [c for c in final.columns if c not in spec.pred_columns]
    )

    final[columns].to_csv(
        ctx.out(spec.pred_file),
        index=False,
    )

    write_decision(
        decide(ctx, final),
        ctx.out("decision.json"),
        ctx.out("decision_log.csv"),
    )
