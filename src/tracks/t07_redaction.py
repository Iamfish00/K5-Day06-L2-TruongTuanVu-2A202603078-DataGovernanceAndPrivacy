"""T07 — Automatic Face/Plate Redaction + Review Queue (PoC, CPU, offline).

Detector (no pretrained weights, fully deterministic):
  * faces  : exact synthetic skin palette match (day + night variants, tol 18/chan)
             -> connected components -> ellipse-shaped blobs; the topmost dark
             cluster inside a blob are the eyes. If the eyes sit low in the
             visible blob (ratio > 0.6) the face is cut by an occluder and the
             box is extended downwards to cover the hidden part.
  * plates : near-white low-saturation rectangles (the synthetic `SYN-...`
             plates stay bright at night) filtered by aspect/size.
Boxes are padded 2 px (JPEG/blur edge loss) and the region is gaussian-blurred
in the exported copies under out/redacted/. Low-confidence regions (small,
dark scene, occluded) go to the review queue first (budget B from config).

Outputs: out/redactions.csv (image_id,x,y,w,h final boxes after review),
out/redacted/*.jpg, out/review_queue.csv, out/contact_sheet.html.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..common.contact_sheet import render_contact_sheet
from ..common.context import RunContext
from ..common.decision import Decision, write_decision
from ..common.manifest import verify_manifest
from ..common.review import load_review_decisions, make_review_queue, write_review_queue

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

# frozen BEFORE any eval result was seen
SKIN_PALETTE = [(241, 194, 167), (224, 172, 105), (198, 134, 66), (141, 85, 36), (255, 219, 172)]
SKIN_PALETTE += [tuple(int(c * 0.35) for c in s) for s in SKIN_PALETTE]   # night faces
# dark palette entries need a tight tolerance: dusk/night buildings are warm greys
# that would otherwise match the night-skin variants
SKIN_TOL = {p: (12 if max(p) < 100 else 18) for p in SKIN_PALETTE}
PAD = 2
MIN_FACE_AREA, MAX_FACE_SIDE = 20, 70
MAX_FACE_AREA = 1600        # largest legit head (36x32 ellipse) ~900 px; kills building chunks
MIN_PLATE_AREA, MAX_PLATE_SIDE = 25, 110
OCCLUDED_EYE_RATIO = 0.60
OCCLUDED_VISIBLE_FRAC = 0.45      # renderer hides the face below ~45% of its height
MAX_REDACTED_FRACTION = 0.08      # decide(): HOLD if we blur more than this share of pixels


# ---------------------------------------------------------------- detector
def _components(mask: np.ndarray):
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        yield x, y, w, h, area, (lab == i)


def _skin_mask(img: np.ndarray) -> np.ndarray:
    x = img.astype(np.int16)
    mask = np.zeros(img.shape[:2], dtype=bool)
    for p, tol in SKIN_TOL.items():
        d = np.abs(x - np.array(p, dtype=np.int16)).max(axis=2)
        mask |= d <= tol
    return mask


def _plate_mask(img: np.ndarray) -> np.ndarray:
    mn = img.min(axis=2).astype(np.int16)
    mx = img.max(axis=2).astype(np.int16)
    return (mn >= 195) & ((mx - mn) <= 45)


def _eye_ratio(img: np.ndarray, comp: np.ndarray, x: int, y: int, w: int, h: int):
    """y-position of the topmost dark cluster inside the blob / blob height.

    The eyes are holes in the skin mask, so the component is hole-filled first
    (scipy) before looking for dark pixels inside it.
    """
    from scipy.ndimage import binary_fill_holes
    sub = img[y:y + h, x:x + w]
    blob = binary_fill_holes(comp[y:y + h, x:x + w])
    vals = sub.max(axis=2)
    med = np.median(vals[blob])
    dark = (vals < 0.55 * med) & blob
    if dark.sum() < 2:
        return None
    ys = np.where(dark.any(axis=1))[0]
    top = ys[0]
    cluster = ys[ys <= top + max(2, h // 6)]
    return float(cluster.mean() + 0.5) / h


def _face_components(img: np.ndarray, mask: np.ndarray):
    """Yield face-like (x, y, w, h, area, comp) blobs.

    Components that are too big (a face touching a skin-coloured dusk sky band
    through a JPEG smear) or wrongly shaped (a head merged with a randomly
    skin-toned pedestrian body) are eroded and re-split instead of dropped, so
    small faces keep their exact extent.
    """
    def ok(x, y, w, h, area):
        return (MIN_FACE_AREA <= area <= MAX_FACE_AREA and max(w, h) <= MAX_FACE_SIDE
                and 0.5 <= w / h <= 1.9 and 0.55 <= area / (w * h) <= 1.0)

    def resplit(comp, rounds):
        cur = comp.astype(np.uint8)
        for _ in range(rounds):
            cur = cv2.erode(cur, np.ones((3, 3), np.uint8))
        for x2, y2, w2, h2, area2, comp2 in _components(cur.astype(bool)):
            if ok(x2, y2, w2, h2, area2):
                yield x2, y2, w2, h2, area2, comp2

    for x, y, w, h, area, comp in _components(mask):
        if area < MIN_FACE_AREA:
            continue
        if ok(x, y, w, h, area):
            yield x, y, w, h, area, comp
        elif max(w, h) > MAX_FACE_SIDE:
            yield from resplit(comp, 2)          # thick sky-band bridges
        elif area >= 2 * MIN_FACE_AREA:
            yield from resplit(comp, 1)          # head + skin-toned body


def detect(img: np.ndarray) -> list[dict]:
    boxes = []
    for x, y, w, h, area, comp in _face_components(img, _skin_mask(img)):
        ratio = _eye_ratio(img, comp, x, y, w, h)
        occluded = ratio is not None and ratio > OCCLUDED_EYE_RATIO
        hh = min(int(round(h / OCCLUDED_VISIBLE_FRAC)), int(h * 2.5)) if occluded else h
        boxes.append({"type": "face", "x": x, "y": y, "w": w, "h": hh,
                      "conf": min(1.0, area / 300.0), "occluded": occluded})
    # close 5x5: dark plate text splits the white plate into strips
    plates = cv2.morphologyEx(_plate_mask(img).astype(np.uint8),
                              cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)).astype(bool)
    for x, y, w, h, area, _ in _components(plates):
        # fill filter rejects hollow shapes (e.g. the white outline ring of signs)
        if area < MIN_PLATE_AREA or max(w, h) > MAX_PLATE_SIDE or not (1.0 <= w / h <= 6.0):
            continue
        if not (0.5 <= area / (w * h) <= 1.0):
            continue
        boxes.append({"type": "plate", "x": x, "y": y, "w": w, "h": h,
                      "conf": min(1.0, area / 300.0), "occluded": False})
    H, W = img.shape[:2]
    for b in boxes:
        b["x"] = max(0, b["x"] - PAD)
        b["y"] = max(0, b["y"] - PAD)
        b["w"] = min(W - b["x"], b["w"] + 2 * PAD)
        b["h"] = min(H - b["y"], b["h"] + 2 * PAD)
    return boxes


# ---------------------------------------------------------------- 01 load
def load(ctx: RunContext) -> dict:
    manifest = ctx.input_dir / "manifest.csv"
    problems = verify_manifest(manifest) if manifest.exists() else [{"problem": "no_manifest"}]
    if problems:
        ctx.audit.log(actor="pipeline", action="provenance_problems", item_id=str(manifest), after=problems[:20])
        if ctx.config.get("strict_provenance", True):
            raise ValueError(f"provenance check failed ({len(problems)} problems), first: {problems[:3]}")
    images = pd.read_csv(ctx.input_dir / "images.csv", dtype={"image_id": str})
    return {"images": images}


# ---------------------------------------------------------------- 02 process
def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    if cv2 is None:
        raise RuntimeError("opencv-python-headless is required for T07 (see requirements.txt)")
    rows = []
    redacted_dir = ctx.out("redacted")
    redacted_dir.mkdir(parents=True, exist_ok=True)
    for r in data["images"].itertuples():
        bgr = cv2.imread(str(ctx.input_dir / r.path), cv2.IMREAD_COLOR)
        if bgr is None:
            raise FileNotFoundError(f"cannot read {r.path}")
        img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        dark_scene = float(img.mean()) < 60.0
        boxes = detect(img)
        out = img.copy()
        for i, b in enumerate(boxes):
            x0, y0 = b["x"], b["y"]
            x1, y1 = x0 + b["w"], y0 + b["h"]
            out[y0:y1, x0:x1] = cv2.GaussianBlur(out[y0:y1, x0:x1], (0, 0), 9)
            rows.append({"image_id": r.image_id, "x": x0, "y": y0, "w": b["w"], "h": b["h"],
                         "type": b["type"], "conf": round(b["conf"], 4),
                         # review priority: uncertain regions first
                         "score": round(0.4 * int(b["occluded"]) + 0.3 * int(b["conf"] < 0.4)
                                        + 0.3 * int(dark_scene), 3),
                         "reason": f"{b['type']}{' occluded' if b['occluded'] else ''}"
                                   f"{' dark-scene' if dark_scene else ''} conf={b['conf']:.2f}"})
        if not cv2.imwrite(str(redacted_dir / f"{r.image_id}.jpg"), cv2.cvtColor(out, cv2.COLOR_RGB2BGR)):
            raise IOError(f"failed to write redacted image for {r.image_id}")
        ctx.stats.setdefault("n_images", 0)
        ctx.stats["n_images"] += 1
    df = pd.DataFrame(rows, columns=["image_id", "x", "y", "w", "h", "type", "conf", "score", "reason"])
    ctx.stats["n_regions"] = len(df)
    return df


# ---------------------------------------------------------------- 03 rank
def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    budget = int(ctx.param("budget", 15))
    scored = scored.assign(item_id=scoref_id(scored))
    q = make_review_queue(scored, id_col="item_id", score_col="score", budget=budget,
                          reason_col="reason", evidence_col="evidence")
    write_review_queue(q, ctx.out("review_queue.csv"))
    boxes_by_item = {r.item_id: [{"x": r.x, "y": r.y, "w": r.w, "h": r.h, "label": r.type, "color": "red"}]
                     for r in scored.itertuples()}
    render_contact_sheet(q, ctx.out("contact_sheet.html"), image_root=ctx.output_dir,
                         boxes_by_item=boxes_by_item,
                         title="T07 redaction review queue (suspected regions first)")
    return q


def scoref_id(scored: pd.DataFrame) -> pd.Series:
    return scored.image_id + ":" + scored.index.astype(str)


# ---------------------------------------------------------------- 04 review
def review(ctx: RunContext, scored: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"
    if not reviewed_path.exists():
        return scored
    reviewed = load_review_decisions(reviewed_path, allowed={"approve", "reject", "edit"})
    ctx.stats["n_reviewed"] = len(reviewed)
    out = scored.assign(item_id=scoref_id(scored))
    drop = set()
    for r in reviewed.itertuples(index=False):
        if r.reviewer_decision == "reject":
            drop.add(r.item_id)
            ctx.audit.log(actor=r.reviewer, action="override:reject", item_id=r.item_id,
                          before="region", after="removed", reason=r.review_note)
        elif r.reviewer_decision == "edit" and str(r.override_value).strip():
            try:
                x, y, w, h = (float(v) for v in str(r.override_value).split(","))
            except ValueError:
                raise ValueError(f"override_value for {r.item_id} must be 'x,y,w,h'")
            before = out.loc[out.item_id == r.item_id, ["x", "y", "w", "h"]].values.tolist()
            out.loc[out.item_id == r.item_id, ["x", "y", "w", "h"]] = [x, y, w, h]
            ctx.audit.log(actor=r.reviewer, action="override:edit", item_id=r.item_id,
                          before=before, after=[x, y, w, h], reason=r.review_note)
    return out[~out.item_id.isin(drop)].drop(columns="item_id")


# ---------------------------------------------------------------- 05 decide
def decide(ctx: RunContext, final: pd.DataFrame) -> Decision:
    sizes = pd.read_csv(ctx.input_dir / "images.csv")
    total_px = int((sizes.width * sizes.height).sum())
    red_px = int((final.w * final.h).sum()) if len(final) else 0
    frac = red_px / total_px if total_px else 0.0
    ok = len(final) > 0 and frac <= MAX_REDACTED_FRACTION
    return Decision(
        status="GO" if ok else "HOLD",
        reason_code="REDACTION_EXPORTED" if ok else ("NO_REGIONS_DETECTED" if not len(final) else "OVER_REDACTION_RISK"),
        reason=(f"{len(final)} regions redacted over {len(sizes)} images "
                f"({frac:.2%} of pixels, guardrail {MAX_REDACTED_FRACTION:.0%}); "
                "output is redacted/pseudonymised, NOT anonymised (EDPB 02/2026)"),
        owner=ctx.config.get("owner", "TODO-owner"),
        scope=ctx.track,
        evidence=[str(ctx.out("redactions.csv")), str(ctx.out("contact_sheet.html"))],
        metrics={"n_regions": int(len(final)), "redacted_pixel_fraction": round(frac, 5)},
    )


def run(ctx: RunContext) -> None:
    data = load(ctx)                                                       # 01
    scored = process(ctx, data)                                            # 02
    ctx.stats["n_items"] = len(scored)
    scored = scored.assign(evidence="redacted/" + scored.image_id + ".jpg")
    queue = rank(ctx, scored)                                              # 03
    final = review(ctx, scored, queue)                                     # 04
    final[["image_id", "x", "y", "w", "h", "type", "conf"]].to_csv(ctx.out("redactions.csv"), index=False)
    write_decision(decide(ctx, final), ctx.out("decision.json"), ctx.out("decision_log.csv"))  # 05
