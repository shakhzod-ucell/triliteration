# 📡 Cell Tower Discovery Engine

Reverse-engineers the physical location of LTE cell towers (eNodeBs) using only
passive observations from mobile users — no operator cooperation required.

Supports **two positioning methods** with side-by-side benchmarking:
- **TA (Timing Advance)** — Direct time-of-flight measurement
- **RSRP (Signal Strength)** — Log-distance path loss model

---

## What It Does

The engine uses trilateration to find tower locations from mobile measurements.
Each measurement creates a ring around the unknown tower. Multiple rings from
different positions intersect at the tower location.

### Method 1: Timing Advance (TA) — Recommended

When a 4G device connects to a tower, the network sends a **Timing Advance (TA)**
value encoding round-trip signal travel time:

```
distance (meters) = (TA × 78.125) + 39.0625
```

**Pros:** Direct distance measurement, highly accurate
**Median Error:** 53m (on test dataset)

### Method 2: RSRP (Signal Strength)

Uses received signal power (RSRP) with log-distance path loss model:

```
distance (meters) = 10^((TxPower - RSRP - PL₀) / (10 × n))
```

**Pros:** Works when TA unavailable, tunable parameters
**Cons:** Environment-dependent, less accurate
**Median Error:** 147m (on test dataset with default parameters)

**Input:** CSV with user GPS positions, LTE measurements (TA/RSRP), eNBid, CidRaw
**Output:** Maps, CSVs of discovered towers, comparison reports

---

## Project Structure

```
pipeline/
├── src/
│   ├── loader.py               # Data ingestion, parsing, filtering
│   ├── distance_strategies.py  # TA & RSRP distance calculation
│   ├── trilateration.py        # Core math engine (ring intersection)
│   ├── export.py               # Folium map + CSV generation
│   └── validate.py             # Merge with ground truth, accuracy report
├── run_pipeline.py             # Main entry point — runs full pipeline
├── make_report.py              # Generates Excel accuracy table
├── compare_methods.py          # Multi-method comparison reports
├── requirements.txt
└── README.md
```

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run TA-based positioning (recommended)
python run_pipeline.py --input your_data.csv --ground-truth sites.csv --method ta

# Run RSRP-based positioning
python run_pipeline.py --input your_data.csv --ground-truth sites.csv --method rsrp

# Benchmark both methods (generates comparison report)
python run_pipeline.py --input your_data.csv --ground-truth sites.csv --method both

# Customize RSRP parameters
python run_pipeline.py --input your_data.csv --method rsrp \
  --tx-power 46 --pl0 50 --path-loss-exp 4.0

# Generate comparison report from existing results
python make_report.py --compare merged_towers_ta.csv merged_towers_rsrp.csv \
  --output comparison.xlsx
```

---

## Data Format

Your input CSV has a duplicate `@timestamp` header and a non-standard location field.
The pipeline handles both automatically.

```
@timestamp, LocationSeconds, location,              @timestamp, AppName, LteRsrp, eNBid, ConnectionType, LteTimingAdvance, Cid, CidRaw
2026-03-03, 3,              {"lat":41.18|"lon":69.32}, ...,   -107,    1160,  4G,    7,               40,  297000
```

**Key field relationships (verified 100% on your data):**
```
CidRaw  = eNBid × 256 + Cid     ← CidRaw IS the full ECI
eNBid   = CidRaw >> 8           ← upper 20 bits = physical tower
Cid     = CidRaw & 0xFF         ← lower 8 bits  = sector (0, 1, 2...)
```

One physical tower (eNBid) can have up to 3 sectors (Cid 0/1/2). The engine
groups by eNBid so all sectors from the same mast are used together.

---

## Ground Truth Merge

Your operator site file uses `eci` column. The join key:
```
GT eci >> 8  ==  predicted eNBid
```
The merge applies a 50 km proximity filter to reject cross-operator eNBid collisions
(same number reused by different operators in different cities).

---

## Benchmark Results

Tested on 3,285,048 measurements across 2,419 discovered towers:

### TA-based Positioning (Recommended) ✅

| Tier   | Towers | Median Error | Within 200m |
|--------|--------|-------------|-------------|
| HIGH   | 164    | **34 m**    | 100.0%      |
| MEDIUM | 229    | **53 m**    | 99.6%       |
| LOW    | 244    | **77 m**    | 79.1%       |
| **All**| **637**| **53 m**    | **91.8%**   |

### RSRP-based Positioning

| Tier   | Towers | Median Error | Within 200m |
|--------|--------|-------------|-------------|
| HIGH   | 150    | **106 m**   | 90.7%       |
| MEDIUM | 243    | **139 m**   | 79.8%       |
| LOW    | 244    | **228 m**   | 45.5%       |
| **All**| **637**| **147 m**   | **69.2%**   |

**Winner:** TA method is **64% more accurate** (53m vs 147m median error)

---

## Pipeline Stages

| Stage | Module | What Happens |
|-------|--------|-------------|
| 1 | `loader.py` | Parse pipe-separated location, rename duplicate headers, filter 4G/GPS/TA range |
| 2 | `distance_strategies.py` | Convert measurements to distance (TA or RSRP method) |
| 3 | `trilateration.py` | Group by eNBid, run weighted least-squares ring intersection with multi-start |
| 4 | `trilateration.py` | Score each tower: angular coverage + measurement count + residual error |
| 5 | `export.py` | Save CSV, build Folium interactive map with confidence-colored markers |
| 6 | `validate.py` | Merge with GT via ECI→eNBid, proximity filter, compute real error per tower |
| 7 | `compare_methods.py` | Generate 4-sheet Excel comparison (when using `--method both`) |

## Output Files

When using `--method both`, the pipeline generates:

```
results/
├── calculated_towers_ta.csv          # TA-based tower positions
├── calculated_towers_rsrp.csv        # RSRP-based tower positions
├── towers_map_ta.html                # TA interactive map
├── towers_map_rsrp.html              # RSRP interactive map
├── merged_towers_ta.csv              # TA with validation errors
├── merged_towers_rsrp.csv            # RSRP with validation errors
├── validation_map_ta.html            # TA predicted vs actual
├── validation_map_rsrp.html          # RSRP predicted vs actual
└── method_comparison.xlsx            # 4-sheet comparison report
    ├── Summary                       # Statistical metrics
    ├── Tower-by-Tower                # Per-tower comparison
    ├── TA All Errors                 # Complete TA results
    └── RSRP All Errors               # Complete RSRP results
```

## RSRP Parameter Tuning

The RSRP method has three tunable parameters:

```bash
--tx-power       # Transmit power in dBm (default: 43.0)
--pl0            # Reference path loss at 1m (default: 46.67)
--path-loss-exp  # Path loss exponent (default: 3.76)
```

**Tuning tips:**
- Errors too high → increase `--pl0` or decrease `--path-loss-exp`
- Errors too low → decrease `--pl0` or increase `--path-loss-exp`
- Try different transmit powers if your network uses non-standard configurations

**Example grid search:**
```bash
for n in 3.0 3.5 4.0 4.5; do
  for pl0 in 40 45 50; do
    python run_pipeline.py --method rsrp \
      --path-loss-exp $n --pl0 $pl0 \
      --input data.csv --ground-truth gt.csv \
      --out-dir "results_n${n}_pl${pl0}"
  done
done
```
