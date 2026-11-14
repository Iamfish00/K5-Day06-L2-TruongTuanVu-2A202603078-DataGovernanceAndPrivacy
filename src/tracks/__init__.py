"""Per-track PoC pipelines owned by the group (dispatched from src.pipeline)."""
from __future__ import annotations

from . import t06_gate, t07_redaction, t08_deletion

REGISTRY = {"t06": t06_gate, "t07": t07_redaction, "t08": t08_deletion}
