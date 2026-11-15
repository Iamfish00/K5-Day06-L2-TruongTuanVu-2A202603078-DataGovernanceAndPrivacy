"""Exact matches, perceptual candidates and group-aware splitting."""

from itertools import combinations

import numpy as np
import pandas as pd

from .review_stage import finalize, make_queue
from ..common.image_ops import color_histogram, dhash, hamming, load_rgb, phash
from ..common.io import sha256_file
from ..pipeline import verify_inputs


def group_split(ids, edges, fraction, rng):
    parents = {key: key for key in ids}

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for a, b in edges:
        parents[root(a)] = root(b)
    groups = {}
    for image_id in ids:
        groups.setdefault(root(image_id), []).append(image_id)
    target = round(len(ids) * fraction)
    selected = set()
    if len(groups) > 1:
        group_list = list(groups.values())
        for i in rng.permutation(len(group_list)):
            group = group_list[i]
            if abs(target - len(selected) - len(group)) < abs(target - len(selected)):
                selected.update(group)
        if not selected:
            selected.update(min(group_list, key=lambda group: (len(group), group[0])))
    return pd.DataFrame({"image_id": ids, "split": ["test" if i in selected else "train" for i in ids],
                         "group_id": [root(i) for i in ids]})


def run(ctx, *, baseline=False):
    verify_inputs(ctx)
    images = pd.read_csv(ctx.input_dir / "images.csv").sort_values("image_id")
    if images.empty or not images.image_id.is_unique:
        raise ValueError("Need unique T05 images")
    fraction = float(ctx.param("test_fraction", 0.3))
    if not 0 < fraction < 1:
        raise ValueError("test_fraction must be between 0 and 1")
    timestamps = pd.to_datetime(images.captured_at, format="ISO8601")
    images = images.assign(timestamp=timestamps).set_index("image_id")
    features = {}
    for key, row in images.iterrows():
        path = ctx.input_dir / row.path
        image = None if baseline else load_rgb(path)
        features[key] = (sha256_file(path), None if baseline else (phash(image), dhash(image), color_histogram(image)))

    rows, burst_edges = [], []
    for a, b in combinations(images.index, 2):
        left, right = images.loc[a], images.loc[b]
        sha_a, fa = features[a]
        sha_b, fb = features[b]
        exact = sha_a == sha_b
        burst = not baseline and left.camera_id == right.camera_id and abs((left.timestamp-right.timestamp).total_seconds()) <= 1
        if burst:
            burst_edges.append((a, b))
        if exact:
            score, reason = 1.0, "exact_sha256"
        elif baseline:
            continue
        else:
            p, h = hamming(fa[0], fb[0]), hamming(fa[1], fb[1])
            color = float(np.abs(fa[2] - fb[2]).sum())
            if p > 16 or h > 19 or color > 1.0:
                continue
            score = (1 - (p + h) / 128) * (1 - color / 2)
            reason = "perceptual_burst" if burst else "perceptual"
        rows.append({"item_id": f"{a}|{b}", "img_a": a, "img_b": b, "score": score,
                     "reason": reason, "pred_dup": int(exact or score >= .72),
                     "left_path": left.path, "right_path": right.path})
    pairs = pd.DataFrame(rows, columns=["item_id","img_a","img_b","score","reason","pred_dup","left_path","right_path"])
    pairs["suggested_action"] = np.where(pairs.pred_dup.eq(1), "MATCH", "CHECK")
    pairs = pairs.set_index("item_id", drop=False)
    reviewed, events = [], []
    if not baseline:
        # Near-threshold matches are the most useful cases for a false-merge review.
        review_items = pairs.reset_index(drop=True).assign(
            similarity=pairs.score.to_numpy(),
            score=np.where(pairs.reason.eq("exact_sha256"), 0, 1 - abs(pairs.score - .72)),
        )
        _, reviewed = make_queue(ctx, review_items, int(ctx.param("budget", 20)), "T05 - Suspect image pairs",
                                 image_col="right_path", before_col="left_path", fields=("similarity",))
        for row in reviewed:
            before = int(pairs.at[row.item_id, "pred_dup"])
            action = row.reviewer_decision
            after = {"approve": 1, "reject": 0}.get(action, before)
            if action == "edit":
                if row.override_value not in {"0", "1"}:
                    raise ValueError("T05 edits use 0 or 1")
                after = int(row.override_value)
            pairs.at[row.item_id, "pred_dup"] = after
            events.append((row, before, after))
    matched = pairs[pairs.pred_dup.eq(1)]
    links = list(zip(matched.img_a, matched.img_b))
    # A rejection also removes a direct burst edge, while alternate graph paths still apply.
    rejected = {tuple(row.item_id.split("|")) for row in reviewed
                if row.reviewer_decision == "reject" or (row.reviewer_decision == "edit" and row.override_value == "0")}
    groups = group_split(list(images.index), [] if baseline else links + [edge for edge in burst_edges if edge not in rejected],
                         fraction, ctx.rng)
    pairs[["img_a", "img_b", "score", "pred_dup", "reason"]].to_csv(ctx.out("suspect_pairs.csv"), index=False)
    groups.to_csv(ctx.out("split_manifest.csv"), index=False)
    split = groups.set_index("image_id").split
    ctx.stats.update(n_matched=len(matched), n_burst_edges=len(burst_edges), split_counts=groups.split.value_counts().to_dict(),
                     predicted_cross_split=sum(split[a] != split[b] for a, b in links))
    finalize(ctx, len(images), reviewed, events, ["suspect_pairs.csv", "split_manifest.csv"], baseline=baseline)
