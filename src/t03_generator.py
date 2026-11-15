"""Generate reproducible T03 synthetic development data.

This generator uses ONLY originals.csv rows with split == dev.
It never reads the GT directory or modifies holdout fixtures.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from PIL import Image

from .common.corruptions import apply
from .common.image_ops import load_rgb
from .common.io import sha256_file
from .common.contact_sheet import render_contact_sheet


ROOT = Path("fixtures/t03_synthetic")
OUTPUT = Path("out/t03_dev")

TRANSFORMS = ("night", "glare", "fog", "blur")
SEVERITIES = (0, 1, 2)
BASE_SEED = 42
GENERATOR_VERSION = "1.0"


def glare_center(seed: int, width: int, height: int) -> tuple[float, float]:
    """Reproduce the default random glare center and record it explicitly."""
    import numpy as np

    rng = np.random.default_rng(seed)
    return (
        float(rng.uniform(0.2, 0.8) * width),
        float(rng.uniform(0.1, 0.5) * height),
    )


def center_inside_bbox(center: tuple[float, float], boxes: pd.DataFrame) -> bool:
    cx, cy = center
    for box in boxes.itertuples(index=False):
        if box.x <= cx <= box.x + box.w and box.y <= cy <= box.y + box.h:
            return True
    return False


def usability_label(
    transform: str,
    severity: int,
    center: tuple[float, float] | None,
    boxes: pd.DataFrame,
) -> tuple[int, str]:
    if severity == 2:
        return 1, "severity_2_frozen_rule"

    if (
        transform == "glare"
        and severity >= 1
        and center is not None
        and center_inside_bbox(center, boxes)
    ):
        return 1, "glare_center_inside_bbox"

    return 0, "usable_under_frozen_rule"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate T03 synthetic development images."
    )
    parser.add_argument("--seed", type=int, default=BASE_SEED)
    args = parser.parse_args()

    root = ROOT
    output = OUTPUT

    if output.exists():
        raise FileExistsError(
            f"{output} đã tồn tại. Không ghi đè dữ liệu cũ. "
            "Hãy kiểm tra thư mục trước khi quyết định cách xử lý."
        )

    originals_path = root / "originals.csv"
    objects_path = root / "objects.csv"

    originals = pd.read_csv(originals_path)
    objects = pd.read_csv(objects_path)

    required_originals = {"image_id", "path", "split"}
    required_objects = {"image_id", "x", "y", "w", "h"}

    if not required_originals.issubset(originals.columns):
        raise ValueError("originals.csv thiếu cột bắt buộc")

    if not required_objects.issubset(objects.columns):
        raise ValueError("objects.csv thiếu cột bbox")

    dev = originals.loc[originals["split"] == "dev"].copy()

    if len(dev) != 20:
        raise ValueError(f"Yêu cầu đúng 20 ảnh dev, tìm thấy {len(dev)}")

    if dev["image_id"].duplicated().any():
        raise ValueError("image_id trong tập dev bị trùng")

    # Kiểm tra toàn bộ ảnh đầu vào trước khi tạo output.
    for row in dev.itertuples(index=False):
        image_path = root / str(row.path)
        if not image_path.is_file():
            raise FileNotFoundError(image_path)

    output_images = output / "images"
    output_images.mkdir(parents=True, exist_ok=False)

    bbox_groups = {
        str(image_id): group.copy()
        for image_id, group in objects.groupby("image_id")
    }

    manifest_rows = []
    review_rows = []

    for parent_index, row in enumerate(dev.itertuples(index=False)):
        parent_id = str(row.image_id)
        parent_path = root / str(row.path)
        before = load_rgb(parent_path)
        height, width = before.shape[:2]

        boxes = bbox_groups.get(parent_id, pd.DataFrame(
            columns=["x", "y", "w", "h"]
        ))

        parent_sha = sha256_file(parent_path)

        for transform_index, transform in enumerate(TRANSFORMS):
            for severity in SEVERITIES:
                # Stable, unique seed per parent/transform/severity.
                seed = (
                    args.seed
                    + parent_index * 1000
                    + transform_index * 10
                    + severity
                )

                params = {}
                center = None

                if transform == "glare" and severity > 0:
                    center = glare_center(seed, width, height)
                    params = {
                        "center": [center[0], center[1]],
                        "radius_frac": 0.35,
                    }

                after = apply(
                    transform,
                    before,
                    severity=severity,
                    seed=seed,
                    **params,
                )

                image_id = f"{parent_id}_{transform}_s{severity}"
                filename = f"{image_id}.jpg"
                destination = output_images / filename

                Image.fromarray(after).save(
                    destination,
                    format="JPEG",
                    quality=95,
                )

                unusable, reason = usability_label(
                    transform, severity, center, boxes
                )

                manifest_rows.append({
                    "image_id": image_id,
                    "path": str(destination),
                    "parent_image": parent_id,
                    "transform": transform,
                    "severity": severity,
                    "seed": seed,
                    "params": json.dumps(params, sort_keys=True),
                    "generator": "src.t03_generator",
                    "generator_version": GENERATOR_VERSION,
                    "source_type": "synthetic_simulation_not_real_weather",
                    "parent_sha256": parent_sha,
                    "generated_sha256": sha256_file(destination),
                    "unusable": unusable,
                    "usability_reason": reason,
                })

                review_rows.append({
                    "item_id": image_id,
                    "before_path": str(parent_path.resolve()),
                    "evidence_path": str(destination.resolve()),
                    "transform": transform,
                    "severity": severity,
                    "seed": seed,
                    "unusable": unusable,
                    "usability_reason": reason,
                })

    manifest = pd.DataFrame(manifest_rows)
    manifest_path = output / "transform_manifest.csv"
    manifest.to_csv(manifest_path, index=False)

    review = pd.DataFrame(review_rows)
    html_path = output / "dev_contact_sheet.html"

    render_contact_sheet(
        review,
        html_path,
        image_col="evidence_path",
        before_image_col="before_path",
        fields=(
            "item_id",
            "transform",
            "severity",
            "seed",
            "unusable",
            "usability_reason",
        ),
        title="T03 Dev — Synthetic Before/After Review",
        thumb_width=240,
        embed=True,
    )

    counts = (
        manifest.groupby(["transform", "severity"])["image_id"]
        .count()
        .rename("images")
        .reset_index()
    )

    report = [
        "# T03 Synthetic Dev Generation Report",
        "",
        "- Dataset: synthetic simulation only; NOT real weather data.",
        f"- Base seed: {args.seed}",
        f"- Original dev images: {len(dev)}",
        f"- Generated images: {len(manifest)}",
        "- Transforms: " + ", ".join(TRANSFORMS),
        "- Severities: 0, 1, 2",
        "",
        "## Counts by transform and severity",
        "",
        "    " + counts.to_string(index=False).replace("\n", "\n    "),
        "",
        "## Usability labels",
        "",
        manifest["unusable"].value_counts().sort_index().to_string(),
        "",
        "These labels follow the frozen usability rule for generated dev data.",
        "They are not official holdout ground truth or evaluation metrics.",
        "",
        "## Provenance",
        "",
        f"- Manifest: `{manifest_path}`",
        f"- Before/after review: `{html_path}`",
        "- Each row records parent, transform, severity, seed, parameters,",
        "  parent SHA-256 and generated-image SHA-256.",
    ]

    (output / "generation_report.md").write_text(
        "\n".join(report) + "\n",
        encoding="utf-8",
    )

    print(f"Generated images: {len(manifest)}")
    print(f"Manifest: {manifest_path}")
    print(f"Contact sheet: {html_path}")
    print(f"Report: {output / 'generation_report.md'}")
    print("\nCounts by transform/severity:")
    print(counts.to_string(index=False))
    print("\nUsability label counts:")
    print(manifest["unusable"].value_counts().sort_index())


if __name__ == "__main__":
    main()
