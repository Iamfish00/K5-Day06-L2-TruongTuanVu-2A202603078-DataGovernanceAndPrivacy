"""T09 offline policy triage. Automated decisions are provisional until human review."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pandas as pd
import yaml

from .common.contact_sheet import render_contact_sheet
from .common.context import RunContext
from .common.decision import Decision, canonical_status, write_decision
from .common.manifest import verify_manifest
from .common.review import load_review_decisions, make_review_queue, write_review_queue

PRIORITY = {"GO": 0, "REWORK": 1, "HOLD": 2}


def _value(case: dict, node: ast.AST):
    """Small expression grammar for policy conditions; calls and indexing are forbidden."""
    if isinstance(node, ast.Name):
        if node.id == "null":
            return None
        if node.id not in case:
            raise ValueError(f"policy field missing: {node.id}")
        return case[node.id]
    if isinstance(node, ast.Attribute):
        parent = _value(case, node.value)
        if not isinstance(parent, dict) or node.attr not in parent:
            raise ValueError(f"policy field missing: {ast.unparse(node)}")
        return parent[node.attr]
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_value(case, item) for item in node.elts]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not bool(_value(case, node.operand))
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        values = (bool(_value(case, item)) for item in node.values)
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _value(case, node.left)
        for op, item in zip(node.ops, node.comparators):
            right = _value(case, item)
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.Is):
                ok = left is right
            elif isinstance(op, ast.IsNot):
                ok = left is not right
            elif isinstance(op, ast.In):
                ok = left in right
            elif isinstance(op, ast.NotIn):
                ok = left not in right
            elif isinstance(op, ast.Gt):
                ok = left is not None and right is not None and left > right
            else:
                raise ValueError(f"unsupported policy operator: {type(op).__name__}")
            if not ok:
                return False
            left = right
        return True
    raise ValueError(f"unsupported policy expression: {ast.dump(node)}")


def matches(case: dict, expression: str) -> bool:
    return bool(_value(case, ast.parse(expression, mode="eval").body))


def load(ctx: RunContext) -> tuple[list[dict], dict]:
    problems = verify_manifest(ctx.input_dir / "manifest.csv")
    if problems:
        raise ValueError(f"manifest verification failed: {problems[:3]}")
    cases = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((ctx.input_dir / "cases").glob("CASE-*.json"))]
    ids = [case["case_id"] for case in cases]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("case input is empty or has duplicate case_id")
    policy_path = Path(ctx.param("policy_file", "configs/policies/t09.yaml"))
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict) or not policy.get("policy_version") or not policy.get("effective_date"):
        raise ValueError("policy needs policy_version and effective_date")
    rules = policy.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("policy has no rules")
    rule_ids = []
    for rule in rules:
        if not all(rule.get(key) for key in ("id", "markets", "when", "decision", "reason_code", "source")):
            raise ValueError(f"incomplete rule: {rule}")
        if not rule["source"].get("document") or not rule["source"].get("provision"):
            raise ValueError(f"rule {rule['id']} needs source document and provision")
        canonical_status(rule["decision"])
        ast.parse(rule["when"], mode="eval")
        rule_ids.append(rule["id"])
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("duplicate policy rule ID")
    ctx.audit.log(actor="pipeline", action="policy_loaded", after={"version": policy["policy_version"], "cases": len(cases)})
    return cases, policy


def process(cases: list[dict], policy: dict, mode: str = "poc") -> pd.DataFrame:
    rows = []
    for case in cases:
        market = case.get("market")
        if mode == "consent_baseline":
            decision = "GO" if case.get("consent", {}).get("obtained") else "HOLD"
            hits = [{"id": "B-CONSENT", "decision": decision, "reason_code": "CONSENT_ONLY_BASELINE",
                     "source": {"document": "Lab baseline", "provision": "Consent flag only"}}]
        elif mode == "all_hold_baseline":
            decision = "HOLD"
            hits = [{"id": "B-ALL-HOLD", "decision": decision, "reason_code": "ALL_HOLD_BASELINE",
                     "source": {"document": "Lab baseline", "provision": "Trivial guardrail check"}}]
        elif mode == "poc":
            hits = [rule for rule in policy["rules"]
                    if ("*" in rule["markets"] or market in rule["markets"]) and matches(case, rule["when"])]
            if case.get("ambiguity_notes"):
                hits.append({"id": "AMBIGUITY", "decision": "HOLD", "reason_code": "AMBIGUOUS_NEEDS_LEGAL",
                             "source": {"document": "Case input", "provision": "ambiguity_notes"}})
            if market not in {"VN", "EU", "US-CA", "SG", "IN"}:
                hits.append({"id": "UNKNOWN-MARKET", "decision": "HOLD", "reason_code": "UNSUPPORTED_MARKET",
                             "source": {"document": "Lab scope", "provision": "Market not covered"}})
            decision = max((canonical_status(rule["decision"]) for rule in hits), key=PRIORITY.get, default="GO")
        else:
            raise ValueError(f"unknown T09 mode: {mode}")
        codes = ";".join(dict.fromkeys(rule["reason_code"] for rule in hits)) or "NO_FLAG_FOUND"
        sources = ";".join(dict.fromkeys(str(rule["source"].get("url", rule["source"]["document"])) for rule in hits))
        rule_ids = ";".join(rule["id"] for rule in hits)
        summary = (
            f"market={market}; data={','.join(case.get('data_types', []))}; "
            f"basis={case.get('lawful_basis')}; consent={case.get('consent', {}).get('obtained')}; "
            f"notice={case.get('notice_given')}; transfer={case.get('transfer', {}).get('cross_border')}; "
            f"retention_days={case.get('retention_days')}; ambiguity={case.get('ambiguity_notes') or 'none'}"
        )
        rows.append({"case_id": case["case_id"], "decision": decision, "reason_codes": codes,
                     "score": 100 * PRIORITY[decision] + 10 * len(hits) + int(bool(case.get("ambiguity_notes"))),
                     "market": market, "rule_ids": rule_ids,
                     "rule_sources": sources, "evidence_path": f"cases/{case['case_id']}.json",
                     "case_summary": summary,
                     "review_reason": f"{codes} | rules: {rule_ids or 'none'} | sources: {sources or 'none'}",
                     "policy_version": policy["policy_version"] if mode == "poc" else mode,
                     "review_status": "PENDING"})
    return pd.DataFrame(rows)


def review(ctx: RunContext, scored: pd.DataFrame, *, mode: str) -> pd.DataFrame:
    queue = make_review_queue(scored, "case_id", "score", budget=len(scored), reason_col="review_reason",
                              action_col="decision", slice_col="market")
    write_review_queue(queue, ctx.out("review_queue.csv"))
    display = queue.merge(scored[["case_id", "case_summary", "rule_sources", "evidence_path"]].rename(columns={"evidence_path": "case_file"}),
                          left_on="item_id", right_on="case_id", how="left")
    render_contact_sheet(display, ctx.out("review_cases.html"), image_col=None,
                         fields=("rank", "item_id", "score", "case_summary", "suggested_action", "reason", "case_file"),
                         title="T09 privacy case review (educational)")
    final = scored.copy()
    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"
    if mode != "poc" or not reviewed_path.exists():
        return final
    reviewed = load_review_decisions(reviewed_path)
    if reviewed.item_id.duplicated().any() or not set(reviewed.item_id).issubset(set(final.case_id)):
        raise ValueError("review file contains duplicate or unknown case_id")
    current = queue.set_index("item_id")
    for row in reviewed.itertuples(index=False):
        if row.suggested_action != current.loc[row.item_id, "suggested_action"] or row.reason != current.loc[row.item_id, "reason"]:
            raise ValueError(f"{row.item_id}: reviewed queue is stale; review current policy output")
        mask = final.case_id == row.item_id
        before = final.loc[mask, "decision"].item()
        if row.reviewer_decision in {"edit", "reject"}:
            if not row.override_value or not row.review_note:
                raise ValueError(f"{row.item_id}: edit/reject needs a GO/HOLD/REWORK override and note")
            after = canonical_status(row.override_value)
            final.loc[mask, "decision"] = after
            final.loc[mask, "reason_codes"] = final.loc[mask, "reason_codes"].item() + ";HUMAN_OVERRIDE"
            final.loc[mask, "review_status"] = "OVERRIDDEN"
        elif row.reviewer_decision == "escalate":
            after = "HOLD"
            final.loc[mask, "decision"] = after
            final.loc[mask, "reason_codes"] = final.loc[mask, "reason_codes"].item() + ";ESCALATED_TO_LEGAL"
            final.loc[mask, "review_status"] = "ESCALATED"
        elif row.reviewer_decision == "approve":
            after = before
            final.loc[mask, "review_status"] = "APPROVED"
        else:
            after = before
        ctx.audit.log(actor=row.reviewer, action=f"review:{row.reviewer_decision}", item_id=row.item_id,
                      before=before, after=after, reason=row.review_note)
    ctx.stats["n_reviewed"] = len(reviewed)
    return final


def run(ctx: RunContext, mode: str = "poc") -> None:
    if ctx.track != "t09":
        raise ValueError("T09 pipeline requires --track t09")
    cases, policy = load(ctx)
    scored = process(cases, policy, mode=mode)
    ctx.stats["n_items"] = len(scored)
    final = review(ctx, scored, mode=mode)
    final.to_csv(ctx.out("decisions.csv"), index=False)
    final[["case_id", "market", "case_summary", "decision", "reason_codes", "rule_ids", "rule_sources",
           "policy_version", "review_status", "evidence_path"]].to_csv(ctx.out("audit_checklist.csv"), index=False)
    pending_go = int(((final.decision == "GO") & (final.review_status != "APPROVED")).sum())
    hold = int((final.decision == "HOLD").sum())
    rework = int((final.decision == "REWORK").sum())
    status = "HOLD" if hold or pending_go else "REWORK" if rework else "GO"
    code = "CASE_HOLD_OR_APPROVAL_PENDING" if status == "HOLD" else "CASE_REWORK" if status == "REWORK" else "ALL_CASES_APPROVED"
    write_decision(Decision(status=status, reason_code=code,
                            reason=f"{hold} HOLD; {rework} REWORK; {pending_go} GO cases pending human approval",
                            owner=str(ctx.config.get("owner", "")), scope="t09",
                            evidence=[str(ctx.out("decisions.csv")), str(ctx.out("review_queue.csv"))],
                            metrics={"n_cases": len(final), "n_reviewed": ctx.stats.get("n_reviewed", 0)}),
                   ctx.out("decision.json"), ctx.out("decision_log.csv"))
