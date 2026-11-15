"""Align camera timestamps to IMU using local motion correlation."""

import numpy as np
import pandas as pd
from scipy.stats import linregress

from .review_stage import finalize, make_queue
from ..pipeline import verify_inputs


def nearest(values, targets):
    right = np.searchsorted(values, targets).clip(1, len(values) - 1)
    left = right - 1
    return np.where(abs(values[left] - targets) <= abs(values[right] - targets), left, right)


def fit_clock(cam, motion, imu, az, regime):
    lag_grid = np.arange(-350, 351, 5)
    acceleration = abs(az - np.median(az))
    corrected = cam.copy()
    for group in np.unique(regime):
        indices = np.flatnonzero(regime == group)
        centers, lags = [], []
        for window in np.array_split(indices, max(1, len(indices) // 240)):
            if len(window) < 30 or np.std(motion[window]) == 0:
                continue
            times = cam[window]
            signal = motion[window]
            scores = []
            for lag in lag_grid:
                shifted = times + lag
                closest = imu[nearest(imu, shifted)]
                valid = abs(shifted - closest) <= 15
                shifted_signal = np.interp(shifted[valid], imu, acceleration)
                quality = np.corrcoef(signal[valid], shifted_signal)[0, 1] if valid.sum() >= 30 and np.std(shifted_signal) > 0 and np.std(signal[valid]) > 0 else -1
                scores.append(quality)
            best = int(np.argmax(scores))
            if scores[best] >= 0.15:
                centers.append(np.median(times))
                lags.append(lag_grid[best])
        if centers:
            origin = centers[0]
            if len(centers) > 1:
                slope = float(np.clip(linregress(np.array(centers) - origin, lags).slope, -0.005, 0.005))
            else:
                slope = 0.0
            intercept = np.median(np.array(lags) - slope * (np.array(centers) - origin))
            corrected[indices] += intercept + slope * (cam[indices] - origin)
    return corrected


def run(ctx, *, baseline=False):
    verify_inputs(ctx)
    shock = ctx.param("shock", False)
    camera = pd.read_csv(ctx.input_dir / ("shock/camera_clock_reset.csv" if shock else "camera.csv"))
    imu = pd.read_csv(ctx.input_dir / ("shock/imu_clock_reset.csv" if shock else "imu.csv"))
    if camera.empty or len(imu) < 2 or not camera.frame_id.is_unique or not imu.t_imu_ms.is_monotonic_increasing or not imu.t_imu_ms.is_unique:
        raise ValueError("Need unique camera frame IDs and at least two strictly increasing IMU timestamps")
    epoch = float(imu.t_imu_ms.iloc[0])
    raw = camera.t_cam_s.to_numpy(float) * 1000 - epoch
    reference = imu.t_imu_ms.to_numpy(float) - epoch
    reset = np.r_[False, np.diff(raw) < -5000]
    times = raw.copy()
    for i in np.flatnonzero(reset):
        period = np.median(np.diff(times[max(0, i-90):i]))
        times[i:] += times[i-1] + period - times[i]
    # Split fitting on discontinuities and on each 20-second segment.
    frame_numbers = camera.frame_id.str.extract(r"(\d+)$")[0].astype(int)
    regimes = frame_numbers.to_numpy() // 600 + 1000 * reset.cumsum()
    corrected = raw if baseline else fit_clock(times, camera.motion_score.to_numpy(), reference, imu.az.to_numpy(), regimes)
    indexes = nearest(reference, corrected)
    residual = abs(reference[indexes] - corrected)
    tolerance = float(ctx.param("tol_ms", 50)) if baseline else min(20.0, float(ctx.param("tol_ms", 50)))
    hold = residual > tolerance
    results = camera[["frame_id"]].assign(
        imu_t_ms=pd.array(imu.t_imu_ms.to_numpy()[indexes], dtype="Int64"),
        offset_ms=corrected - times, residual_ms=residual, reset_detected=reset,
    )
    results.loc[hold, "imu_t_ms"] = pd.NA
    reviewed, events = [], []
    evidence = ["pairs.csv", "sync_report.csv"]
    if not baseline:
        items = results.rename(columns={"frame_id": "item_id"}).assign(
            score=residual + reset * (residual.max() + 1),
            reason=np.where(reset, "clock_reset", np.where(hold, "missing_imu_or_outside_tolerance", "check_alignment")),
            suggested_action=np.where(hold, "HOLD", "PAIR"),
        )
        _, reviewed = make_queue(ctx, items, 5, "T04 - Sensor alignment review", fields=("offset_ms", "residual_ms"))
        results = results.set_index("frame_id", drop=False)
        for row in reviewed:
            before = results.at[row.item_id, "imu_t_ms"]
            if row.reviewer_decision in {"reject", "escalate", "edit"}:
                if row.reviewer_decision == "edit" and row.override_value.strip().upper() != "HOLD":
                    raise ValueError("T04 edit must set HOLD")
                results.at[row.item_id, "imu_t_ms"] = pd.NA
            after = results.at[row.item_id, "imu_t_ms"]
            events.append((row, None if pd.isna(before) else int(before), None if pd.isna(after) else int(after)))
        evidence += ["review_queue.csv", "offset.png"]
        from matplotlib.figure import Figure
        plot = Figure(figsize=(9, 3))
        axis = plot.subplots()
        axis.plot(times / 1000, corrected - times, linewidth=1)
        axis.set(xlabel="Seconds from IMU epoch", ylabel="Fitted correction (ms)")
        plot.savefig(ctx.out("offset.png"), bbox_inches="tight")
    results[["frame_id", "imu_t_ms"]].to_csv(ctx.out("pairs.csv"), index=False)
    results.to_csv(ctx.out("sync_report.csv"), index=False)
    ctx.stats.update(n_held=int(results.imu_t_ms.isna().sum()), resets=int(reset.sum()))
    finalize(ctx, len(results), reviewed, events, evidence, baseline=baseline)
