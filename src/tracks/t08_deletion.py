"""T08 — Retention & Delete Request Simulator (PoC, mock data only).

For every delete request: scope = artefacts whose `subject_ids` contain the
requesting subject, closed under lineage (children via `parent_ids`, so backups,
frames, crops, embeddings, exports derived from a matching artefact are included
even if their own subject list were empty).

Policy precedence (frozen BEFORE any eval result was seen; docs/tracks/T08.md §8):
  1. legal_hold set                -> retain_legal_hold  (GDPR Art.17(3), L91/2025 exceptions)
  2. origin_unknown / no permission-> escalate           (cannot prove provenance or ACL lacks our owner)
  3. multi-subject artefact        -> escalate           (deletion would hit other subjects)
  4. otherwise                     -> delete

Every action row is written to the AuditLog (out/audit_log.jsonl) so the
audit-trace-completeness metric sees one entry per acted artefact. Deletion is
simulated: nothing real is touched, evidence = out/deletion_evidence.csv.
"""
from __future__ import annotations

import json
from collections import deque

import pandas as pd

from ..common.context import RunContext
from ..common.decision import Decision, write_decision
from ..common.manifest import verify_manifest
from ..common.review import load_review_decisions, make_review_queue, write_review_queue

PROCESSING_OWNER = "data-platform"          # who runs the deletion job
ESCALATE_REASONS = {"origin_unknown": "provenance of artefact unknown — legal review required",
                    "no_permission": "artefact ACL does not include the processing owner",
                    "multi_subject": "artefact contains other subjects — partial deletion needs review"}


# ---------------------------------------------------------------- helpers
def lineage_closure(artefacts: list[dict], seed_ids: set[str]) -> set[str]:
    """BFS from the directly-matching artefacts down through derived children."""
    children: dict[str, list[str]] = {}
    for a in artefacts:
        for p in a.get("parent_ids") or []:
            children.setdefault(p, []).append(a["artefact_id"])
    seen = set(seed_ids)
    dq = deque(seen)
    while dq:
        for c in children.get(dq.popleft(), []):
            if c not in seen:
                seen.add(c)
                dq.append(c)
    return seen


def decide_action(a: dict, request_subject: str) -> tuple[str, str]:
    if a.get("legal_hold"):
        return "retain_legal_hold", f"legal hold {a['legal_hold']} in force — schedule deletion after hold lifts"
    if not a.get("origin_known", True):
        return "escalate", ESCALATE_REASONS["origin_unknown"]
    if PROCESSING_OWNER not in (a.get("acl") or []):
        return "escalate", ESCALATE_REASONS["no_permission"]
    others = [s for s in (a.get("subject_ids") or []) if s != request_subject]
    if others:
        return "escalate", f"{ESCALATE_REASONS['multi_subject']} (also: {', '.join(others[:3])})"
    return "delete", "subject deletion request, no exception applies"


# ---------------------------------------------------------------- 01 load
def load(ctx: RunContext) -> dict:
    manifest = ctx.input_dir / "manifest.csv"
    problems = verify_manifest(manifest) if manifest.exists() else [{"problem": "no_manifest"}]
    if problems:
        ctx.audit.log(actor="pipeline", action="provenance_problems", item_id=str(manifest), after=problems[:20])
        if ctx.config.get("strict_provenance", True):
            raise ValueError(f"provenance check failed ({len(problems)} problems), first: {problems[:3]}")
    graph = json.loads((ctx.input_dir / "data_graph.json").read_text(encoding="utf-8"))
    requests = json.loads((ctx.input_dir / "requests.json").read_text(encoding="utf-8"))
    return {"artefacts": graph["artefacts"], "requests": requests}


# ---------------------------------------------------------------- 02 process
def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    by_id = {a["artefact_id"]: a for a in data["artefacts"]}
    rows = []
    for req in data["requests"]:
        subject = req["subject_id"]
        seeds = {a["artefact_id"] for a in data["artefacts"] if subject in (a.get("subject_ids") or [])}
        scope = lineage_closure(data["artefacts"], seeds)
        for aid in sorted(scope):
            a = by_id[aid]
            action, reason = decide_action(a, subject)
            rows.append({"request_id": req["request_id"], "artefact_id": aid, "action": action,
                         "type": a["type"], "reason": reason,
                         "score": {"retain_legal_hold": 3.0, "escalate": 2.0, "delete": 1.0}[action],
                         "deadline": req.get("deadline", ""),
                         "dry_run": bool(ctx.param("dry_run", True))})
    df = pd.DataFrame(rows)
    ctx.stats["n_requests"] = len(data["requests"])
    ctx.stats["n_actions"] = len(df)
    return df


# ---------------------------------------------------------------- 03 rank
def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    budget = int(ctx.param("budget", 20))
    scored = scored.assign(item_id=scored.request_id + "/" + scored.artefact_id)
    q = make_review_queue(scored, id_col="item_id", score_col="score", budget=budget,
                          reason_col="reason", action_col="action", slice_col="type")
    write_review_queue(q, ctx.out("review_queue.csv"))
    return q


# ---------------------------------------------------------------- 04 review
def review(ctx: RunContext, scored: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"
    if not reviewed_path.exists():
        return scored
    reviewed = load_review_decisions(reviewed_path, allowed={"approve", "reject", "edit"})
    ctx.stats["n_reviewed"] = len(reviewed)
    out = scored.assign(item_id=scored.request_id + "/" + scored.artefact_id)
    for r in reviewed.itertuples(index=False):
        mask = out.item_id == r.item_id
        if not mask.any():
            continue
        before = out.loc[mask, "action"].iloc[0]
        if r.reviewer_decision == "edit" and str(r.override_value).strip():
            after = str(r.override_value).strip().lower()
        elif r.reviewer_decision == "reject":
            after = "escalate"          # reviewer refuses the simulated delete -> human escalation
        else:
            continue
        if after not in {"delete", "retain_legal_hold", "escalate"}:
            raise ValueError(f"invalid override action {after!r}")
        out.loc[mask, "action"] = after
        ctx.audit.log(actor=r.reviewer, action=f"override:{r.reviewer_decision}", item_id=r.item_id,
                      before=before, after=after, reason=r.review_note)
    return out.drop(columns="item_id")


# ---------------------------------------------------------------- 05 decide
def decide(ctx: RunContext, final: pd.DataFrame) -> Decision:
    n_del = int((final.action == "delete").sum())
    n_hold = int((final.action == "retain_legal_hold").sum())
    n_esc = int((final.action == "escalate").sum())
    dry = bool(ctx.param("dry_run", True))
    return Decision(
        status="GO" if dry else "HOLD",
        reason_code="DRY_RUN_SIMULATED" if dry else "LIVE_DELETE_REQUIRES_SIGNOFF",
        reason=(f"simulated deletion of {n_del} artefacts across {final.request_id.nunique()} requests; "
                f"{n_hold} retained under legal hold, {n_esc} escalated for human decision"),
        owner=ctx.config.get("owner", "TODO-owner"),
        scope=ctx.track,
        evidence=[str(ctx.out("actions.csv")), str(ctx.out("audit_log.jsonl")),
                  str(ctx.out("deletion_evidence.csv"))],
        metrics={"delete": n_del, "retain_legal_hold": n_hold, "escalate": n_esc},
    )


def run(ctx: RunContext) -> None:
    data = load(ctx)                                                       # 01
    scored = process(ctx, data)                                            # 02
    ctx.stats["n_items"] = len(scored)
    queue = rank(ctx, scored)                                              # 03
    final = review(ctx, scored, queue)                                     # 04
    final[["request_id", "artefact_id", "action", "type", "reason"]].to_csv(ctx.out("actions.csv"), index=False)
    # simulated deletion evidence + one audit entry per acted artefact
    ev = final[final.action == "delete"][["request_id", "artefact_id", "type", "reason"]]
    ev.assign(simulated=True, deleted_at=pd.NaT).to_csv(ctx.out("deletion_evidence.csv"), index=False)
    for r in final.itertuples(index=False):
        ctx.audit.log(actor="deletion-service", action=r.action, item_id=r.artefact_id,
                      before="present", after="deleted(mock)" if r.action == "delete" else r.action,
                      reason=r.reason, request_id=r.request_id, artefact_type=r.type)
    write_decision(decide(ctx, final), ctx.out("decision.json"), ctx.out("decision_log.csv"))  # 05
