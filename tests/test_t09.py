"""T09 contract, review and safe policy expression checks without instructor GT."""
import json

import pandas as pd
import pytest

from src import main as runner
from src.t09 import matches


def test_policy_expression_does_not_execute_code():
    case = {"transfer": {"cross_border": True, "mechanism": None}, "retention_days": None}
    assert matches(case, "transfer.cross_border and transfer.mechanism is null")
    assert not matches(case, "retention_days > 365")
    with pytest.raises(ValueError, match="unsupported"):
        matches(case, "__import__('os').system('echo unsafe')")


def test_t09_end_to_end_and_review_override(tmp_path):
    out = tmp_path / "poc"
    args = ["--track", "t09", "--output", str(out), "--offline", "--seed", "42"]
    assert runner.main(args) == 0
    pred = pd.read_csv(out / "decisions.csv")
    assert len(pred) == 12 and pred.case_id.is_unique
    assert set(pred.decision).issubset({"GO", "HOLD", "REWORK"})
    assert pred.loc[pred.case_id == "CASE-02", "decision"].item() == "HOLD"
    assert pred.loc[pred.case_id == "CASE-10", "decision"].item() == "GO"
    assert (out / "review_cases.html").exists()
    assert "market=EU" in (out / "review_cases.html").read_text(encoding="utf-8")
    assert len(pd.read_csv(out / "audit_checklist.csv")) == 12
    assert json.loads((out / "decision.json").read_text(encoding="utf-8"))["status"] == "HOLD"

    reviewed = pd.read_csv(out / "review_queue.csv", dtype=str).fillna("")
    reviewed.loc[reviewed.item_id == "CASE-02", ["reviewer", "reviewer_decision", "override_value", "review_note"]] = [
        "Human QA", "edit", "REWORK", "Collect transfer evidence before release"
    ]
    reviewed.to_csv(out / "review_queue_reviewed.csv", index=False)
    assert runner.main(args) == 0
    changed = pd.read_csv(out / "decisions.csv")
    assert changed.loc[changed.case_id == "CASE-02", "decision"].item() == "REWORK"
    assert "HUMAN_OVERRIDE" in changed.loc[changed.case_id == "CASE-02", "reason_codes"].item()
    audit = [json.loads(line) for line in (out / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event["action"] == "review:edit" and event["item_id"] == "CASE-02" for event in audit)


def test_t09_rejects_unexplained_override(tmp_path):
    out = tmp_path / "poc"
    args = ["--track", "t09", "--output", str(out), "--offline"]
    assert runner.main(args) == 0
    reviewed = pd.read_csv(out / "review_queue.csv", dtype=str).fillna("")
    reviewed.loc[0, ["reviewer", "reviewer_decision", "override_value", "review_note"]] = ["Human QA", "edit", "GO", ""]
    reviewed.to_csv(out / "review_queue_reviewed.csv", index=False)
    assert runner.main(args) == 2


def test_t09_baselines(tmp_path):
    for module, expected_go in [("src.examples.t09_baseline", 4), ("src.examples.t09_all_hold_baseline", 0)]:
        out = tmp_path / module.rsplit(".", 1)[-1]
        assert runner.main(["--track", "t09", "--pipeline", module, "--output", str(out), "--offline", "--seed", "42"]) == 0
        pred = pd.read_csv(out / "decisions.csv")
        assert len(pred) == 12 and int((pred.decision == "GO").sum()) == expected_go
