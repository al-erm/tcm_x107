"""
Mathematical processing of measurements
"""

from __future__ import annotations

import numpy as np
from typing import List, Tuple
from scipy.stats import siegelslopes, t

P = 0.95  # confidence probability
BOOTSTRAP_REPLICATES = 1000  # bootstrap replicates for post-selection-aware CI


def robust_average_current(currents: List[float], threshold: float = 3.0) -> Tuple[float, float, np.ndarray]:
    """Robust averaging of current measurements via Siegel slopes + outlier rejection.

    Algorithm:
    1. Fit a straight line to (sample_index, current) using Siegel's repeated
       median regression (`scipy.stats.siegelslopes`).
    2. Subtract the fitted line from all data -> residuals.
    3. Compute the standard deviation of the residuals.
    4. Mark the single point with the largest |residual| as outlier if
       |residual| > threshold · s.
    5. Remove that one outlier and repeat from step 1 until no outliers remain.
    6. Return (mean_current, confidence_interval_half_width, outlier_indices):
       - mean_current       = mean of the fitted values over the cleaned x-range
       - error              = t_coeff(P, n-1) · s / √n   (two-sided Student's interval)
       - outlier_indices    = original sample indices that were rejected as outliers

    Parameters
    ----------
    currents : list of float
        Raw current readings from the Keithley buffer.
    threshold : float
        Outlier rejection threshold in units of s (default 3.0).
    """
    arr = np.array(currents, dtype=float)
    if arr.size == 0:
        return 0.0, 0.0, np.array([], dtype=int)

    x = np.arange(arr.size, dtype=float)
    y = arr.copy()
    x_work = x.copy()
    idx_work = np.arange(arr.size, dtype=int)

    all_outlier_indices: list[int] = []

    # Iterative loop: fit -> subtract -> reject the worst outlier -> refit
    while True:
        if y.size < 2:
            # Not enough points for regression; fall back to plain mean
            mean_val = float(np.mean(y)) if y.size > 0 else 0.0
            return mean_val, 0.0, np.array(sorted(all_outlier_indices), dtype=int)

        # Siegel slopes – robust linear fit, insensitive to outliers
        slope, intercept = siegelslopes(y, x_work, method='separate')
        fitted = slope * x_work + intercept
        residuals = y - fitted

        # Standard deviation of residuals
        std_res = float(np.std(residuals, ddof=2)) if residuals.size > 2 else 0.0
        if std_res == 0.0:
            break
        # Find the single worst outlier. Removing only one point per pass
        # avoids masking: two large outliers inflate std_res so much that
        # neither exceeds the threshold if both are tested at once.
        abs_res = np.abs(residuals)
        worst_idx = int(np.argmax(abs_res))
        if abs_res[worst_idx] <= threshold * std_res:
            break

        # Remember original index of rejected point
        all_outlier_indices.append(int(idx_work[worst_idx]))

        # Remove only the worst outlier and repeat the whole calculation
        keep = np.ones(y.size, dtype=bool)
        keep[worst_idx] = False
        y = y[keep]
        x_work = x_work[keep]
        idx_work = idx_work[keep]

    # Final fit on the cleaned data
    slope, intercept = siegelslopes(y, x_work)
    fitted = slope * x_work + intercept
    residuals = y - fitted
    n = y.size

    # Estimated current: mean level of the fitted line over the cleaned range
    current_mean = float(np.mean(fitted)) if n > 0 else 0.0

    # Standard deviation of residuals
    std_res = float(np.std(residuals, ddof=2)) if n > 2 else 0.0

    # Confidence interval: bootstrap percentile (accounts for post-selection
    # after outlier removal); fall back to Student's t with df = n-2 for tiny n.
    if n >= 4 and std_res > 0.0:
        _, error = _bootstrap_mean_level(x_work, y)
    elif n > 2 and std_res > 0.0:
        df = n - 2
        t_coeff = float(t.ppf((1.0 + P) / 2.0, df))
        error = t_coeff * std_res / np.sqrt(n)
    else:
        error = 0.0
    return current_mean, float(error), np.array(sorted(all_outlier_indices), dtype=int)


def _bootstrap_mean_level(x: np.ndarray, y: np.ndarray,
                          n_replicates: int = BOOTSTRAP_REPLICATES,
                          seed: int | None = None) -> Tuple[float, float]:
    """
    Bootstrap percentile confidence interval for the mean level of a Siegel line.

    Resamples (x, y) pairs with replacement, fits Siegel slopes on each replicate,
    evaluates the fitted line at mean(x), and returns the bootstrap center and
    half-width of the percentile interval at level P.

    Re-running the same selection-then-estimation pipeline on each bootstrap
    sample approximately accounts for the optimism introduced by prior
    outlier removal (post-selection bias).
    """
    rng = np.random.default_rng(seed)
    n = x.size
    x_mean = float(np.mean(x))
    estimates = np.empty(n_replicates, dtype=float)
    for b in range(n_replicates):
        idx = rng.integers(0, n, size=n)
        x_boot = x[idx]
        y_boot = y[idx]
        try:
            slope_b, intercept_b = siegelslopes(y_boot, x_boot, method='separate')
            estimates[b] = slope_b * x_mean + intercept_b
        except Exception:
            estimates[b] = np.nan
    estimates = estimates[~np.isnan(estimates)]
    if estimates.size == 0:
        return 0.0, 0.0
    alpha = 1.0 - P
    low = float(np.percentile(estimates, 100.0 * alpha / 2.0))
    high = float(np.percentile(estimates, 100.0 * (1.0 - alpha / 2.0)))
    center = float(np.mean(estimates))
    error = max(center - low, high - center)
    return center, error
