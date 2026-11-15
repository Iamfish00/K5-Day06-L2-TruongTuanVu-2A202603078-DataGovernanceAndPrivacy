"""T02 baseline ranked by whole-image Laplacian variance."""

from ..common.context import RunContext
from ..pipeline import run_t02


def run(ctx: RunContext) -> None:
    if ctx.track != "t02":
        raise NotImplementedError("The Laplacian baseline supports T02 only")
    run_t02(ctx, method="laplacian")
