"""Day06 PoCs for T01 Coverage Planner and T02 Raw QC Triage."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .common.contact_sheet import render_contact_sheet
from .common.context import RunContext
from .common.decision import Decision, write_decision
from .common.image_ops import brightness_stats, laplacian_variance, load_rgb, to_gray
from .common.manifest import verify_manifest
from .common.review import load_review_decisions, make_review_queue, write_review_queue


DIMENSIONS = ("route", "weather", "light", "object")


def verify_inputs(ctx: RunContext) -> None:
    manifest = ctx.input_dir / "manifest.csv"
    problems = verify_manifest(manifest) if manifest.exists() else [{"problem": "no_manifest"}]
    if problems:
        ctx.audit.log(actor="pipeline", action="provenance_problems", item_id=str(manifest), after=problems[:20])
        if ctx.config.get("strict_provenance", True):
            raise ValueError(f"provenance check failed ({len(problems)} problems), first: {problems[:3]}")

def load(ctx: RunContext) -> tuple[pd.DataFrame, pd.DataFrame]:
    verify_inputs(ctx)
    pool = ctx.param("candidate_pool", "candidates.csv")
    if pool not in {"candidates.csv", "holdout_candidates.csv"}:
        raise ValueError(f"Unknown T01 candidate pool: {pool}")
    clips = pd.read_csv(ctx.input_dir / pool, dtype={"clip_id": str})
    targets = pd.read_csv(ctx.input_dir / "targets.csv", dtype={"cell_id": str})
    for table, key, columns in (
        (clips, "clip_id", ("cost_min", *DIMENSIONS)),
        (targets, "cell_id", ("weight", "is_hard", *DIMENSIONS)),
    ):
        missing = set((key, *columns)) - set(table.columns)
        if missing:
            raise ValueError(f"{key} table missing columns: {sorted(missing)}")
        if table.empty or table[key].isna().any() or not table[key].is_unique:
            raise ValueError(f"{key} table must have nonempty, unique IDs")
    if (targets.weight <= 0).any() or targets.weight.isna().any() or clips.cost_min.isna().any():
        raise ValueError("Targets need positive weights and clips need valid costs")
    return clips, targets


def process(ctx: RunContext, clips: pd.DataFrame, targets: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Select by new target weight, then prefer distinct, lower-cost scenarios."""
    k = int(ctx.param("k", 30))
    if not 1 <= k <= len(clips):
        raise ValueError(f"k must be between 1 and {len(clips)}, got {k}")

    target_cells = list(targets.itertuples(index=False))
    weights = dict(zip(targets.cell_id, targets.weight))
    scenarios = {}
    matches = {}
    costs = dict(zip(clips.clip_id, clips.cost_min))
    for clip in clips.itertuples(index=False):
        scenario = tuple(getattr(clip, dim) for dim in DIMENSIONS)
        scenarios[clip.clip_id] = scenario
        matches[clip.clip_id] = {
            cell.cell_id for cell in target_cells
            if all(getattr(cell, dim) == "*" or getattr(cell, dim) == tag
                   for dim, tag in zip(DIMENSIONS, scenario))
        }

    method = ctx.param("selection_method", "poc")
    if method not in {"poc", "greedy"}:
        raise ValueError(f"Unknown T01 selection method: {method}")
    remaining = set(scenarios)
    covered = set()
    chosen_scenarios = set()
    picks = []
    for _ in range(k):
        clip_id = min(remaining, key=lambda cid: (
            -sum(weights[cell] for cell in matches[cid] - covered),
            scenarios[cid] in chosen_scenarios if method == "poc" else False,
            costs[cid] if method == "poc" else 0,
            cid,
        ))
        remaining.remove(clip_id)
        covered.update(matches[clip_id])
        chosen_scenarios.add(scenarios[clip_id])
        picks.append(clip_id)

    coverage = targets.assign(
        covered=targets.cell_id.isin(covered),
        pool_matches=targets.cell_id.map({
            cell: sum(cell in cells for cells in matches.values()) for cell in weights
        }),
        picked_clips=targets.cell_id.map({
            cell: ", ".join(cid for cid in picks if cell in matches[cid]) for cell in weights
        }),
    )
    return pd.DataFrame({"clip_id": picks, "rank": range(1, k + 1)}), coverage


def rank(ctx: RunContext, coverage: pd.DataFrame) -> pd.DataFrame:
    priority = coverage.sort_values(
        ["covered", "is_hard", "weight", "cell_id"], ascending=[True, False, False, True]
    )
    priority = priority.assign(
        score=range(len(priority), 0, -1),
        reason=priority.pool_matches.map(
            lambda count: "NO_METADATA_MATCH_IN_POOL" if count == 0 else "VERIFY_COVERAGE"
        ),
        suggested_action=priority.covered.map({True: "VERIFY", False: "REVIEW_CAPTURE_GAP"}),
        evidence="coverage.csv",
        scenario=priority[list(DIMENSIONS)].astype(str).agg(" / ".join, axis=1),
    )
    queue = make_review_queue(
        priority, "cell_id", "score", budget=3, reason_col="reason",
        action_col="suggested_action", slice_col="scenario", evidence_col="evidence",
    )
    write_review_queue(queue, ctx.out("review_queue.csv"))
    cards = queue.assign(
        pool_matches=queue.item_id.map(priority.set_index("cell_id").pool_matches),
        picked_clips=queue.item_id.map(priority.set_index("cell_id").picked_clips),
    )
    render_contact_sheet(
        cards, ctx.out("contact_sheet.html"), image_col=None,
        fields=("item_id", "slice", "reason", "suggested_action", "pool_matches", "picked_clips"),
        title="T01 - Coverage review",
    )
    return queue


def review(ctx: RunContext, coverage: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"
    if not reviewed_path.exists():
        return coverage.assign(reviewer="", reviewer_decision="", override_value="", review_note="")

    reviewed = load_review_decisions(reviewed_path)
    if not reviewed.item_id.is_unique or not reviewed.item_id.isin(queue.item_id).all():
        raise ValueError("Reviewed rows must refer to unique cells in the current review queue")

    edits = reviewed[reviewed.reviewer_decision.eq("edit")]
    if edits.override_value.str.strip().eq("").any():
        raise ValueError("Each edit needs a capture override")

    for row in reviewed.itertuples(index=False):
        ctx.audit.log(
            actor=row.reviewer, action=f"review:{row.reviewer_decision}", item_id=row.item_id,
            after={"capture_override": row.override_value} if row.reviewer_decision == "edit" else None,
            reason=row.review_note,
        )
    return coverage.merge(
        reviewed[["item_id", "reviewer", "reviewer_decision", "override_value", "review_note"]],
        left_on="cell_id", right_on="item_id", how="left", validate="one_to_one",
    ).drop(columns="item_id").fillna("")


def decide(ctx: RunContext, coverage: pd.DataFrame, queue: pd.DataFrame) -> Decision:
    missing = coverage.loc[~coverage.covered]
    reviewed = coverage.set_index("cell_id").loc[queue.item_id, "reviewer_decision"]
    if not reviewed.isin(["approve", "edit"]).all():
        status, reason_code = "HOLD", "REVIEW_PENDING"
        reason = "The priority coverage cells need reviewer approval or a documented capture edit."
    elif not missing.empty:
        status, reason_code = "REWORK", "COVERAGE_GAPS"
        reason = "The reviewed plan still has coverage gaps according to candidate metadata."
    else:
        status, reason_code = "GO", "REVIEWED_METADATA_COVERAGE"
        reason = "The priority cells were reviewed and the selected clips cover all target metadata cells."

    return Decision(
        status=status, reason_code=reason_code, reason=reason,
        owner=str(ctx.config.get("owner") or ""), scope=ctx.track,
        evidence=["pick.csv", "coverage.csv", "capture_plan.csv", "review_queue.csv"],
        metrics={
            "metadata_cells_covered": int(coverage.covered.sum()),
            "metadata_cells_total": len(coverage),
            "weighted_metadata_coverage": float(coverage.loc[coverage.covered, "weight"].sum() / coverage.weight.sum()),
        },
    )


def run_t01(ctx: RunContext) -> None:
    clips, targets = load(ctx)
    picks, coverage = process(ctx, clips, targets)
    queue = rank(ctx, coverage)
    coverage = review(ctx, coverage, queue)
    decision = decide(ctx, coverage, queue)
    ctx.stats.update(n_items=len(clips), n_picked=len(picks), n_reviewed=int(coverage.reviewer_decision.ne("").sum()))

    picks.to_csv(ctx.out("pick.csv"), index=False)
    coverage.to_csv(ctx.out("coverage.csv"), index=False)
    gaps = coverage.loc[~coverage.covered].sort_values(
        ["is_hard", "weight", "cell_id"], ascending=[False, False, True]
    ).copy()
    gaps["capture_action"] = gaps.pool_matches.map(
        lambda count: "CAPTURE_NEW_SCENE" if count == 0 else "REVIEW_UNSELECTED_CLIP"
    )
    edited = gaps.reviewer_decision.eq("edit")
    gaps.loc[edited, "capture_action"] = gaps.loc[edited, "override_value"]
    gaps.to_csv(ctx.out("capture_plan.csv"), index=False)
    write_decision(decision, ctx.out("decision.json"), ctx.out("decision_log.csv"))


def qc_features(img: np.ndarray) -> dict:
    gray = to_gray(img)
    h, w = gray.shape
    tile = 16
    grid = gray[: h // tile * tile, : w // tile * tile]
    local_saturation = 0.0
    if grid.size:
        local_saturation = float((grid.reshape(h // tile, tile, w // tile, tile) >= 245).mean(axis=(1, 3)).max())

    boundary_energy, interior_energy = [], []
    for axis in (0, 1):
        diffs = np.abs(np.diff(gray, axis=axis))
        offsets = np.arange(1, gray.shape[axis])
        boundaries = np.flatnonzero(offsets % 8 == 0)
        interior = np.flatnonzero(offsets % 8 != 0)
        if boundaries.size and interior.size:
            boundary_energy.append(float(diffs.take(boundaries, axis=axis).mean()))
            interior_energy.append(float(diffs.take(interior, axis=axis).mean()))
    blockiness = (sum(boundary_energy) + 1.0) / (sum(interior_energy) + 1.0)

    return {
        "sharpness": laplacian_variance(img),
        "brightness": brightness_stats(img)["mean"],
        "local_saturation": local_saturation,
        "blockiness": blockiness,
    }


def load_qc_images(ctx: RunContext) -> pd.DataFrame:
    verify_inputs(ctx)
    images = pd.read_csv(ctx.input_dir / "images.csv", dtype=str).fillna("")
    required = {"image_id", "path", "camera_id", "light", "split"}
    missing = required - set(images.columns)
    if missing or images.empty or not images.image_id.is_unique or images.image_id.eq("").any():
        raise ValueError(f"Invalid T02 images.csv, missing={sorted(missing)} or invalid IDs")

    root = ctx.input_dir.resolve()
    rows = []
    for image in images.to_dict("records"):
        path = (root / image["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Image path leaves the input directory: {image['image_id']}")
        try:
            rows.append({**image, **qc_features(load_rgb(path))})
        except OSError as error:
            raise ValueError(f"Unreadable image: {image['image_id']} ({image['path']})") from error
    return pd.DataFrame(rows)


def score_qc(ctx: RunContext, images: pd.DataFrame, method: str) -> pd.DataFrame:
    if method not in {"poc", "laplacian"}:
        raise ValueError(f"Unknown T02 score method: {method}")
    dev = images[images.split.eq(ctx.param("dev_split", "dev"))]
    if dev.empty:
        raise ValueError("T02 needs development images to calculate reference statistics")

    if method == "laplacian":
        threshold = dev.sharpness.quantile(0.25)
        score = (threshold + 1) / (images.sharpness + 1)
        defects = pd.Series("blur", index=images.index)
    else:
        ref_sharp = images.camera_id.map(dev.groupby("camera_id").sharpness.median()).fillna(dev.sharpness.median())
        ref_light = images.light.map(dev.groupby("light").brightness.median()).fillna(dev.brightness.median())
        ref_glare = images.light.map(dev.groupby("light").local_saturation.median()).fillna(dev.local_saturation.median())
        risks = pd.DataFrame({
            "blur": (ref_sharp + 1) / (images.sharpness + 1) / 2,
            "dark": (ref_light + 1) / (images.brightness + 1) / 1.6,
            "glare": (images.local_saturation - ref_glare).clip(lower=0) / 0.45,
            "compression": images.blockiness / max(1.35, dev.blockiness.quantile(0.9)),
        })
        score = risks.max(axis=1)
        defects = risks.idxmax(axis=1)

    return images.assign(
        score=score, pred_defect_type=defects.where(score.ge(1), "none"),
        pred_action=np.where(score.ge(1), "RECAPTURE", "KEEP"),
    ).sort_values(["score", "image_id"], ascending=[False, True])


def review_qc(ctx: RunContext, predictions: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    path = ctx.output_dir / "review_queue_reviewed.csv"
    final = predictions.assign(review_state="", final_action="").set_index("image_id", drop=False)
    if not path.exists():
        return final

    reviewed = load_review_decisions(path)
    if not reviewed.item_id.is_unique or not reviewed.item_id.isin(queue.item_id).all():
        raise ValueError("Reviewed rows must refer to unique images in the current review queue")
    changes = []
    for row in reviewed.itertuples(index=False):
        original = final.at[row.item_id, "pred_action"]
        action = {"approve": original, "reject": "RECAPTURE"}.get(row.reviewer_decision, "")
        if row.reviewer_decision == "edit":
            action = row.override_value.strip().upper()
            if action not in {"KEEP", "RECAPTURE"}:
                raise ValueError("T02 edits must set override_value to KEEP or RECAPTURE")
        changes.append((row, original, action))

    for row, original, action in changes:
        final.loc[row.item_id, ["review_state", "final_action"]] = [row.reviewer_decision, action]
        ctx.audit.log(actor=row.reviewer, action=f"review:{row.reviewer_decision}", item_id=row.item_id,
                      before=original, after=action or None, reason=row.review_note)
    return final


def run_t02(ctx: RunContext, method: str = "poc") -> None:
    predictions = score_qc(ctx, load_qc_images(ctx), method)
    budget = int(ctx.param("budget", 20))
    split = (ctx.config.get("eval_params") or {}).get("split", "eval")
    eligible = predictions if split == "all" else predictions[predictions.split.eq(split)]
    if budget < 1 or eligible.empty:
        raise ValueError("T02 needs a positive review budget and images in the review split")

    queue = make_review_queue(
        eligible, "image_id", "score", budget, reason_col="pred_defect_type",
        action_col="pred_action", slice_col="light", evidence_col="path",
    )
    final = review_qc(ctx, predictions, queue)
    actions = final.loc[queue.item_id, "final_action"]
    reviewed_count = int(actions.ne("").sum())
    recapture_count = int(actions.eq("RECAPTURE").sum())
    if reviewed_count < len(queue):
        status, code = "HOLD", "REVIEW_PENDING"
        reason = "The priority images need a completed human review."
    elif recapture_count:
        status, code = "RECAPTURE", "CONFIRMED_IMAGE_DEFECTS"
        reason = "The reviewer flagged priority images for recapture."
    else:
        status, code = "KEEP", "REVIEWED_PRIORITY_IMAGES"
        reason = "The reviewer accepted the priority images in the review budget."

    predictions[["image_id", "score", "pred_defect_type", "pred_action", "sharpness", "brightness",
                 "local_saturation", "blockiness", "camera_id", "light", "split"]].to_csv(
        ctx.out("ranked_review.csv"), index=False
    )
    write_review_queue(queue, ctx.out("review_queue.csv"))
    cards = queue.assign(camera_id=queue.item_id.map(final.camera_id))
    render_contact_sheet(
        cards, ctx.out("contact_sheet.html"), image_root=ctx.input_dir,
        fields=("rank", "item_id", "score", "reason", "suggested_action", "slice", "camera_id"),
        title="T02 - Raw image quality review",
    )
    if reviewed_count:
        final.loc[queue.item_id, ["image_id", "review_state", "final_action"]].to_csv(
            ctx.out("review_actions.csv"), index=False
        )
    ctx.stats.update(n_items=len(predictions), review_pool=len(eligible), n_reviewed=reviewed_count)
    write_decision(
        Decision(
            status=status, reason_code=code, reason=reason, scope=f"t02:{split}",
            owner=str(ctx.config.get("owner") or ""),
            evidence=["ranked_review.csv", "review_queue.csv"] + (
                ["review_queue_reviewed.csv", "review_actions.csv"] if reviewed_count else []
            ),
            metrics={"review_budget": len(queue), "reviewed": reviewed_count, "recapture": recapture_count},
        ),
        ctx.out("decision.json"), ctx.out("decision_log.csv"),
    )


def run(ctx: RunContext) -> None:
    if ctx.track == "t01":
        return run_t01(ctx)
    if ctx.track == "t02":
        return run_t02(ctx)

    from . import pipeline_t03, pipeline_t12, t09
    from .tracks import REGISTRY

    pipeline = {**REGISTRY, "t03": pipeline_t03, "t09": t09, "t12": pipeline_t12}.get(ctx.track)
    if pipeline is None:
        raise NotImplementedError(f"No implemented pipeline for {ctx.track}")
    pipeline.run(ctx)
