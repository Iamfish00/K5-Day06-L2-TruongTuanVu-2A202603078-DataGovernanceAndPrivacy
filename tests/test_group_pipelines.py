"""Focused tests for the group PoC pipelines (T06 gate, T07 detector, T08 policy)."""
from __future__ import annotations

import json

import numpy as np
import pytest

from src.tracks import t06_gate, t07_redaction, t08_deletion


# --------------------------------------------------------------------- T06
def _coco(images, anns, cats):
    return {"info": {"version": "test"}, "images": images, "annotations": anns, "categories": cats}


def _write_release(tmp_path, parent, candidate):
    (tmp_path / "releases" / "v1.0").mkdir(parents=True)
    (tmp_path / "releases" / "cand").mkdir(parents=True)
    (tmp_path / "releases" / "v1.0" / "annotations.json").write_text(json.dumps(parent))
    (tmp_path / "releases" / "v1.0" / "release_meta.json").write_text(
        json.dumps({"version": "v1.0", "label_map_version": "lm-3"}))
    (tmp_path / "releases" / "cand" / "annotations.json").write_text(json.dumps(candidate))
    (tmp_path / "releases" / "cand" / "release_meta.json").write_text(
        json.dumps({"version": "v1.1", "parent_version": "v1.0", "parent_sha256": "x",
                    "label_map_version": "lm-3"}))
    return tmp_path / "releases" / "cand"


def _img(i, seq="s0", fr=None):
    return {"id": i, "file_name": f"images/f{i}.jpg", "width": 320, "height": 180,
            "sequence_id": seq, "frame_index": fr if fr is not None else i}


def test_t06_clean_release_passes(tmp_path):
    imgs = [_img(i) for i in range(4)]
    anns = [{"id": i, "image_id": i, "category_id": 1, "bbox": [10.0, 10.0, 20.0, 20.0], "area": 400, "iscrowd": 0}
            for i in range(4)]
    cats = [{"id": 1, "name": "car"}, {"id": 2, "name": "pedestrian"}]
    parent = _coco(imgs, anns, cats)
    cand = _coco(imgs, [dict(a, bbox=[10.3, 10.1, 20.0, 20.0]) for a in anns], cats)  # benign jitter
    d = _write_release(tmp_path, parent, cand)
    res = t06_gate.check_release(d, parent, "x", {"lm-3": {"1": "car", "2": "pedestrian"}}, "lm-3")
    assert res["root_cause"] == "clean"


def test_t06_class_swap_detected(tmp_path):
    imgs = [_img(i) for i in range(4)]
    anns = [{"id": i, "image_id": i, "category_id": 1, "bbox": [10.0, 10.0, 20.0, 20.0], "area": 400, "iscrowd": 0}
            for i in range(4)]
    parent = _coco(imgs, anns, [{"id": 1, "name": "car"}, {"id": 2, "name": "pedestrian"}])
    cand = _coco(imgs, anns, [{"id": 1, "name": "pedestrian"}, {"id": 2, "name": "car"}])
    d = _write_release(tmp_path, parent, cand)
    res = t06_gate.check_release(d, parent, "x", {"lm-3": {"1": "car", "2": "pedestrian"}}, "lm-3")
    assert res["root_cause"] == "class_swap"


def test_t06_stale_map_detected(tmp_path):
    imgs = [_img(i) for i in range(4)]
    anns = [{"id": i, "image_id": i, "category_id": 1, "bbox": [10.0, 10.0, 20.0, 20.0], "area": 400, "iscrowd": 0}
            for i in range(4)]
    parent = _coco(imgs, anns, [{"id": 1, "name": "car"}, {"id": 2, "name": "pedestrian"}, {"id": 3, "name": "sign"}])
    cand = _coco(imgs, anns, [{"id": 1, "name": "car"}, {"id": 2, "name": "pedestrian"}])  # lm-2 subset
    d = _write_release(tmp_path, parent, cand)
    maps = {"lm-2": {"1": "car", "2": "pedestrian"}, "lm-3": {"1": "car", "2": "pedestrian", "3": "sign"}}
    res = t06_gate.check_release(d, parent, "x", maps, "lm-3")
    assert res["root_cause"] == "stale_map"


def test_t06_dropped_frames_and_id_collision(tmp_path):
    imgs = [_img(i) for i in range(6)]
    anns = [{"id": i, "image_id": i, "category_id": 1, "bbox": [1.0, 1.0, 5.0, 5.0], "area": 25, "iscrowd": 0}
            for i in range(6)]
    parent = _coco(imgs, anns, [{"id": 1, "name": "car"}])
    cand = _coco([imgs[0], imgs[1], imgs[3], imgs[4], imgs[5]],
                 anns[:4] + [dict(anns[4], id=3)],
                 [{"id": 1, "name": "car"}])  # frame 2 dropped + duplicate ann id 3
    d = _write_release(tmp_path, parent, cand)
    res = t06_gate.check_release(d, parent, "x", {"lm-3": {"1": "car"}}, "lm-3")
    checks = {f["check"] for f in res["findings"]}
    assert {"dropped_frames", "id_collision"} <= checks
    assert res["root_cause"] == "id_collision"


def test_t06_bbox_shift_detected(tmp_path):
    imgs = [_img(i) for i in range(10)]
    anns = [{"id": i, "image_id": i, "category_id": 1, "bbox": [10.0, 10.0, 20.0, 20.0], "area": 400, "iscrowd": 0}
            for i in range(10)]
    parent = _coco(imgs, anns, [{"id": 1, "name": "car"}])
    cand = _coco(imgs, [dict(a, bbox=[30.0, 20.0, 20.0, 20.0]) for a in anns], [{"id": 1, "name": "car"}])
    d = _write_release(tmp_path, parent, cand)
    res = t06_gate.check_release(d, parent, "x", {"lm-3": {"1": "car"}}, "lm-3")
    assert res["root_cause"] == "bbox_shift"


# --------------------------------------------------------------------- T07
def _synth_image():
    img = np.full((360, 640, 3), (95, 120, 80), dtype=np.uint8)   # flat "grass"
    # face: palette skin ellipse with two eyes
    yy, xx = np.mgrid[0:360, 0:640]
    face = ((xx - 100) / 12.0) ** 2 + ((yy - 100) / 15.0) ** 2 <= 1
    img[face] = (224, 172, 105)
    img[96:100, 94:97] = (20, 20, 20)
    img[96:100, 104:107] = (20, 20, 20)
    # plate: bright rectangle with dark text row
    img[200:214, 300:350] = (245, 245, 235)
    img[205:210, 305:345] = (10, 10, 10)
    return img


def test_t07_detects_face_and_plate():
    boxes = t07_redaction.detect(_synth_image())
    faces = [b for b in boxes if b["type"] == "face"]
    plates = [b for b in boxes if b["type"] == "plate"]
    assert len(faces) == 1 and len(plates) == 1
    f, p = faces[0], plates[0]
    assert f["x"] <= 88 and f["x"] + f["w"] >= 112      # covers the drawn ellipse
    assert f["y"] <= 85 and f["y"] + f["h"] >= 115
    assert p["x"] <= 300 and p["x"] + p["w"] >= 350
    assert p["y"] <= 200 and p["y"] + p["h"] >= 214


def test_t07_occluded_face_extended():
    img = _synth_image()
    img[104:130, 86:114] = (70, 60, 90)                 # occluder over lower face
    boxes = [b for b in t07_redaction.detect(img) if b["type"] == "face"]
    assert len(boxes) == 1 and boxes[0]["occluded"]
    assert boxes[0]["y"] + boxes[0]["h"] >= 115          # extended past the occluder top


# --------------------------------------------------------------------- T08
def _art(aid, subj=("s1",), **kw):
    a = {"artefact_id": aid, "type": "frame", "subject_ids": list(subj), "parent_ids": [],
         "uri": f"mock://{aid}", "owner": "data-platform", "acl": ["data-platform"],
         "legal_hold": None, "origin_known": True}
    a.update(kw)
    return a


def test_t08_policy_precedence():
    assert t08_deletion.decide_action(_art("a1", legal_hold="LH-1"), "s1")[0] == "retain_legal_hold"
    assert t08_deletion.decide_action(_art("a2", origin_known=False, legal_hold="LH-1"), "s1")[0] == "retain_legal_hold"
    assert t08_deletion.decide_action(_art("a3", origin_known=False), "s1")[0] == "escalate"
    assert t08_deletion.decide_action(_art("a4", acl=["vendor-x"]), "s1")[0] == "escalate"
    assert t08_deletion.decide_action(_art("a5", subj=("s1", "s2")), "s1")[0] == "escalate"
    assert t08_deletion.decide_action(_art("a6"), "s1")[0] == "delete"


def test_t08_lineage_closure_includes_children():
    arts = [_art("raw", subj=("s1",)), _art("emb", subj=("s1",), parent_ids=["raw"]),
            _art("other", subj=("s2",))]
    closed = t08_deletion.lineage_closure(arts, {"raw"})
    assert closed == {"raw", "emb"}
