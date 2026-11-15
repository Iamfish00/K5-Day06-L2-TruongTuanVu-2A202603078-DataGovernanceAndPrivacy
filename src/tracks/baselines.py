"""Trivial baselines for T06/T07/T08 — the bar the PoC must beat (docs §4).

Run with e.g.
    python -m src.main --track t06 --pipeline src.tracks.baselines --output out/t06/baseline --run-name baseline --offline
    python -m src.main --track t07 --pipeline src.tracks.baselines --output out/t07/baseline --run-name baseline --offline
    python -m src.main --track t07 --pipeline src.tracks.baselines --output out/t07/blur_all --run-name blur_all --offline --params? (use cfg params: mode)
    python -m src.main --track t08 --pipeline src.tracks.baselines --output out/t08/baseline --run-name baseline --offline

* t06: JSON schema/parse + file existence only — every release PASSes.
* t07: default detector (coarse YCrCb skin + bright-pixel rule, fixed pad — the
       Haar cascade API no longer exists in OpenCV 5.0) + fixed blur, no queue/audit.
       params.mode='blur_all' gives the trivial redact-everything baseline the
       T07 doc asks groups to report (miss rate 0, guardrail FAIL).
* t08: delete ONE raw artefact per request by filename match; derived frames,
       crops, embeddings, backups and exports are never touched.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..common.context import RunContext
from ..common.decision import Decision, write_decision
from ..metrics.tracks import get_track

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


# --------------------------------------------------------------------- t06
def _run_t06(ctx: RunContext) -> None:
    releases = pd.read_csv(ctx.input_dir / "releases.csv", dtype=str)
    rows = []
    for r in releases.itertuples():
        d = ctx.input_dir / r.path
        ok = (d / "annotations.json").exists() and (d / "release_meta.json").exists()
        if ok:
            with open(d / "annotations.json", "r", encoding="utf-8") as f:
                ann = json.load(f)
            ok = all(k in ann for k in ("images", "annotations", "categories"))
        rows.append({"release_id": r.release_id, "decision": "PASS" if ok else "HOLD",
                     "root_cause": "clean" if ok else "stale_map",
                     "reason": "schema parse only (baseline)"})
    pd.DataFrame(rows).to_csv(ctx.out("release_decisions.csv"), index=False)
    write_decision(Decision(status="GO", reason_code="BASELINE_SCHEMA_ONLY",
                            reason="baseline: JSON schema check only, no semantic invariants",
                            owner=ctx.config.get("owner", "baseline"), scope=ctx.track),
                   ctx.out("decision.json"), ctx.out("decision_log.csv"))


# --------------------------------------------------------------------- t07
def _run_t07(ctx: RunContext) -> None:
    """Default-detector baseline: coarse colour thresholds + fixed blur, no queue/audit.

    OpenCV 5.0 removed the Haar cascade API (`cv2.CascadeClassifier` is gone and
    headless builds ship no `objdetect`), so the "default detector" here is the
    naive YCrCb skin rule + a bright-pixel rule with a FIXED 6 px pad — no shape
    filtering, no night palette, no occlusion handling, no review queue.
    params.mode / run_name 'blur_all' gives the trivial redact-everything
    baseline the T07 doc asks groups to report (miss rate 0, guardrail FAIL).
    """
    if cv2 is None:
        raise RuntimeError("opencv required")
    images = pd.read_csv(ctx.input_dir / "images.csv", dtype={"image_id": str})
    mode = ctx.param("mode", "blur_all" if ctx.run_name == "blur_all" else "coarse")
    rows = []
    for r in images.itertuples():
        bgr = cv2.imread(str(ctx.input_dir / r.path), cv2.IMREAD_COLOR)
        H, W = bgr.shape[:2]
        if mode == "blur_all":
            boxes = [(0, 0, W, H)]
        else:
            ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
            skin = (ycrcb[:, :, 1] >= 133) & (ycrcb[:, :, 1] <= 173) & \
                   (ycrcb[:, :, 2] >= 77) & (ycrcb[:, :, 2] <= 127)
            bright = (bgr.min(axis=2) >= 195)
            mask = (skin | bright).astype(np.uint8)
            n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
            boxes = []
            for i in range(1, n):
                x, y, w, h, area = stats[i]
                if area < 20 or max(w, h) > 120:
                    continue
                boxes.append((max(0, x - 6), max(0, y - 6), min(W - x + 6, w + 12), min(H - y + 6, h + 12)))
        for (x, y, w, h) in boxes:
            rows.append({"image_id": r.image_id, "x": x, "y": y, "w": w, "h": h})
    pd.DataFrame(rows, columns=["image_id", "x", "y", "w", "h"]).to_csv(ctx.out("redactions.csv"), index=False)
    write_decision(Decision(status="GO", reason_code="BASELINE_NO_QUEUE",
                            reason=f"baseline ({mode}): fixed detector, no review queue or audit",
                            owner=ctx.config.get("owner", "baseline"), scope=ctx.track),
                   ctx.out("decision.json"), ctx.out("decision_log.csv"))


# --------------------------------------------------------------------- t08
def _run_t08(ctx: RunContext) -> None:
    graph = json.loads((ctx.input_dir / "data_graph.json").read_text(encoding="utf-8"))
    requests = json.loads((ctx.input_dir / "requests.json").read_text(encoding="utf-8"))
    rows = []
    for req in requests:
        raw = next((a for a in graph["artefacts"]
                    if a["type"] == "raw_video" and req["subject_id"] in a["subject_ids"]), None)
        if raw:
            rows.append({"request_id": req["request_id"], "artefact_id": raw["artefact_id"],
                         "action": "delete", "reason": "baseline: raw file only, by filename match"})
    pd.DataFrame(rows, columns=["request_id", "artefact_id", "action", "reason"]).to_csv(
        ctx.out("actions.csv"), index=False)
    write_decision(Decision(status="GO", reason_code="BASELINE_RAW_ONLY",
                            reason="baseline: deletes one raw artefact per request, ignores derived copies",
                            owner=ctx.config.get("owner", "baseline"), scope=ctx.track),
                   ctx.out("decision.json"), ctx.out("decision_log.csv"))


def run(ctx: RunContext) -> None:
    if ctx.track == "t12":
        from dataclasses import replace

        from ..pipeline_t12 import run as run_t12

        params = {**(ctx.config.get("params") or {}), "pipeline_t12_sampling": "random"}
        run_t12(replace(ctx, config={**ctx.config, "params": params}))
    elif ctx.track in {"t06", "t07", "t08"}:
        {"t06": _run_t06, "t07": _run_t07, "t08": _run_t08}[ctx.track](ctx)
    else:
        from . import REGISTRY

        if ctx.track not in {"t04", "t05", "t10", "t11", "t13", "t14"}:
            raise ValueError(f"No comparison baseline for {ctx.track} in this module")
        REGISTRY[ctx.track].run(ctx, baseline=True)
    spec = get_track(ctx.track)
    ctx.stats["baseline"] = True
    ctx.stats["pred_file"] = spec.pred_file
