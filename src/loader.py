"""
loader.py
---------
Ingests the raw measurement CSV, handles all format quirks, applies the
filter pipeline, and returns a clean DataFrame ready for trilateration.

Quirks handled:
  - Duplicate '@timestamp' column header → renamed on load
  - location field uses pipe separator: {"lat":41.18|"lon":69.32}
  - CidRaw encodes eNBid and sector: CidRaw = eNBid * 256 + Cid (verified 100%)
"""

import re
import pandas as pd
import numpy as np

# ── Constants ────────────────────────────────────────────────────────────────
# LTE Timing Advance → distance formula (3GPP TS 36.211)
TA_STEP_M   = 78.125    # meters per TA unit
TA_OFFSET_M = 39.0625   # half-step offset

# Filter thresholds
TA_MAX          = 150   # TA > 150 almost always means indoor/reflected signal
LOC_SEC_MAX     = 30    # GPS fixes older than 30s are too stale for accuracy
MIN_VALID_LAT   = 0.0   # exclude null-island (0,0) GPS errors

# CSV column names (handles duplicate @timestamp header)
CSV_COLUMNS = ['ts', 'loc_sec', 'location', 'ts2', 'app',
               'rsrp', 'enb', 'conn', 'ta', 'cid', 'cid_raw']

# ── Location parser ──────────────────────────────────────────────────────────
_LAT_RE = re.compile(r'"lat":([-\d\.]+)')
_LON_RE = re.compile(r'"lon":([-\d\.]+)')

def _parse_location(series: pd.Series):
    """Extract lat/lon from pipe-separated JSON-like strings."""
    lat = series.str.extract(_LAT_RE, expand=False).astype(float)
    lon = series.str.extract(_LON_RE, expand=False).astype(float)
    return lat, lon

# ── Main loader ──────────────────────────────────────────────────────────────
def load_and_clean(filepath: str, verbose: bool = True) -> pd.DataFrame:
    """
    Load raw CSV, parse all fields, apply filter pipeline.

    Parameters
    ----------
    filepath : str
        Path to the raw measurement CSV.
    verbose : bool
        Print row counts at each filter stage.

    Returns
    -------
    pd.DataFrame
        Clean DataFrame with columns:
        user_lat, user_lon, ta_m, quality_weight,
        enb (eNBid), cid, cid_raw, rsrp, loc_sec
    """
    if verbose:
        print(f"  Loading: {filepath}")

    df = pd.read_csv(filepath, names=CSV_COLUMNS, skiprows=1, low_memory=False)
    n_raw = len(df)
    if verbose:
        print(f"  Raw rows: {n_raw:,}")

    # ── Parse location ────────────────────────────────────────────────────
    df['user_lat'], df['user_lon'] = _parse_location(df['location'])

    # ── Numeric coercion ──────────────────────────────────────────────────
    for col in ['enb', 'cid', 'cid_raw', 'ta', 'rsrp']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['loc_sec'] = pd.to_numeric(df['loc_sec'], errors='coerce').fillna(999)

    # ── Filter pipeline ───────────────────────────────────────────────────
    steps = [
        ("4G only",              lambda d: d[d['conn'] == '4G']),
        ("drop nulls",           lambda d: d.dropna(subset=['user_lat','user_lon','ta','enb'])),
        ("valid GPS",            lambda d: d[(d['user_lat'] > MIN_VALID_LAT) & (d['user_lon'] > MIN_VALID_LAT)]),
        (f"TA 0–{TA_MAX}",       lambda d: d[(d['ta'] >= 0) & (d['ta'] <= TA_MAX)]),
        (f"GPS ≤{LOC_SEC_MAX}s", lambda d: d[d['loc_sec'] <= LOC_SEC_MAX]),
    ]

    for label, fn in steps:
        before = len(df)
        df = fn(df).copy()
        if verbose:
            print(f"  [{label:20s}]  {len(df):>10,}  (−{before - len(df):,})")

    # ── Derived columns ───────────────────────────────────────────────────
    df['ta_m']           = df['ta'] * TA_STEP_M + TA_OFFSET_M
    df['quality_weight'] = 1.0 / (1.0 + df['loc_sec'])  # fresher GPS = higher weight

    # Final type enforcement
    df['enb']     = df['enb'].astype(np.int64)
    df['cid']     = df['cid'].astype(np.int64)
    df['cid_raw'] = df['cid_raw'].astype(np.int64)

    if verbose:
        print(f"\n  ✅ Valid rows:     {len(df):,}")
        print(f"  📡 Unique eNBids: {df['enb'].nunique():,}")
        counts = df.groupby('enb').size()
        print(f"  📊 Measurements/tower: "
              f"median={counts.median():.0f}  "
              f"min={counts.min()}  "
              f"max={counts.max()}")

    return df
