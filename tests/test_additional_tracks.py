"""Offline pipeline contracts and review regressions for the additional tracks."""

import json
import numpy as np
import pandas as pd
import pytest

from src.main import main
from src.metrics.tracks import get_track
from src.tracks.t04_sync import fit_clock


def run_track(track, out):
    return main(["--track", track, "--output", str(out), "--offline", "--seed", "42"])


@pytest.mark.parametrize("track", ("t04", "t05", "t10", "t11", "t13", "t14"))
def test_track_output_and_shared_review(track, tmp_path):
    assert run_track(track, tmp_path) == 0
    spec = get_track(track)
    data = pd.read_csv(tmp_path / spec.pred_file)
    assert not data.empty and set(spec.pred_columns) <= set(data.columns)
    queue = pd.read_csv(tmp_path / "review_queue.csv")
    assert not queue.empty and queue.item_id.is_unique
    assert (tmp_path / "contact_sheet.html").is_file()
    info = json.loads((tmp_path / "run_info.json").read_text(encoding="utf-8"))
    assert info["status"] == 0 and info["stats"]["n_reviewed"] == 0
    if track == "t05":
        assert not queue.reason.eq("exact_sha256").all()
        split = pd.read_csv(tmp_path / "split_manifest.csv").set_index("image_id").split
        assert {"train","test"} == set(split)
        positive = data[data.pred_dup.eq(1)]
        assert positive.img_a.map(split).tolist() == positive.img_b.map(split).tolist()
    if track == "t13":
        assert len(data) == 20 and data.image_id.is_unique


def test_t04_recovers_known_synthetic_offset():
    imu = np.arange(0, 20000, 10, dtype=float)
    bumps = sum(np.exp(-((imu-center)/52)**2) for center in (2180,3570,5290,7790,12910,17060))
    actual = np.arange(150,19850,100/3)
    camera = actual + 85
    motion = np.interp(actual, imu, bumps)
    corrected = fit_clock(camera, motion, imu, bumps + 9.8, np.zeros(len(camera)))
    assert np.median(abs(corrected - actual)) <= 5


def reviewer_edit(path, row, value):
    review = pd.read_csv(path / "review_queue.csv", dtype=str).fillna("")
    review = review.loc[[row]].copy()
    review.loc[:,["reviewer","reviewer_decision","override_value","review_note"]] = [
        "TEST_FIXTURE", "edit", json.dumps(value), "Synthetic verification of reviewer editing",
    ]
    review.to_csv(path/"review_queue_reviewed.csv",index=False)
    return review.item_id.iloc[0]


def test_t11_review_applies_bbox_correction_with_diff(tmp_path):
    track = "t11"
    assert run_track(track,tmp_path) == 0
    before = pd.read_csv(tmp_path/"corrected_annotations.csv").set_index("ann_id")
    queue = pd.read_csv(tmp_path/"review_queue.csv")
    box_index = queue.index[~queue.item_id.str.startswith("img:")][0]
    key = reviewer_edit(tmp_path, box_index, {"class":"car","x":5,"y":6,"w":15,"h":20})

    assert run_track(track,tmp_path) == 0
    after = pd.read_csv(tmp_path/"corrected_annotations.csv").set_index("ann_id")
    assert before.loc[key,"x"] != after.loc[key,"x"] == 5
    changes = pd.read_csv(tmp_path/"label_changes.csv")
    assert changes.item_id.tolist() == [key]
    audit = [json.loads(row) for row in (tmp_path/"audit_log.jsonl").read_text().splitlines()]
    assert audit[-1]["item_id"] == key


def test_t14_review_exports_consistent_yolo_and_coco(tmp_path):
    track = "t14"
    assert run_track(track,tmp_path) == 0
    key = reviewer_edit(tmp_path,0,{"class":"motorbike","x":5,"y":6,"w":15,"h":20})
    assert run_track(track,tmp_path) == 0
    changes = pd.read_csv(tmp_path/"label_changes.csv")
    assert changes.item_id.tolist() == [key]
    pred = pd.read_csv(tmp_path/"predictions.csv")
    coco = json.loads((tmp_path/"annotations.json").read_text(encoding="utf-8"))
    assert len(coco["annotations"]) == len(pred)
    ids = {x["id"]: x["name"] for x in coco["categories"]}
    assert {ids[x["category_id"]] for x in coco["annotations"]} <= {"car","pedestrian","motorbike","sign"}
    assert any(x["bbox"] == [5,6,15,20] and ids[x["category_id"]] == "motorbike" for x in coco["annotations"])
    yolo = {p.stem: p.read_text().splitlines() for p in (tmp_path/"labels").glob("*.txt")}
    assert len(yolo) == len(coco["images"]) == 60 and sum(map(len,yolo.values())) == len(pred)
    for line in (line for lines in yolo.values() for line in lines):
        label, *box = line.split()
        assert 0 <= int(label) < 4 and len(box) == 4 and all(0 <= float(n) <= 1 for n in box)


def test_t10_clears_batch_gate_only_after_review(tmp_path):
    assert run_track("t10", tmp_path) == 0
    decision = tmp_path / "decision.json"
    assert json.loads(decision.read_text(encoding="utf-8"))["status"] == "HOLD"

    queue = pd.read_csv(tmp_path / "review_queue.csv", dtype=str).fillna("")
    queue[["reviewer", "reviewer_decision", "override_value", "review_note"]] = [
        "TEST_FIXTURE", "edit", "KEEP", "Synthetic verification of completed review",
    ]
    queue.to_csv(tmp_path / "review_queue_reviewed.csv", index=False)
    assert run_track("t10", tmp_path) == 0
    assert json.loads(decision.read_text(encoding="utf-8"))["status"] == "KEEP"
