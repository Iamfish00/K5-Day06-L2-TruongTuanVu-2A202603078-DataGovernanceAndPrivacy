"""YOUR PoC PIPELINE — this is the file your group owns.

Lab contract = 5 automated steps, run by `python -m src.main ...`:
    01 load      input + provenance check (manifest.csv)
    02 process   your algorithm / model (the research part)
    03 rank      prioritise for a fixed review budget B
    04 review    human-in-the-loop: queue -> reviewer decisions -> overrides + audit log
    05 decide    GO / HOLD / REWORK (or track equivalent) with reason_code + owner, write outputs

Required output: out/<pred_file> with the columns listed for your track in
docs/tracks/TXX.md (also in src/metrics/tracks.py::TRACKS). Then evaluate with
    python -m src.evaluate --track tXX --pred out/<pred_file>

Rules: never read fixtures/*/gt/ here (the GT guard will stop you); seed everything via ctx.rng;
no network calls; fail loudly on missing models instead of downloading.
See src/examples/random_baseline.py for a minimal end-to-end wiring example.
"""
from __future__ import annotations

import pandas as pd

from .common.contact_sheet import render_contact_sheet
from .common.context import RunContext
from .common.decision import Decision, write_decision
from .common.manifest import verify_manifest
from .common.review import load_review_decisions, make_review_queue, write_review_queue
from .metrics.tracks import get_track
import math
import yaml
from pathlib import Path

from scipy.stats import hypergeom

from .common.audit_oracle import AuditOracle
from .common.gt_guard import GroundTruthAccessError

Z95 = 1.959964
TAG_W = {"normal": 0.0, "night": 0.5, "occlusion": 0.6, "small_object": 0.7}

# ---------------------------------------------------------------- 01 load
def load(ctx: RunContext) -> dict:
    manifest = ctx.input_dir / "manifest.csv"
    problems = verify_manifest(manifest) if manifest.exists() else [{"problem": "no_manifest"}]
    if problems:
        ctx.audit.log(actor="pipeline", action="provenance_problems", item_id=str(manifest), after=problems[:20])
        if ctx.config.get("strict_provenance", True):
            raise ValueError(f"provenance check failed ({len(problems)} problems), first: {problems[:3]}")

    policy = yaml.safe_load((ctx.input_dir / "acceptance_policy.yaml").read_text(encoding="utf-8"))
    b = pd.read_csv(ctx.input_dir / "batches.csv")

    need = {"sample_id", "batch_id", "vendor", "risk_tag", "annotator_id", "seconds_spent", "n_boxes"}
    if need - set(b.columns):
        raise ValueError(f"batches.csv missing columns {sorted(need - set(b.columns))}")
    if b["sample_id"].duplicated().any():
        raise ValueError("duplicate sample_id in batches.csv")
    if b[["seconds_spent", "n_boxes"]].isna().any().any():
        raise ValueError("NaN in seconds_spent / n_boxes")
    ctx.audit.log(actor="pipeline", action="batch_sizes", item_id="batches.csv",
                  after=b.groupby("batch_id").size().to_dict())
    return {"manifest": pd.read_csv(manifest), "batches": b, "policy": policy}



# ---------------------------------------------------------------- helpers
def _wilson(p: float, n: float, z: float = Z95):
    if n <= 0:
        return 0.0, 1.0
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def _hyp_cdf(k: int, N: int, M: int, n: int) -> float:
    """P(X <= k), X ~ Hypergeometric(N, M lỗi, n mẫu)."""
    tot = math.comb(N, n)
    return sum(math.comb(M, i) * math.comb(N - M, n - i)
               for i in range(0, min(k, M, n) + 1) if 0 <= n - i <= N - M) / tot


def _stratified_accept_p(k, population, audited, bad_min):
    """Worst-case lower tail under all allocations of the minimum bad count."""
    hi, lo = population["hi"], population["lo"]
    n_hi, n_lo = audited["hi"], audited["lo"]
    if bad_min > hi + lo:
        return 0.0
    return max(
        sum(hypergeom.pmf(i, hi, bad_hi, n_hi) * hypergeom.cdf(k - i, lo, bad_min - bad_hi, n_lo)
            for i in range(min(k, n_hi) + 1))
        for bad_hi in range(max(0, bad_min - lo), min(bad_min, hi) + 1)
    )


def _risk_scores(b: pd.DataFrame) -> pd.DataFrame:
    """Điểm rủi ro chỉ từ metadata. Trọng số cố định trước, KHÔNG tune theo eval."""
    b = b.copy()
    b["spb"] = b["seconds_spent"] / b["n_boxes"].clip(lower=1)
    med = b.groupby("batch_id")["spb"].transform("median")
    f_fast = (1 - b["spb"] / med).clip(0, 1)
    ann = b.groupby("annotator_id")["spb"].median()
    z = (ann - ann.median()) / (ann.std(ddof=0) + 1e-9)
    f_ann = (-b["annotator_id"].map(z)).clip(0, 2) / 2
    f_abs = (b["seconds_spent"] < 10).astype(float)
    f_tag = b["risk_tag"].map(TAG_W).fillna(0.3)
    b["risk"] = 1.0 * f_fast + 0.8 * f_ann + 0.8 * f_abs + 0.6 * f_tag
    return b


def _gt_available(ctx: RunContext) -> Path | None:
    if ctx.param("use_gt", True) is False:
        return None
    p = ctx.input_dir / "gt" / "hidden_errors.csv"
    try:
        return p if p.exists() else None
    except GroundTruthAccessError:
        return None


def _get_labels(ctx: RunContext, picks: pd.DataFrame, budget: int):
    """Trả về (labels: {sample_id: 0/1}, source: 'oracle' | 'human' | 'none')."""
    ids = picks["sample_id"].tolist()

    gt = _gt_available(ctx)
    if gt is not None:
        oracle = AuditOracle(str(gt), "sample_id", "is_error", "batch_id",
                             budget_per_group=budget, audit=ctx.audit)
        return {k: int(v) for k, v in oracle.inspect(ids).items()}, "oracle"

    human = ctx.output_dir / "audit_labels.csv"
    if human.exists():
        df = pd.read_csv(human)
        if not {"sample_id", "is_error"} <= set(df.columns):
            raise ValueError("audit_labels.csv cần cột sample_id,is_error")
        df = df[df["sample_id"].isin(ids)].dropna(subset=["is_error"])
        if not df.sample_id.is_unique or not df.is_error.isin([0, 1]).all():
            raise ValueError("Audit labels must have unique sample IDs and binary is_error values")
        if len(df):
            for r in df.itertuples(index=False):
                ctx.audit.log(actor="human_auditor", action="audit_label", item_id=str(r.sample_id),
                              after=int(r.is_error))
            return dict(zip(df["sample_id"], df["is_error"].astype(int))), "human"

    return {}, "none"


def _plan_samples(ctx, scored: pd.DataFrame, B: int, n_hi: int, high_frac: float) -> pd.DataFrame:
    """Chọn mẫu audit: stratified random (tầng rủi ro cao / còn lại), cố định theo seed."""
    method = ctx.param("pipeline_t12_sampling", "stratified")
    if method not in {"stratified", "random"}:
        raise ValueError(f"Unknown T12 sampling method: {method}")
    if B <= 0 or not 0 < n_hi < B or not 0 < high_frac < 1:
        raise ValueError("Invalid T12 sample allocation")
    out = []
    for bid, g in scored.groupby("batch_id", sort=True):
        g = g.sort_values(["risk", "sample_id"], ascending=[False, True])
        if len(g) < B:
            raise ValueError(f"Batch {bid} has fewer rows than the audit budget")
        if method == "random":
            out.extend({"sample_id": str(sample), "batch_id": bid, "stratum": "all", "N_h": len(g)}
                       for sample in ctx.rng.choice(g.sample_id.to_list(), size=B, replace=False))
            continue
        n_pop_hi = int(round(high_frac * len(g)))
        hi, lo = list(g["sample_id"][:n_pop_hi]), list(g["sample_id"][n_pop_hi:])
        if len(hi) < n_hi or len(lo) < B - n_hi:
            raise ValueError(f"Batch {bid} cannot meet the stratified audit allocation")
        p_hi = [str(x) for x in ctx.rng.choice(hi, size=min(n_hi, len(hi)), replace=False)]
        p_lo = [str(x) for x in ctx.rng.choice(lo, size=min(B - n_hi, len(lo)), replace=False)]
        for s in p_hi:
            out.append({"sample_id": s, "batch_id": bid, "stratum": "hi", "N_h": len(hi)})
        for s in p_lo:
            out.append({"sample_id": s, "batch_id": bid, "stratum": "lo", "N_h": len(lo)})
    return pd.DataFrame(out).merge(scored[["sample_id", "risk", "risk_tag", "annotator_id"]], on="sample_id")



# ---------------------------------------------------------------- 02 process
def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    pol = data["policy"]
    B = int(ctx.param("audit_budget_per_batch", pol.get("audit_budget_per_batch", 20)))
    if B > int(pol["audit_budget_per_batch"]):
        raise ValueError("Audit budget exceeds the fixture policy")
    thr = float(ctx.param("max_error_rate", pol.get("max_error_rate", 0.05)))
    n_hi = int(ctx.param("n_high", B // 2))
    high_frac = float(ctx.param("high_frac", 0.30))
    accept_alpha = float(ctx.param("accept_alpha", 1 - float(pol.get("confidence_level", 0.95))))

    scored = _risk_scores(data["batches"])
    plan = _plan_samples(ctx, scored, B, n_hi, high_frac)

    # luôn xuất danh sách mẫu cần audit để người kiểm (kể cả khi chưa có GT)
    sheet = plan[["sample_id", "batch_id", "stratum", "risk_tag", "annotator_id", "risk"]].copy()
    sheet["is_error"] = ""
    sheet.to_csv(ctx.out("audit_sample.csv"), index=False)

    labels, source = _get_labels(ctx, plan, B)
    ctx.stats["label_source"] = source
    ctx.audit.log(actor="pipeline", action="label_source", item_id="audit", after=source)
    print(f"[t12] nguồn nhãn audit: {source}")

    rows = []
    for bid, g in plan.groupby("batch_id", sort=True):
        N = int((scored["batch_id"] == bid).sum())
        hard_bad = math.floor(thr * N) + 1                     # 6/100 => chắc chắn > 5%
        lab = g[g["sample_id"].isin(labels)]
        strata = {s: lab[lab["stratum"] == s] for s in g.stratum.unique()}

        base = {"batch_id": bid, "n_audited": len(lab)}
        if len(lab) == 0:
            rows.append({**base, "est_error_rate": float("nan"), "ci_low": float("nan"),
                         "ci_high": float("nan"), "decision": "HOLD", "k_errors": None,
                         "p_accept_test": None, "reason": "NO_AUDIT_LABELS", "score": 1.0})
            continue
        if len(lab) != len(g):
            rows.append({**base, "est_error_rate": float("nan"), "ci_low": float("nan"),
                         "ci_high": float("nan"), "decision": "HOLD", "k_errors": None,
                         "p_accept_test": None, "reason": "INCOMPLETE_AUDIT", "score": 1.0})
            continue

        Nh = {s: int(g[g["stratum"] == s]["N_h"].iloc[0]) for s in strata}
        nh = {s: len(strata[s]) for s in strata}
        kh = {s: int(strata[s]["sample_id"].map(labels).sum()) for s in strata}
        W = {s: Nh[s] / N for s in strata}

        est = sum(W[s] * kh[s] / nh[s] for s in strata)       # ước lượng có trọng số tầng
        sm = lambda s: (kh[s] + 0.5) / (nh[s] + 1)
        var = sum(W[s] ** 2 * sm(s) * (1 - sm(s)) / nh[s] * (1 - nh[s] / Nh[s]) for s in strata)
        n_eff = est * (1 - est) / var if 0 < est < 1 and var > 0 else len(lab)
        ci_low, ci_high = _wilson(est, float(min(max(n_eff, 5), N)))

        k, n_a = sum(kh.values()), len(lab)
        if "all" in strata:
            p_acc = _hyp_cdf(k, N, hard_bad, n_a) if hard_bad <= N else 0.0
        else:
            p_acc = _stratified_accept_p(k, Nh, nh, hard_bad)
        if k >= hard_bad:
            dec, code = "REWORK", "ERRORS_FOUND_GE_HARD_BAD"
        elif ci_low > thr:
            dec, code = "REWORK", "CI_LOW_ABOVE_THRESHOLD"
        elif p_acc <= accept_alpha:
            dec, code = "ACCEPT", "ACCEPT_EVIDENCE_SUFFICIENT"
        else:
            dec, code = "HOLD", "INSUFFICIENT_EVIDENCE"

        rows.append({**base, "est_error_rate": round(est, 4), "ci_low": round(ci_low, 4),
                     "ci_high": round(ci_high, 4), "decision": dec, "k_errors": k,
                     "p_accept_test": round(p_acc, 4), "reason": code,
                     "score": 1.0 if dec == "HOLD" else 0.5 + 0.5 * (ci_high - ci_low)})
        ctx.audit.log(actor="pipeline", action="batch_decision", item_id=str(bid),
                      after={"decision": dec, "reason": code, "k": k, "n": n_a, "source": source})

    cols = ["batch_id", "est_error_rate", "decision", "n_audited", "ci_low", "ci_high"]
    df = pd.DataFrame(rows)
    return df[cols + [c for c in df.columns if c not in cols]]


# ---------------------------------------------------------------- 03 rank
def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    """Default: top-B by `score`. Adapt id/score/reason columns to your track."""
    budget = int(ctx.param("budget", 20))
    if "score" not in scored.columns:
        raise ValueError("rank(): process() output needs a numeric `score` column (higher = review first), "
                         "or adapt rank() to your track")
    q = make_review_queue(scored, id_col="batch_id", score_col="score",
                          budget=int(ctx.param("review_budget", len(scored))), reason_col="reason", action_col="decision")
    write_review_queue(q, ctx.out("review_queue.csv"))
    return q


# ---------------------------------------------------------------- 04 review
def review(ctx: RunContext, scored: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    cards = queue.merge(scored[["batch_id", "est_error_rate", "ci_low", "ci_high", "n_audited"]],
                        left_on="item_id", right_on="batch_id", validate="one_to_one")
    render_contact_sheet(cards.astype(object).fillna(""), ctx.out("contact_sheet.html"), image_col=None,
                         fields=("item_id", "reason", "suggested_action", "est_error_rate",
                                 "ci_low", "ci_high", "n_audited"), title="T12 - Batch audit review")
    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"
    if not reviewed_path.exists():
        return scored
    reviewed = load_review_decisions(reviewed_path)
    if not reviewed.item_id.is_unique or not reviewed.item_id.isin(queue.item_id).all():
        raise ValueError("T12 review must refer to unique batches in the current queue")

    final = scored.copy().set_index("batch_id", drop=False)
    updates = []
    for row in reviewed.itertuples(index=False):
        before = final.at[row.item_id, "decision"]
        action = row.reviewer_decision
        after = {"reject": "REWORK", "escalate": "HOLD"}.get(action, before)
        if action == "edit":
            after = row.override_value.strip().upper()
            if after not in {"HOLD", "REWORK"}:
                raise ValueError("T12 override must be HOLD or REWORK")
        if after != before and not row.review_note.strip():
            raise ValueError("T12 decision changes need a review note")
        updates.append((row, before, after))
    for row, before, after in updates:
        final.at[row.item_id, "decision"] = after
        if after != before:
            final.at[row.item_id, "reason"] = "REVIEWER_OVERRIDE"
        ctx.audit.log(actor=row.reviewer, action=f"review:{row.reviewer_decision}",
                      item_id=row.item_id, before=before, after=after, reason=row.review_note)
    ctx.stats["n_reviewed"] = len(updates)
    return final


def decide(ctx: RunContext, final: pd.DataFrame) -> Decision:
    count_hold = int(final.decision.eq("HOLD").sum())
    count_rework = int(final.decision.eq("REWORK").sum())
    if ctx.stats.get("label_source") == "none":
        status, code = "HOLD", "NO_AUDIT_LABELS"
        reason = "Danh sách cần kiểm nằm trong audit_sample.csv."
    elif count_hold:
        status, code, reason = "HOLD", "INSUFFICIENT_EVIDENCE", f"{count_hold} batch chưa đủ bằng chứng."
    elif count_rework:
        status, code, reason = "REWORK", "BATCH_REWORK_REQUIRED", f"{count_rework} batch cần làm lại."
    else:
        status, code, reason = "GO", "ALL_BATCHES_ACCEPTED", "Mọi batch đủ bằng chứng để chấp nhận."
    return Decision(status=status, reason_code=code, reason=reason,
                    owner=str(ctx.config.get("owner", "")), scope=ctx.track,
                    evidence=["audit_sample.csv", "batch_decisions.csv", "review_queue.csv"],
                    metrics={"hold_batches": count_hold, "rework_batches": count_rework,
                             "label_source": ctx.stats.get("label_source")})


def run(ctx: RunContext) -> None:
    spec = get_track(ctx.track)
    data = load(ctx)
    scored = process(ctx, data)
    ctx.stats["n_items"] = len(scored)
    queue = rank(ctx, scored)
    final = review(ctx, scored, queue)
    final[spec.pred_columns + [c for c in final.columns if c not in spec.pred_columns]].to_csv(ctx.out(spec.pred_file), index=False)
    write_decision(decide(ctx, final), ctx.out("decision.json"), ctx.out("decision_log.csv"))
