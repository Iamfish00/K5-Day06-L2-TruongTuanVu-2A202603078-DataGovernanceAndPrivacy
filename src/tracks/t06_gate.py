"""T06 — Dataset Semantic Integrity & Provenance Gate (PoC).

Five lab steps: load -> process (invariant checks vs parent release) -> rank
(review queue) -> review (human overrides) -> decide (PASS/HOLD/REWORK).

Silent defects detected (all keep the JSON schema valid):
  * stale_map    : declared label_map_version does not match `categories`
                   (category set equals an OLDER map, e.g. lm-2 while claiming lm-3)
  * class_swap   : category id->name assignment permuted vs the declared label map
  * id_collision : duplicated annotation / image ids inside the release
  * dropped_frames: images missing vs parent or non-contiguous frame_index per sequence
  * bbox_shift   : systematic bbox translation vs parent (mean offset well above
                   the benign sub-pixel jitter present in every release)

Benign diffs (present in *every* release, therefore NOT a signal):
  * +1 new frame with its own annotations
  * sub-pixel bbox re-rounding (|dx|,|dy| < 1 px on a handful of boxes)

Outputs: out/release_decisions.csv (release_id, decision, root_cause, ...) and
out/release_report.json (machine-readable gate report, milestone 180').
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import pandas as pd

from ..common.context import RunContext
from ..common.decision import Decision, write_decision
from ..common.io import sha256_file
from ..common.manifest import verify_manifest
from ..common.review import apply_overrides, load_review_decisions, make_review_queue, write_review_queue

# frozen BEFORE any eval result was seen (no gt/ in the student repo anyway)
BBOX_JITTER_TOL = 1.0        # px, per-box benign re-rounding tolerance
BBOX_SHIFT_MIN_FRAC = 0.10   # >=10% of shared annotations moved => systematic
BBOX_SHIFT_MIN_MEAN = 3.0    # px, mean |offset| of the moved boxes
SEVERITY = {"class_swap": 5, "stale_map": 5, "id_collision": 4, "dropped_frames": 4,
            "bbox_shift": 3, "parent_sha_mismatch": 5}
REWORK_CAUSES = {"id_collision", "dropped_frames"}   # vendor must fix & resubmit
HOLD_CAUSES = {"class_swap", "stale_map", "bbox_shift", "parent_sha_mismatch"}


# ---------------------------------------------------------------- helpers
def _load_coco(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _index(ann: dict) -> tuple[dict, dict, dict]:
    imgs = {i["id"]: i for i in ann["images"]}
    anns = {a["id"]: a for a in ann["annotations"]}
    cats = {c["id"]: c["name"] for c in ann["categories"]}
    return imgs, anns, cats


def _frame_gaps(ann: dict) -> list[str]:
    seqs: dict[str, list[int]] = {}
    for i in ann["images"]:
        seqs.setdefault(str(i.get("sequence_id", "na")), []).append(int(i.get("frame_index", 0)))
    gaps = []
    for s, fr in sorted(seqs.items()):
        fr = sorted(fr)
        if fr and fr != list(range(fr[0], fr[0] + len(fr))):
            gaps.append(s)
    return gaps


def check_release(rel_dir: Path, parent: dict, parent_sha: str, label_maps: dict,
                  parent_map_version: str) -> dict:
    """All invariant checks for one candidate release. Returns findings + root cause."""
    ann = _load_coco(rel_dir / "annotations.json")
    meta = json.loads((rel_dir / "release_meta.json").read_text(encoding="utf-8"))
    p_imgs, p_anns, _ = _index(parent)
    c_imgs, c_anns, c_cats = _index(ann)
    findings: list[dict] = []

    def add(kind: str, detail: str, **extra) -> None:
        findings.append({"check": kind, "detail": detail, **extra})

    # provenance: parent hash declared by the release must match the real parent
    declared = meta.get("parent_sha256")
    if declared and declared != parent_sha:
        add("parent_sha_mismatch", f"release_meta.parent_sha256={declared[:12]}... != parent {parent_sha[:12]}...")

    # label map consistency
    lm = meta.get("label_map_version")
    declared_map = {int(k): v for k, v in (label_maps.get(lm) or {}).items()}
    if declared_map:
        if declared_map != c_cats:
            older = [v for v, m in label_maps.items() if v != lm
                     and {int(k): n for k, n in m.items()} == c_cats]
            if older or set(c_cats.values()) < set(declared_map.values()):
                add("stale_map", f"categories match older map {older[0] if older else '(subset)'} while meta claims {lm}")
            elif set(c_cats.values()) == set(declared_map.values()):
                swapped = {i: (declared_map.get(i), c_cats.get(i)) for i in declared_map if declared_map.get(i) != c_cats.get(i)}
                add("class_swap", f"id->name permuted vs {lm}: {swapped}")
            else:
                add("class_swap", f"categories disagree with declared map {lm}: {c_cats}")
    elif lm != parent_map_version:
        add("stale_map", f"label_map_version {lm} != parent {parent_map_version} and unknown in label_maps.json")

    # unique ids
    dup_ann = len(ann["annotations"]) - len(c_anns)
    dup_img = len(ann["images"]) - len(c_imgs)
    if dup_ann or dup_img:
        add("id_collision", f"duplicate ids: {dup_ann} annotations, {dup_img} images")

    # frames vs parent
    missing = sorted(set(p_imgs) - set(c_imgs))
    if missing:
        add("dropped_frames", f"{len(missing)} parent images missing (e.g. {missing[:3]})")
    gaps = _frame_gaps(ann)
    if gaps:
        add("dropped_frames", f"non-contiguous frame_index in sequences {gaps}")

    # bbox drift vs parent (shared annotation ids only)
    shared = set(p_anns) & set(c_anns)
    moved = []
    for k in shared:
        pb, cb = p_anns[k]["bbox"], c_anns[k]["bbox"]
        dx, dy = cb[0] - pb[0], cb[1] - pb[1]
        if abs(dx) > BBOX_JITTER_TOL or abs(dy) > BBOX_JITTER_TOL:
            moved.append((dx, dy))
    if shared and len(moved) / len(shared) >= BBOX_SHIFT_MIN_FRAC:
        mdx = statistics.mean(abs(d) for d, _ in moved)
        mdy = statistics.mean(abs(d) for _, d in moved)
        if max(mdx, mdy) >= BBOX_SHIFT_MIN_MEAN:
            add("bbox_shift", f"{len(moved)}/{len(shared)} boxes moved, mean |dx|={mdx:.1f} |dy|={mdy:.1f} px")

    causes = [f["check"] for f in findings if f["check"] in SEVERITY]
    order = ["class_swap", "stale_map", "id_collision", "dropped_frames", "bbox_shift", "parent_sha_mismatch"]
    root = next((c for c in order if c in causes), "clean")
    score = sum(SEVERITY[c] for c in causes)
    return {"findings": findings, "root_cause": root, "score": score,
            "n_images": len(c_imgs), "n_annotations": len(c_anns), "meta": meta}


# ---------------------------------------------------------------- 01 load
def load(ctx: RunContext) -> dict:
    manifest = ctx.input_dir / "manifest.csv"
    problems = verify_manifest(manifest) if manifest.exists() else [{"problem": "no_manifest"}]
    if problems:
        ctx.audit.log(actor="pipeline", action="provenance_problems", item_id=str(manifest), after=problems[:20])
        if ctx.config.get("strict_provenance", True):
            raise ValueError(f"provenance check failed ({len(problems)} problems), first: {problems[:3]}")
    parent_rel = ctx.param("parent_release", "releases/v1.0")
    parent_dir = ctx.input_dir / parent_rel
    parent = _load_coco(parent_dir / "annotations.json")
    label_maps = json.loads((ctx.input_dir / "releases" / "label_maps.json").read_text(encoding="utf-8"))
    pmeta = json.loads((parent_dir / "release_meta.json").read_text(encoding="utf-8"))
    releases = pd.read_csv(ctx.input_dir / "releases.csv", dtype=str)
    return {"releases": releases, "parent": parent,
            "parent_sha": sha256_file(parent_dir / "annotations.json"),
            "label_maps": label_maps, "parent_map_version": pmeta.get("label_map_version")}


# ---------------------------------------------------------------- 02 process
def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    rows = []
    for r in data["releases"].itertuples():
        rel_dir = ctx.input_dir / r.path
        res = check_release(rel_dir, data["parent"], data["parent_sha"],
                            data["label_maps"], data["parent_map_version"])
        decision = "PASS" if res["root_cause"] == "clean" else (
            "REWORK" if res["root_cause"] in REWORK_CAUSES else "HOLD")
        rows.append({
            "release_id": r.release_id,
            "split": r.split,
            "score": float(res["score"]),
            "decision": decision,
            "root_cause": res["root_cause"],
            "reason": "; ".join(f"{f['check']}: {f['detail']}" for f in res["findings"]) or "all invariants hold vs parent",
            "evidence": str(rel_dir),
            "findings": json.dumps(res["findings"], ensure_ascii=False),
        })
        for f in res["findings"]:
            ctx.audit.log(actor="gate", action=f"violation:{f['check']}", item_id=r.release_id,
                          after=f["detail"], reason=res["root_cause"])
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 03 rank
def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    budget = int(ctx.param("budget", 20))
    q = make_review_queue(scored, id_col="release_id", score_col="score", budget=budget,
                          reason_col="reason", action_col="decision", evidence_col="evidence")
    write_review_queue(q, ctx.out("review_queue.csv"))
    return q


# ---------------------------------------------------------------- 04 review
def review(ctx: RunContext, scored: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"
    if not reviewed_path.exists():
        return scored
    reviewed = load_review_decisions(reviewed_path, allowed={"approve", "reject", "edit"})
    ctx.stats["n_reviewed"] = len(reviewed)
    out = scored.copy()
    # approve = confirm gate decision; reject = reviewer clears the release; edit = override_value
    for r in reviewed.itertuples(index=False):
        mask = out.release_id == r.item_id
        if not mask.any():
            continue
        before = out.loc[mask, "decision"].iloc[0]
        if r.reviewer_decision == "reject":
            after, cause = "PASS", "clean"
        elif r.reviewer_decision == "edit" and str(r.override_value).strip():
            after = str(r.override_value).strip().upper()
            cause = out.loc[mask, "root_cause"].iloc[0]
        else:
            continue
        out.loc[mask, "decision"] = after
        out.loc[mask, "root_cause"] = cause
        ctx.audit.log(actor=r.reviewer, action=f"override:{r.reviewer_decision}", item_id=r.item_id,
                      before=before, after=after, reason=r.review_note)
    return out


# ---------------------------------------------------------------- 05 decide
def decide(ctx: RunContext, final: pd.DataFrame) -> Decision:
    blocked = final[final.decision.str.upper() != "PASS"]
    status = "HOLD" if len(blocked) else "GO"
    return Decision(
        status=status,
        reason_code="RELEASES_BLOCKED" if len(blocked) else "ALL_RELEASES_CLEAN",
        reason=(f"{len(blocked)}/{len(final)} candidate releases blocked by the semantic gate: "
                + ", ".join(f"{r.release_id}({r.root_cause})" for r in blocked.itertuples()))
        if len(blocked) else "every candidate release passed all semantic invariants vs parent",
        owner=ctx.config.get("owner", "TODO-owner"),
        scope=ctx.track,
        evidence=[str(ctx.out("release_decisions.csv")), str(ctx.out("release_report.json"))],
        metrics={"n_releases": int(len(final)), "n_blocked": int(len(blocked))},
    )


def run(ctx: RunContext) -> None:
    data = load(ctx)                                                       # 01
    scored = process(ctx, data)                                            # 02
    ctx.stats["n_items"] = len(scored)
    queue = rank(ctx, scored)                                              # 03
    final = review(ctx, scored, queue)                                     # 04
    cols = ["release_id", "decision", "root_cause", "split", "score", "reason", "evidence", "findings"]
    final[cols].to_csv(ctx.out("release_decisions.csv"), index=False)
    report = {"track": ctx.track, "run_name": ctx.run_name, "seed": ctx.seed,
              "parent_sha256": data["parent_sha"],
              "releases": [{k: r[k] for k in ("release_id", "decision", "root_cause", "score", "reason")}
                           for _, r in final.iterrows()],
              "findings": {r.release_id: json.loads(r.findings) for _, r in final.iterrows()}}
    from ..common.io import write_json
    write_json(ctx.out("release_report.json"), report)                     # machine-readable gate report
    write_decision(decide(ctx, final), ctx.out("decision.json"), ctx.out("decision_log.csv"))  # 05
