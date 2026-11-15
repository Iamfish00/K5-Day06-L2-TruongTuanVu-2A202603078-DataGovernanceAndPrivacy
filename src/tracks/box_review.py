"""Read and validate reviewer edits to pixel-space boxes."""

import json
import math

BOX = ["x", "y", "w", "h"]


def overlay(box, color="#ffbf29"):
    return {key: float(box[key]) for key in BOX} | {"label": str(box["class"]), "color": color}


def valid_box(box, width, height):
    x, y, w, h = (float(box[key]) for key in BOX)
    return (all(math.isfinite(value) for value in (x,y,w,h))
            and x >= 0 and y >= 0 and w > 0 and h > 0 and x+w <= width and y+h <= height)


def parse_edit(row, image, classes):
    try:
        change = json.loads(row.override_value)
        if not isinstance(change, dict) or set(change) != {"class", *BOX} or change["class"] not in classes:
            raise ValueError
        if not valid_box(change, image.width, image.height):
            raise ValueError
        return {"class": change["class"], **{key: float(change[key]) for key in BOX}}
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        raise ValueError(f"{row.item_id}: enter a JSON class,x,y,w,h box within image bounds") from error
