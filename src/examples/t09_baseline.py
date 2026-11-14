"""Two explicit T09 baselines: consent-only and all-HOLD."""
from ..t09 import run as run_t09


def run(ctx):
    mode = ctx.param("baseline_mode", "consent_baseline")
    run_t09(ctx, mode=mode)
