"""
trilateration.py
----------------
Core mathematical engine.

Each user measurement defines a ring around the unknown tower:
    ring radius = (TA × 78.125) + 39.0625 meters

Given N rings from N user positions, we find the single point that
minimises the sum of squared deviations from all ring surfaces.
This is a nonlinear least-squares problem solved with scipy.

Confidence scoring uses three factors relative to this dataset:
    1. Angular coverage  — do users surround the tower from multiple directions?
    2. Measurement count — more observations = more reliable estimate
    3. Residual error    — how tightly do the rings actually intersect?
"""

from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

# ── Thresholds ───────────────────────────────────────────────────────────────
MIN_MEASUREMENTS   = 3        # absolute minimum for trilateration geometry
MAX_CENTROID_DIST  = 50_000   # reject if predicted > 50 km from data centroid
MAX_RESIDUAL_M     = 800      # reject if mean ring residual > 800 m
MIN_ANGULAR_SPREAD = 8        # reject if all measurements from same direction (°)

# ── Haversine (vectorized) ───────────────────────────────────────────────────
def haversine(lat1: float, lon1: float,
              lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    """Great-circle distance in metres between (lat1,lon1) and each point."""
    R = 6_371_000.0
    phi1 = np.radians(lat1)
    phi2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlam = np.radians(lon2 - lon1)
    a = (np.sin(dphi / 2) ** 2
         + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2)
    return R * 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))


# ── Angular spread ───────────────────────────────────────────────────────────
def angular_spread(lat0: float, lon0: float,
                   user_lats: np.ndarray, user_lons: np.ndarray) -> float:
    """
    Circular standard deviation of bearings from predicted tower to users.
    High value (>90°) means users surround the tower from many directions.
    Low value (<20°) means all users are along one road — poor geometry.
    """
    bearings = np.radians(
        np.degrees(np.arctan2(user_lons - lon0, user_lats - lat0)) % 360
    )
    R_bar = np.sqrt(np.mean(np.sin(bearings)) ** 2 +
                    np.mean(np.cos(bearings)) ** 2)
    return float(np.degrees(np.sqrt(max(0.0, -2.0 * np.log(R_bar + 1e-9)))))


# ── Single tower optimizer ───────────────────────────────────────────────────
def _fit_tower(lats, lons, dists, weights):
    """
    Run weighted least-squares from multiple starting points.
    Returns (best_result, None) or (None, reason_string).
    """
    def residuals(guess):
        calc = haversine(guess[0], guess[1], lats, lons)
        return (calc - dists) * weights

    # Multi-start: centroid, median, freshest-GPS point
    starts = [
        [lats.mean(),              lons.mean()],
        [np.median(lats),          np.median(lons)],
        [lats[weights.argmax()],   lons[weights.argmax()]],
    ]

    best = None
    for x0 in starts:
        try:
            res = least_squares(residuals, x0, loss='soft_l1', max_nfev=1000)
            if best is None or res.cost < best.cost:
                best = res
        except Exception:
            continue
    return best


# ── Per-group trilateration ──────────────────────────────────────────────────
def find_tower(group: pd.DataFrame) -> Optional[dict]:
    """
    Given all measurements for one eNBid, return predicted location dict or None.
    """
    lats  = group['user_lat'].to_numpy(float)
    lons  = group['user_lon'].to_numpy(float)
    dists = group['distance_m'].to_numpy(float)
    wgts  = group['quality_weight'].to_numpy(float)
    n     = len(group)

    if n < MIN_MEASUREMENTS:
        return None

    best = _fit_tower(lats, lons, dists, wgts)
    if best is None:
        return None

    pred_lat, pred_lon = best.x
    calc_dists    = haversine(pred_lat, pred_lon, lats, lons)
    mean_residual = float(np.mean(np.abs(calc_dists - dists)))
    med_residual  = float(np.median(np.abs(calc_dists - dists)))
    ang_spread    = angular_spread(pred_lat, pred_lon, lats, lons)

    # ── Sanity checks ─────────────────────────────────────────────────────
    centroid_dist = haversine(lats.mean(), lons.mean(),
                              np.array([pred_lat]), np.array([pred_lon]))[0]
    if centroid_dist  > MAX_CENTROID_DIST: return None
    if mean_residual  > MAX_RESIDUAL_M:    return None
    if ang_spread     < MIN_ANGULAR_SPREAD: return None

    return {
        'enb_id':            int(group['enb'].iloc[0]),
        'predicted_lat':     round(pred_lat, 6),
        'predicted_lon':     round(pred_lon, 6),
        'n_measurements':    n,
        'mean_residual_m':   round(mean_residual, 1),
        'median_residual_m': round(med_residual, 1),
        'angular_spread':    round(ang_spread, 1),
        'mean_rsrp':         round(float(group['rsrp'].mean()), 1),
        'unique_sectors':    int(group['cid'].nunique()),
        'optimizer_cost':    round(float(best.cost), 2),
    }


# ── Confidence scoring ───────────────────────────────────────────────────────
def assign_confidence(towers: pd.DataFrame) -> pd.DataFrame:
    """
    Score each tower 0–100 using percentile ranks within this dataset.
    This is relative scoring: HIGH = top third of THIS run.

    Factors:
      - Count score  (40 pts): log-scaled measurement count
      - Residual score (35 pts): lower ring residual = better
      - Angular score  (25 pts): wider user spread = better geometry
    """
    df = towers.copy()

    def pct_score(series, pts, invert=False):
        lo  = series.quantile(0.05)
        hi  = series.quantile(0.95)
        rng = hi - lo + 1e-9
        norm = (series - lo) / rng
        norm = norm.clip(0, 1)
        if invert:
            norm = 1 - norm
        return (norm * pts).round(2)

    df['score_count']    = pct_score(np.log1p(df['n_measurements']),  40)
    df['score_residual'] = pct_score(df['mean_residual_m'],            35, invert=True)
    df['score_angular']  = pct_score(df['angular_spread'],             25)
    df['confidence_score'] = (df['score_count']
                              + df['score_residual']
                              + df['score_angular']).round(1)

    p67 = df['confidence_score'].quantile(0.67)
    p33 = df['confidence_score'].quantile(0.33)

    def label(s):
        if s >= p67: return 'HIGH'
        if s >= p33: return 'MEDIUM'
        return 'LOW'

    df['confidence'] = df['confidence_score'].apply(label)
    df = df.drop(columns=['score_count', 'score_residual', 'score_angular'])
    return df


# ── Run trilateration over full dataset ──────────────────────────────────────
def run_trilateration(df: pd.DataFrame,
                      distance_strategy=None,
                      verbose: bool = True) -> pd.DataFrame:
    """
    Group by eNBid, run find_tower() for each group, score confidence.

    Parameters
    ----------
    df : pd.DataFrame
        Output of loader.load_and_clean().
    distance_strategy : DistanceStrategy, optional
        Strategy for computing distances from measurements.
        If None, uses TA-based method (backward compatibility).
    verbose : bool
        Print progress information.

    Returns
    -------
    pd.DataFrame
        One row per discovered tower with coordinates and quality metrics.
    """
    # Apply distance strategy
    if distance_strategy is not None:
        df = distance_strategy.compute_distances(df)
        method_name = distance_strategy.name
        method_desc = distance_strategy.description
    else:
        # Backward compatibility: use TA method if already has 'ta_m' column
        if 'ta_m' in df.columns:
            df = df.copy()
            df['distance_m'] = df['ta_m']
            method_name = "TA"
            method_desc = "Timing Advance (legacy)"
        else:
            raise ValueError("DataFrame must have 'distance_m' column or provide distance_strategy")

    if verbose:
        print(f"  Distance method: {method_name} — {method_desc}")

    grouped = df.groupby('enb')
    results, skipped_few, skipped_sanity = [], 0, 0

    if verbose:
        print(f"  Processing {grouped.ngroups:,} unique towers…")

    for i, (enb_id, group) in enumerate(grouped):
        if verbose and i > 0 and i % 500 == 0:
            print(f"    {i:>4}/{grouped.ngroups}…")

        if len(group) < MIN_MEASUREMENTS:
            skipped_few += 1
            continue

        result = find_tower(group)
        if result is None:
            skipped_sanity += 1
        else:
            results.append(result)

    towers = pd.DataFrame(results)
    towers = assign_confidence(towers)

    if verbose:
        high   = (towers['confidence'] == 'HIGH').sum()
        medium = (towers['confidence'] == 'MEDIUM').sum()
        low    = (towers['confidence'] == 'LOW').sum()
        print(f"\n  ✅ Located:  {len(towers):,}")
        print(f"  ⏭  Skipped:  {skipped_few:,}  (< {MIN_MEASUREMENTS} measurements)")
        print(f"  ⚠️  Rejected: {skipped_sanity:,}  (sanity checks)")
        print(f"\n  🟢 HIGH:   {high:,}  ({100*high/len(towers):.1f}%)")
        print(f"  🟠 MEDIUM: {medium:,}  ({100*medium/len(towers):.1f}%)")
        print(f"  🔴 LOW:    {low:,}  ({100*low/len(towers):.1f}%)")
        print(f"\n  📊 Residual stats:")
        print(f"     Median: {towers['mean_residual_m'].median():.0f} m")
        print(f"     P90:    {towers['mean_residual_m'].quantile(0.9):.0f} m")

    return towers
