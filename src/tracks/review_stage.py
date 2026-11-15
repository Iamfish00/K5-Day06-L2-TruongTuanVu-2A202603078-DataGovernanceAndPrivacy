"""Shared review and decision output for the Day 06 track modules."""

from math import isclose

from ..common.contact_sheet import render_contact_sheet
from ..common.decision import Decision, write_decision
from ..common.review import load_review_decisions, make_review_queue, write_review_queue


def make_queue(ctx, items, budget, title, *, image_col=None, before_col=None, boxes=None, fields=()):
    queue = make_review_queue(
        items, "item_id", "score", budget, reason_col="reason",
        action_col="suggested_action" if "suggested_action" in items else None,
        evidence_col=image_col,
    )
    write_review_queue(queue, ctx.out("review_queue.csv"))
    extras = ([before_col] if before_col else []) + list(fields)
    cards = queue.join(items.set_index("item_id")[extras], on="item_id") if extras else queue
    render_contact_sheet(cards, ctx.out("contact_sheet.html"),
                         image_col="evidence_path" if image_col else None,
                         before_image_col=before_col, image_root=ctx.input_dir, boxes_by_item=boxes,
                         fields=("rank", "item_id", "score", "reason", "suggested_action", *fields), title=title)
    path = ctx.output_dir / "review_queue_reviewed.csv"
    if not path.exists():
        return queue, []
    reviewed = load_review_decisions(path)
    current = queue.set_index("item_id")
    if not reviewed.item_id.is_unique or not reviewed.item_id.isin(current.index).all():
        raise ValueError("Review IDs must be unique and present in the current queue")
    rows = list(reviewed.itertuples(index=False))
    for row in rows:
        expected = current.loc[row.item_id]
        if (row.reason != expected.reason or row.suggested_action != expected.suggested_action
                or not isclose(float(row.score), expected.score, rel_tol=0, abs_tol=1e-6)):
            raise ValueError(f"Review for {row.item_id} does not match the current queue")
        if row.reviewer_decision in {"edit", "reject", "escalate"} and not row.review_note.strip():
            raise ValueError(f"{row.item_id}: a review note is required")
    return queue, rows


def finalize(ctx, items, reviewed, events, evidence, *, hold_reason=None, keep=False, baseline=False):
    ctx.stats.update(n_items=items, n_reviewed=len(reviewed))
    for row, before, after in events:
        ctx.audit.log(actor=row.reviewer, action=f"review:{row.reviewer_decision}",
                      item_id=row.item_id, before=before, after=after, reason=row.review_note)
    if baseline:
        status, code, reason = "HOLD", "BASELINE_ONLY", "Baseline comparison output."
    elif hold_reason:
        status, code = "HOLD", "UNRESOLVED_QUALITY_FLAGS"
        reason = hold_reason
    elif keep:
        status, code, reason = "KEEP", "REVIEWED_NO_BLOCKS", "Reviewed batches have no blocking decisions."
    else:
        status, code, reason = "HOLD", "HUMAN_SIGNOFF_REQUIRED", "Outputs are ready for review and final signoff."
    write_decision(Decision(status, code, reason, str(ctx.config.get("owner") or ""), scope=ctx.track,
                            evidence=evidence, metrics={"n_items": items, "n_reviewed": len(reviewed)}),
                   ctx.out("decision.json"), ctx.out("decision_log.csv"))
