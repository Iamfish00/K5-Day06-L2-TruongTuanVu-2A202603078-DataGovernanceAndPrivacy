"""Trivial T09 baseline to expose the false-HOLD guardrail."""
from ..t09 import run as run_t09


def run(ctx):
    run_t09(ctx, mode="all_hold_baseline")
