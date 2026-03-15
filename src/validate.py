"""
validate.py
-----------
Merges predicted tower locations with operator ground-truth site file.

Join key (verified against your data):
    CidRaw = eNBid × 256 + Cid   →   CidRaw IS the full ECI
    GT_eci >> 8  ==  predicted eNBid

The 50 km proximity filter rejects cross-operator eNBid collisions:
same eNBid number can appear in Tashkent (operator A) and Samarkand (operator B).
Without this filter, naive join produces 250+ km errors for those cases.
"""

import numpy as np
import pandas as pd


def _haversine_vec(lat1, lon1, lat2, lon2):
    """Vectorized haversine — returns array of distances in metres."""
    R = 6_371_000.0
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi/2)**2 + np.cos(phi1)*np.cos(phi2)*np.sin(dlam/2)**2
    return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))


def _best_eci_shift(gt_eci: np.ndarray, pred_enbs: set, verbose: bool) -> int:
    """Try ECI >> N for N in [6..10], return shift that gives most matches."""
    best_shift, best_count = 8, 0
    if verbose:
        print("  Testing ECI bit-shifts:")
    for shift in [8, 7, 9, 6, 10]:
        shifted    = np.right_shift(gt_eci, shift)
        n_matches  = len(set(shifted) & pred_enbs)
        if verbose:
            print(f"    ECI >> {shift}  →  {n_matches:,} unique tower matches")
        if n_matches > best_count:
            best_count, best_shift = n_matches, shift
    if verbose:
        print(f"  ✅ Using ECI >> {best_shift}  ({best_count:,} matches)")
    return best_shift


def load_ground_truth(filepath: str,
                      status_filter: str = 'ONAIR',
                      verbose: bool = True) -> pd.DataFrame:
    """
    Load operator site CSV.

    Returns deduplicated DataFrame (one row per eNBid) with columns:
    enb_id, gt_lat, gt_lon, gt_site_name, gt_status, gt_n_sectors,
    gt_bands, gt_azimuths
    """
    if verbose:
        print(f"  Loading ground truth: {filepath}")
    gt = pd.read_csv(filepath)

    if verbose:
        print(f"  Total rows: {len(gt):,}")
        print(f"  Status breakdown:")
        print(gt['status'].value_counts().to_string())

    # Clean ECI
    gt['eci_int'] = (gt['eci'].astype(str)
                        .str.replace('"', '', regex=False).str.strip()
                        .pipe(pd.to_numeric, errors='coerce'))
    gt = gt.dropna(subset=['eci_int']).copy()
    gt['eci_int'] = gt['eci_int'].astype(np.int64)

    # Filter to active sites
    if status_filter:
        gt = gt[gt['status'] == status_filter].copy()
        if verbose:
            print(f"  ONAIR rows: {len(gt):,}")

    return gt


def merge_with_ground_truth(predicted: pd.DataFrame,
                             gt_raw: pd.DataFrame,
                             proximity_km: float = 50,
                             verbose: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Merge predicted towers with ground truth and compute real location error.

    Parameters
    ----------
    predicted    : output of trilateration.run_trilateration()
    gt_raw       : output of load_ground_truth()
    proximity_km : maximum acceptable distance for a valid match

    Returns
    -------
    matched   : predicted towers with GT data + error_m column
    unmatched : predicted towers with no valid GT match
    """
    pred_enbs = set(predicted['enb_id'].astype(np.int64).unique())
    eci_vals  = gt_raw['eci_int'].to_numpy(dtype=np.int64)

    # Find best bit-shift for ECI → eNBid mapping
    best_shift = _best_eci_shift(eci_vals, pred_enbs, verbose)
    gt_raw = gt_raw.copy()
    gt_raw['enb_id'] = np.right_shift(eci_vals, best_shift)

    # Deduplicate GT: one row per eNBid (same mast, multiple sectors → same lat/lon)
    gt_dedup = (gt_raw.sort_values('created_at', ascending=False)
                      .groupby('enb_id')
                      .agg(
                          gt_lat       = ('latitude',  'first'),
                          gt_lon       = ('longitude', 'first'),
                          gt_site_name = ('site_name', 'first'),
                          gt_status    = ('status',    'first'),
                          gt_n_sectors = ('cell_id',   'count'),
                          gt_bands     = ('fband',     lambda x: ' | '.join(x.unique())),
                          gt_azimuths  = ('azimut',    lambda x: ', '.join(
                                              x.dropna().astype(str).unique())),
                      ).reset_index())

    if verbose:
        print(f"  GT unique ONAIR eNBids: {len(gt_dedup):,}")

    # Naive merge on eNBid
    merged = predicted.merge(gt_dedup, on='enb_id', how='left')
    has_gt = merged.dropna(subset=['gt_lat']).copy()

    # Compute candidate distance
    has_gt['candidate_dist_m'] = _haversine_vec(
        has_gt['predicted_lat'].values, has_gt['predicted_lon'].values,
        has_gt['gt_lat'].values,        has_gt['gt_lon'].values,
    )

    # Proximity filter — reject cross-operator collisions
    limit_m = proximity_km * 1000
    matched   = has_gt[has_gt['candidate_dist_m'] <= limit_m].copy()
    rejected  = has_gt[has_gt['candidate_dist_m']  > limit_m]
    no_gt     = merged[merged['gt_lat'].isna()]

    matched['error_m'] = matched['candidate_dist_m']
    unmatched = pd.concat([rejected, no_gt], ignore_index=True)

    if verbose:
        n_pred = len(predicted)
        print(f"\n  Predicted:              {n_pred:,}")
        print(f"  ✅ Matched (≤{proximity_km:.0f}km):  {len(matched):,}  ({100*len(matched)/n_pred:.1f}%)")
        print(f"  ⚠️  Wrong operator (>{proximity_km:.0f}km): {len(rejected):,}")
        print(f"  ❌ No GT entry:          {len(no_gt):,}")

        print(f"\n  📏 REAL LOCATION ACCURACY")
        print(f"  {'─'*42}")
        print(f"  Median error:  {matched['error_m'].median():.0f} m")
        print(f"  Mean error:    {matched['error_m'].mean():.0f} m")
        print(f"  P75 error:     {matched['error_m'].quantile(0.75):.0f} m")
        print(f"  P90 error:     {matched['error_m'].quantile(0.90):.0f} m")

        print(f"\n  Hit rates:")
        for t in [50, 100, 200, 500]:
            pct = (matched['error_m'] < t).mean() * 100
            bar = '█' * int(pct / 2)
            print(f"    within {t:>4}m:  {pct:5.1f}%  {bar}")

        print(f"\n  By confidence tier:")
        for conf in ['HIGH', 'MEDIUM', 'LOW']:
            sub = matched[matched['confidence'] == conf]
            if len(sub) == 0:
                continue
            print(f"  {conf:6s} (n={len(sub):,}):  "
                  f"median {sub['error_m'].median():.0f}m  "
                  f"P90 {sub['error_m'].quantile(0.9):.0f}m  "
                  f"within 200m: {(sub['error_m']<200).mean()*100:.1f}%")

    return matched, unmatched
