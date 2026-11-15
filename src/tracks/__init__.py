"""Per-track PoC pipelines owned by the group (dispatched from src.pipeline)."""
from __future__ import annotations

from . import (
    t04_sync, t05_duplicates, t06_gate, t07_redaction, t08_deletion, t10_shift, t11_labels,
    t13_mining, t14_export,
)

REGISTRY = {
    "t04": t04_sync, "t05": t05_duplicates, "t06": t06_gate, "t07": t07_redaction, "t08": t08_deletion,
    "t10": t10_shift, "t11": t11_labels, "t13": t13_mining, "t14": t14_export,
}
