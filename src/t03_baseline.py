from pathlib import Path

import pandas as pd

from .common.image_ops import brightness_stats, laplacian_variance, load_rgb


INPUT = Path("out/t03_dev/images")
MANIFEST = Path("out/t03_dev/transform_manifest.csv")
OUTPUT = Path("out/t03_dev/baseline")


def main():
    meta = pd.read_csv(MANIFEST)
    rows = []

    for row in meta.itertuples(index=False):
        image = load_rgb(INPUT / f"{row.image_id}.jpg")
        bright = brightness_stats(image)
        sharpness = laplacian_variance(image)

        pred = int(bright["mean"] < 35 or sharpness < 80)

        rows.append({
            "image_id": row.image_id,
            "pred_unusable": pred,
        })

    OUTPUT.mkdir(parents=True, exist_ok=False)
    pd.DataFrame(rows).to_csv(OUTPUT / "qc.csv", index=False)
    print(f"Baseline rows: {len(rows)}")
    print(f"Saved: {OUTPUT / 'qc.csv'}")


if __name__ == "__main__":
    main()
