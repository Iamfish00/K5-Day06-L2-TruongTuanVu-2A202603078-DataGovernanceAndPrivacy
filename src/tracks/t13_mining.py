"""Choose a diverse queue of rare-scene candidates from offline features."""

from itertools import combinations

import numpy as np
import pandas as pd
from scipy.spatial.distance import cdist

from .review_stage import finalize, make_queue
from ..common.image_ops import hamming, load_rgb, phash
from ..pipeline import verify_inputs


def run(ctx, *, baseline=False):
    verify_inputs(ctx)
    images = pd.read_csv(ctx.input_dir / "images.csv")
    features = pd.read_csv(ctx.input_dir / "pool_features.csv")
    table = images.merge(features, on="image_id", validate="one_to_one").sort_values("image_id").reset_index(drop=True)
    if len(table) != len(images) or len(table) < 3:
        raise ValueError("T13 requires one feature row per image")
    k = int(ctx.param("k", 20))
    if not 1 <= k <= len(table):
        raise ValueError("T13 K exceeds available images")
    embedding = table.filter(regex=r"^emb_\d+$").to_numpy(float)
    if embedding.shape[1] == 0 or not np.isfinite(embedding).all():
        raise ValueError("T13 requires finite embedding columns")
    embedding = (embedding - np.median(embedding, axis=0)) / np.maximum(embedding.std(axis=0), 1e-6)
    distances = cdist(embedding, embedding, metric="euclidean")
    local = np.sort(distances, axis=1)[:, 1:min(6,len(table))].mean(axis=1)
    novelty = np.minimum(local / max(np.quantile(local, .9), 1e-6), 1.2)
    # Heavy high-frequency noise or nearly empty frames are weak labeling candidates.
    quality = table.edge_density.between(.004,.10) & table.brightness_std.ge(12)
    hashes = [phash(load_rgb(ctx.input_dir / path)) for path in table.path]
    near_pairs = {(a,b) for a,b in combinations(range(len(table)), 2) if hamming(hashes[a],hashes[b]) <= 4}
    selected, blocked, scores = [], set(), []
    if baseline:
        selected = ctx.rng.choice(len(table), size=k, replace=False).tolist()
        scores = [float(k-i) for i in range(k)]
    else:
        distance_scale = max(np.quantile(distances, .9), 1e-6)
        nearest_selected = np.ones(len(table))
        for _ in range(k):
            priority = np.where(quality, 1.0, -.6) + .60*novelty + .40*np.minimum(nearest_selected,1)
            priority[selected] = -np.inf
            candidates = [i for i in range(len(table)) if i not in blocked and i not in selected]
            i = max(candidates, key=lambda j: (priority[j], -j)) if candidates else int(np.argmax(priority))
            selected.append(i)
            scores.append(float(priority[i]))
            nearest_selected = np.minimum(nearest_selected, distances[i] / distance_scale)
            blocked.update(b if a == i else a for a,b in near_pairs if i in (a,b))

    queue_items = table.iloc[selected][["image_id","path","brightness_mean","edge_density"]].copy()
    queue_items["item_id"] = queue_items.image_id
    queue_items["score"] = scores
    queue_items["reason"] = "embedding_novelty_and_diversity" if not baseline else "seeded_random"
    queue_items["suggested_action"] = "LABEL"
    reviewed, events = [], []
    queue_items["review_action"] = "PENDING"
    if not baseline:
        _, reviewed = make_queue(ctx, queue_items, k, "T13 - Rare-case labeling queue",
                                 image_col="path", fields=("brightness_mean","edge_density"))
        for row in reviewed:
            action = {"approve":"LABEL","reject":"SKIP","skip":"SKIP","escalate":"HOLD"}.get(row.reviewer_decision)
            if row.reviewer_decision == "edit":
                action = row.override_value.strip().upper()
                if action not in {"LABEL","SKIP","HOLD"}:
                    raise ValueError("T13 edits use LABEL, SKIP or HOLD")
            queue_items.loc[queue_items.image_id.eq(row.item_id),"review_action"] = action
            events.append((row,"PENDING",action))
    queue_items[["image_id"]].assign(rank=range(1,k+1)).to_csv(ctx.out("selected.csv"), index=False)
    queue_items.drop(columns="item_id").to_csv(ctx.out("label_queue.csv"), index=False)
    selected_set = set(selected)
    ctx.stats.update(n_selected=k, phash_pairs=sum(a in selected_set and b in selected_set for a,b in near_pairs),
                     quality_flags=int((~quality.iloc[selected]).sum()))
    finalize(ctx, len(table), reviewed, events, ["selected.csv","label_queue.csv"], baseline=baseline)
