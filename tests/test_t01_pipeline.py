import json

import pandas as pd

from src.main import main


def run_t01(output):
    return main(["--track", "t01", "--output", str(output), "--seed", "42", "--offline"])


def test_t01_uses_budget_and_surfaces_gaps(tmp_path):
    assert run_t01(tmp_path) == 0

    picks = pd.read_csv(tmp_path / "pick.csv")
    coverage = pd.read_csv(tmp_path / "coverage.csv")
    plan = pd.read_csv(tmp_path / "capture_plan.csv")
    queue = pd.read_csv(tmp_path / "review_queue.csv")
    decision = json.loads((tmp_path / "decision.json").read_text(encoding="utf-8"))

    assert len(picks) == 30 and picks.clip_id.is_unique
    assert set(coverage.loc[~coverage.covered, "cell_id"]) == set(plan.cell_id)
    assert set(plan.cell_id).issubset(queue.item_id)
    assert len(queue) == 3 and decision["reason_code"] == "REVIEW_PENDING"
    assert (tmp_path / "contact_sheet.html").is_file()


def test_t01_rejects_edit_without_capture_override(tmp_path, capsys):
    assert run_t01(tmp_path) == 0
    queue = pd.read_csv(tmp_path / "review_queue.csv", dtype=str).fillna("")
    queue.loc[0, ["reviewer", "reviewer_decision"]] = ["TEST_FIXTURE", "edit"]
    queue.to_csv(tmp_path / "review_queue_reviewed.csv", index=False)

    assert run_t01(tmp_path) == 2
    assert "Each edit needs a capture override" in capsys.readouterr().err
    assert not (tmp_path / "audit_log.jsonl").exists()
