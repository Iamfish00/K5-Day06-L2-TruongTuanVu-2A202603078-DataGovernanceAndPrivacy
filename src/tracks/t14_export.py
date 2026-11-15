"""Review simulated detector boxes and export project-taxonomy annotations."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .box_review import BOX, overlay, parse_edit, valid_box
from .review_stage import finalize, make_queue
from ..common.io import sha256_file, write_json
from ..metrics.detection import iou_xywh
from ..pipeline import verify_inputs

ALIASES = {"person": "pedestrian", "motorcycle": "motorbike", "stop sign": "sign"}


def export_labels(ctx, images, boxes, classes):
    coco = {"images": [],"categories": [{"id": idx+1,"name": name} for idx,name in classes.items()],
            "annotations": []}
    cat_ids = {name: idx for idx,name in classes.items()}
    by_image = {key: group for key,group in boxes.groupby("image_id")}
    label_names = [Path(str(path)).stem for path in images.path]
    if len(set(label_names)) != len(label_names):
        raise ValueError("Image filename stems collide in YOLO export")
    for i, (key, image) in enumerate(images.iterrows(), start=1):
        W, H = int(image.width), int(image.height)
        if min(W, H) <= 0:
            raise ValueError(f"Invalid image size for {key}")
        coco["images"].append(dict(id=i, file_name=image.path, width=W, height=H))
        labels = []
        for box in by_image.get(key, boxes.iloc[:0]).to_dict("records"):
            if not valid_box(box, W, H):
                raise ValueError(f"T14 box would create invalid export for {key}")
            x,y,w,h = (float(box[k]) for k in BOX)
            cat = cat_ids[box["class"]]
            coco["annotations"].append(dict(id=len(coco["annotations"])+1,image_id=i,category_id=cat+1,
                                             bbox=[x,y,w,h],area=w*h,iscrowd=0))
            labels.append(f"{cat} {(x+w/2)/W:.8f} {(y+h/2)/H:.8f} {w/W:.8f} {h/H:.8f}")
        ctx.out(f"labels/{Path(image.path).stem}.txt").write_text("\n".join(labels)+("\n" if labels else ""),encoding="utf-8")
    ctx.out("classes.txt").write_text("\n".join(classes.values())+"\n",encoding="utf-8")
    write_json(ctx.out("annotations.json"), coco)


def run(ctx, *, baseline=False):
    verify_inputs(ctx)
    root = ctx.input_dir
    images = pd.read_csv(root/"images.csv").set_index("image_id").sort_index()
    classes = dict(sorted(yaml.safe_load((root/"taxonomy.yaml").read_text(encoding="utf-8"))["classes"].items()))
    if list(classes) != list(range(len(classes))) or len(set(classes.values())) != len(classes):
        raise ValueError("Project classes must have unique names and contiguous integer IDs")
    card = yaml.safe_load((root/"model_card_fixture.yaml").read_text(encoding="utf-8"))
    source_map = card["label_map"]
    raw = pd.read_csv(root/"model_predictions_fixture.csv")
    if not images.index.is_unique or not raw.image_id.isin(images.index).all() or not raw.conf.between(0,1).all():
        raise ValueError("Invalid T14 image IDs or confidence outside [0, 1]")
    conf = float(ctx.param("conf_threshold",.25))
    if not 0 <= conf <= 1:
        raise ValueError("T14 confidence threshold must be in [0, 1]")
    records, invalid = [], []
    for i, box in enumerate(raw.to_dict("records")):
        key = f"box:{i:05d}"
        model_name = source_map.get(box["class_id"])
        label = ALIASES.get(model_name, model_name)
        meta = images.loc[box["image_id"]]
        x,y,w,h = (float(box[k]) for k in BOX)
        if label not in classes.values() or not np.isfinite([x,y,w,h]).all():
            invalid.append((key,box["image_id"],"invalid_class_or_geometry"))
            continue
        x0,y0,x1,y1 = max(0,x),max(0,y),min(meta.width,x+w),min(meta.height,y+h)
        if x1 <= x0 or y1 <= y0:
            invalid.append((key,box["image_id"],"empty_clipped_box"))
            continue
        records.append({"item_id":key,"image_id":box["image_id"],"class":label,"x":x0,"y":y0,
                        "w":x1-x0,"h":y1-y0,"conf":box["conf"]})
    cols = ["item_id","image_id","class",*BOX,"conf"]
    candidates = pd.DataFrame(records, columns=cols).set_index("item_id",drop=False)
    final = candidates[candidates.conf.ge(conf)].copy()
    final.to_csv(ctx.out("predictions_before_review.csv"),index=False)
    review, events, diffs = [], [], []
    if not baseline:
        queue_data, overlays = [], {}
        evaluation_split = (ctx.config.get("eval_params") or {}).get("split","eval")
        pool = set(images.index if evaluation_split == "all" else images.index[images.split.eq(evaluation_split)])
        review_pool = candidates[candidates.image_id.isin(pool)]
        counts = final.groupby("image_id").size()
        for image_id, boxes in review_pool.groupby("image_id"):
            b = boxes.to_dict("records")
            for row in b:
                key = row["item_id"]
                small = row["w"]*row["h"] < 150
                mismatch = any(other["class"] != row["class"] and other["item_id"] != key
                               and iou_xywh([row[k] for k in BOX],[other[k] for k in BOX]) >= .45 for other in b)
                score = 1-abs(row["conf"]-conf) + .25*small + .35*mismatch
                reason = "class_overlap" if mismatch else "small_box" if small else "near_conf_threshold"
                queue_data.append(dict(item_id=key,image_id=image_id,score=score,reason=reason,
                                       path=images.at[image_id,"path"],
                                       suggested_action="KEEP" if key in final.index else "DROP"))
                overlays[key] = [overlay(row)]
        for image_id in sorted(pool - set(counts.index)):
            key = f"img:{image_id}"
            queue_data.append(dict(item_id=key,image_id=image_id,score=1.1,reason="no_exported_box",
                                   path=images.at[image_id,"path"],suggested_action="CHECK_IMAGE"))
        items = pd.DataFrame(queue_data,columns=["item_id","image_id","score","reason","path","suggested_action"])
        _, review = make_queue(ctx, items, int(ctx.param("budget",30)), "T14 - Annotation export review",
                                   image_col="path",boxes=overlays,fields=("image_id",))
        items = items.set_index("item_id")
        for row in review:
            key = row.item_id
            image_id = items.at[key,"image_id"]
            before = final.loc[key].to_dict() if key in final.index else None
            after = before
            if row.reviewer_decision == "edit":
                after = {"item_id":key,"image_id":image_id,**parse_edit(row,images.loc[image_id],classes.values()),
                         "conf":1.0}
                final.loc[key] = after
            elif row.reviewer_decision == "reject" and key in final.index:
                final.drop(key,inplace=True)
                after = None
            if before != after:
                diffs.append({"item_id":key,"before":json.dumps(before),"after":json.dumps(after),"reviewer":row.reviewer})
            events.append((row,before,after))
    final[["image_id","class",*BOX,"conf"]].to_csv(ctx.out("predictions.csv"),index=False)
    pd.DataFrame(invalid,columns=["item_id","image_id","reason"]).to_csv(ctx.out("invalid_input_boxes.csv"),index=False)
    pd.DataFrame(diffs,columns=["item_id","before","after","reviewer"]).to_csv(ctx.out("label_changes.csv"),index=False)
    export_labels(ctx, images, final, classes)
    write_json(ctx.out("export_provenance.json"), {
        "model_name":card["model_name"],"version":card["version"],"license":card["license"],
        "source":"simulated detector fixture", "confidence_threshold":conf,
        "input_predictions_sha256":sha256_file(root/"model_predictions_fixture.csv"),
        "taxonomy_sha256":sha256_file(root/"taxonomy.yaml"),
    })
    ctx.stats.update(n_images=len(images),n_boxes=len(final),n_inputs_rejected=len(invalid),n_changes=len(diffs))
    finalize(ctx,len(raw),review,events,["predictions.csv","annotations.json","labels/","label_changes.csv"],baseline=baseline)
