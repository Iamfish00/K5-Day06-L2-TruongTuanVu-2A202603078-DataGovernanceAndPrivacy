"""Prioritize possible annotation issues and apply explicit reviewer edits."""

import json

import pandas as pd

from .box_review import BOX, overlay, parse_edit, valid_box
from .review_stage import finalize, make_queue
from ..metrics.detection import iou_xywh
from ..metrics.ranking import budget_b
from ..pipeline import verify_inputs


def run(ctx, *, baseline=False):
    verify_inputs(ctx)
    images = pd.read_csv(ctx.input_dir / "images.csv").set_index("image_id")
    annotations = pd.read_csv(ctx.input_dir / "annotations.csv")
    detector = pd.read_csv(ctx.input_dir / "model_predictions.csv")
    if not images.index.is_unique or not annotations.ann_id.is_unique or not annotations.image_id.isin(images.index).all():
        raise ValueError("Invalid image or annotation IDs")
    pred_by_image = {key: group for key, group in detector.groupby("image_id")}
    ann_by_image = {key: group for key, group in annotations.groupby("image_id")}
    classes = set(annotations["class"]) | set(detector["class"])
    rows, overlays = [], {}
    for image_id, image in images.iterrows():
        anns = ann_by_image.get(image_id, annotations.iloc[:0]).sort_values("ann_id")
        preds = pred_by_image.get(image_id, detector.iloc[:0])
        records = list(anns.to_dict("records"))
        proposals = [x for x in preds.to_dict("records") if x["conf"] >= .50]
        for i, box in enumerate(records):
            reasons = [(0.0, "no_strong_disagreement")]
            if not valid_box(box, image.width, image.height):
                reasons.append((1.0, "box_out_of_bounds"))
            if any(old["class"] == box["class"] and iou_xywh([old[k] for k in BOX], [box[k] for k in BOX]) >= .8
                   for old in records[:i]):
                reasons.append((.95, "possible_duplicate"))
            for proposal in proposals:
                overlap = iou_xywh([box[k] for k in BOX], [proposal[k] for k in BOX])
                if overlap >= .5 and proposal["class"] != box["class"]:
                    reasons.append((overlap * proposal["conf"], "possible_class_swap"))
                elif .15 <= overlap < .55 and proposal["class"] == box["class"]:
                    reasons.append(((1-overlap) * proposal["conf"], "possible_box_shift"))
            score, reason = max(reasons)
            if baseline:
                score, reason = ctx.rng.random(), "seeded_random"
            key = box["ann_id"]
            rows.append(dict(item_id=key,image_id=image_id,score=score,reason=reason,path=image.path,
                             split=image.split,suggested_action="CHECK_LABEL"))
            overlays[key] = [overlay(box)] + [overlay(proposal, "#30c4fc") for proposal in proposals]
        missing = [x for x in proposals if not any(
            iou_xywh([x[k] for k in BOX], [ann[k] for k in BOX]) >= .25 for ann in records)]
        score = max((x["conf"] for x in missing), default=0)
        key = f"img:{image_id}"
        rows.append(dict(item_id=key,image_id=image_id,score=ctx.rng.random() if baseline else score,
                         reason="seeded_random" if baseline else ("possible_missing" if missing else "no_unmatched_proposal"),
                         path=image.path,split=image.split,suggested_action="CHECK_MISSING"))
        overlays[key] = [overlay(box) for box in records] + [overlay(proposal, "#30c4fc") for proposal in missing]

    ranking = pd.DataFrame(rows).sort_values(["score","item_id"], ascending=[False, True])
    selected_split = (ctx.config.get("eval_params") or {}).get("split", "heldout")
    eligible = ranking if selected_split == "all" else ranking[ranking.split.eq(selected_split)]
    if eligible.empty:
        raise ValueError("T11 review split has no images")
    n_annotations = int((~eligible.item_id.str.startswith("img:")).sum())
    limit = int(ctx.param("budget", budget_b(n_annotations)))
    _, reviewed = make_queue(ctx, eligible, limit, "T11 - Label issue review",
                             image_col="path", boxes=overlays, fields=("image_id","split")) if not baseline else (None, [])
    corrected = annotations.set_index("ann_id", drop=False)
    changes, events = [], []
    lookup = ranking.set_index("item_id")
    for row in reviewed:
        key = row.item_id
        image_id = lookup.at[key,"image_id"]
        is_image = key.startswith("img:")
        before = None if is_image else corrected.loc[key].to_dict()
        after = before
        if row.reviewer_decision == "edit":
            new_key = f"review:{image_id}" if is_image else key
            if is_image and new_key in corrected.index:
                raise ValueError(f"Duplicate review annotation {new_key}")
            after = {"ann_id":new_key, "image_id":image_id, **parse_edit(row, images.loc[image_id], classes)}
            corrected.loc[new_key] = after
        elif row.reviewer_decision == "reject" and not is_image:
            corrected = corrected.drop(key)
            after = None
        if before != after:
            changes.append({"item_id":key,"before":json.dumps(before),"after":json.dumps(after),"reviewer":row.reviewer})
        events.append((row,before,after))
    ranking[["item_id","score","reason"]].to_csv(ctx.out("ranking.csv"), index=False)
    corrected.to_csv(ctx.out("corrected_annotations.csv"), index=False)
    pd.DataFrame(changes, columns=["item_id","before","after","reviewer"]).to_csv(ctx.out("label_changes.csv"), index=False)
    ctx.stats.update(n_images=len(images), n_changes=len(changes), review_budget=min(len(eligible),limit))
    finalize(ctx, len(ranking), reviewed, events, ["ranking.csv","corrected_annotations.csv","label_changes.csv"],
             baseline=baseline)
