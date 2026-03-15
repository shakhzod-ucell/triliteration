"""
distance_strategies.py
----------------------
Different strategies for computing distance from measurement to tower.
Each strategy takes a DataFrame of measurements and adds a 'distance_m' column.

This enables benchmarking multiple positioning methods (TA, RSRP, etc.) with
the same trilateration algorithm.
"""

import numpy as np
import pandas as pd


# ── Base Strategy Class ──────────────────────────────────────────────────────
class DistanceStrategy:
    """Base class for distance computation strategies."""

    def compute_distances(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add 'distance_m' column to DataFrame based on measurements."""
        raise NotImplementedError

    def get_metadata(self) -> dict:
        """Return metadata about this strategy for reporting."""
        raise NotImplementedError


# ── TA-based distance (existing method) ──────────────────────────────────────
class TADistanceStrategy(DistanceStrategy):
    """
    Convert Timing Advance to distance using 3GPP TS 36.211 formula.

    LTE Timing Advance represents the round-trip signal travel time.
    Each TA unit corresponds to 78.125 meters, with a half-step offset.

    Formula:
        distance (m) = TA × 78.125 + 39.0625

    This is the baseline method used by the original pipeline.
    """

    TA_STEP_M   = 78.125    # meters per TA unit
    TA_OFFSET_M = 39.0625   # half-step offset

    def __init__(self):
        self.name = "TA"
        self.description = "Timing Advance (3GPP TS 36.211)"

    def compute_distances(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add 'distance_m' column based on TA measurements."""
        df = df.copy()
        df['distance_m'] = df['ta'] * self.TA_STEP_M + self.TA_OFFSET_M
        return df

    def get_metadata(self) -> dict:
        """Return metadata for reporting."""
        return {
            'method': self.name,
            'description': self.description,
            'formula': f'distance = TA × {self.TA_STEP_M} + {self.TA_OFFSET_M}',
            'ta_step_m': self.TA_STEP_M,
            'ta_offset_m': self.TA_OFFSET_M,
        }


# ── RSRP-based distance (new method) ─────────────────────────────────────────
class RSRPDistanceStrategy(DistanceStrategy):
    """
    Log-distance path loss model for converting RSRP to distance.

    Uses the standard radio propagation formula:
        PL(d) = PL0 + 10 × n × log10(d)

    Where:
        PL(d) = Path Loss at distance d
        PL0   = Reference path loss at 1 meter
        n     = Path loss exponent (environment-dependent)
        d     = Distance in meters

    Since PL(d) = TxPower - RSRP, we can solve for distance:
        distance = 10^((TxPower - RSRP - PL0) / (10 × n))

    Parameters
    ----------
    tx_power : float
        Base station transmit power in dBm.
        Typical values for LTE macro cells:
        - 40 dBm (10W) for small cells
        - 43 dBm (20W) for typical macro sites
        - 46 dBm (40W) for high-power sites

    pl0 : float
        Reference path loss at 1 meter in dB.
        Theoretical free space at 1800 MHz: 46.67 dB
        Typical urban range: 40-50 dB

    path_loss_exponent : float
        Path loss exponent (n).
        - 2.0: Free space (line-of-sight)
        - 2-4: Urban/suburban environments
        - 4-6: Indoor/dense urban
        Default urban LTE: 3.76

    Notes
    -----
    Distance estimates are clipped to [1m, 20km] to handle:
    - Very strong signals (RSRP > TxPower) → clip to 1m
    - Very weak signals (RSRP << TxPower) → clip to 20km
    """

    def __init__(self,
                 tx_power: float = 43.0,
                 pl0: float = 46.67,
                 path_loss_exponent: float = 3.76):
        """
        Initialize RSRP distance strategy.

        Default parameters are based on typical urban LTE deployment:
        - tx_power = 43 dBm (20W typical macro cell)
        - pl0 = 46.67 dB (free space at 1800 MHz)
        - path_loss_exponent = 3.76 (urban environment)
        """
        self.tx_power = float(tx_power)
        self.pl0 = float(pl0)
        self.path_loss_exponent = float(path_loss_exponent)
        self.name = "RSRP"
        self.description = (f"Log-distance path loss "
                           f"(Tx={tx_power:.1f}dBm, PL0={pl0:.1f}dB, n={path_loss_exponent:.2f})")

        # Validate parameters
        if self.tx_power < 20 or self.tx_power > 60:
            raise ValueError(f"tx_power={tx_power} outside reasonable range [20, 60] dBm")
        if self.pl0 < 20 or self.pl0 > 80:
            raise ValueError(f"pl0={pl0} outside reasonable range [20, 80] dB")
        if self.path_loss_exponent < 1.5 or self.path_loss_exponent > 8:
            raise ValueError(f"path_loss_exponent={path_loss_exponent} outside reasonable range [1.5, 8]")

    def compute_distances(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add 'distance_m' column based on RSRP measurements.

        Formula:
            distance = 10^((TxPower - RSRP - PL0) / (10 × n))

        Returns
        -------
        pd.DataFrame
            Copy of input DataFrame with 'distance_m' column added.
        """
        df = df.copy()

        # Calculate path loss: PL(d) = TxPower - RSRP
        path_loss = self.tx_power - df['rsrp']

        # Solve for distance: d = 10^((PL - PL0) / (10*n))
        exponent = (path_loss - self.pl0) / (10.0 * self.path_loss_exponent)
        df['distance_m'] = 10.0 ** exponent

        # Sanity bounds: clip to reasonable range
        # - Lower bound (1m): handles cases where RSRP > TxPower (shouldn't happen but clip anyway)
        # - Upper bound (20km): handles very weak signals that would otherwise give unrealistic distances
        df['distance_m'] = df['distance_m'].clip(1.0, 20000.0)

        return df

    def get_metadata(self) -> dict:
        """Return metadata for reporting."""
        return {
            'method': self.name,
            'description': self.description,
            'formula': 'd = 10^((Tx - RSRP - PL0) / (10×n))',
            'tx_power_dbm': self.tx_power,
            'pl0_db': self.pl0,
            'path_loss_exponent': self.path_loss_exponent,
        }

    def __repr__(self):
        return (f"RSRPDistanceStrategy(tx_power={self.tx_power}, "
                f"pl0={self.pl0}, path_loss_exponent={self.path_loss_exponent})")
