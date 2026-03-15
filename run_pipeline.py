"""
run_pipeline.py
---------------
Main entry point. Runs the full pipeline end-to-end.

Usage:
    python run_pipeline.py --input data.csv
    python run_pipeline.py --input data.csv --ground-truth sites.csv
    python run_pipeline.py --input data.csv --ground-truth sites.csv --out-dir results/
"""

import argparse
import sys
import time
from pathlib import Path

# Make src/ importable when running from project root
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from loader             import load_and_clean
from trilateration      import run_trilateration
from export             import save_csv, build_map, build_validation_map
from validate           import load_ground_truth, merge_with_ground_truth
from distance_strategies import TADistanceStrategy, RSRPDistanceStrategy


def main():
    parser = argparse.ArgumentParser(description='Cell Tower Discovery Engine')
    parser.add_argument('--input',        required=True, help='Raw measurement CSV')
    parser.add_argument('--ground-truth', default=None,  help='Operator site CSV (optional)')
    parser.add_argument('--out-dir',      default='.',   help='Output directory (default: .)')

    # Method selection
    parser.add_argument('--method',       default='both',
                        choices=['ta', 'rsrp', 'both'],
                        help='Positioning method: ta, rsrp, or both (default: both)')

    # RSRP parameters (used when --method=rsrp or both)
    parser.add_argument('--tx-power',     type=float, default=43.0,
                        help='Transmit power in dBm (default: 43.0)')
    parser.add_argument('--pl0',          type=float, default=46.67,
                        help='Reference path loss at 1m in dB (default: 46.67)')
    parser.add_argument('--path-loss-exp', type=float, default=3.76,
                        help='Path loss exponent (default: 3.76 for urban)')
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()

    # ── Stage 1: Load & clean ────────────────────────────────────────────
    print("\n" + "═"*55)
    print("  STAGE 1 — Load & Filter")
    print("═"*55)
    df = load_and_clean(args.input)

    # ── Prepare methods to run ───────────────────────────────────────────
    methods_to_run = []
    if args.method in ['ta', 'both']:
        methods_to_run.append(('TA', TADistanceStrategy()))
    if args.method in ['rsrp', 'both']:
        methods_to_run.append(('RSRP', RSRPDistanceStrategy(
            tx_power=args.tx_power,
            pl0=args.pl0,
            path_loss_exponent=args.path_loss_exp
        )))

    # Load ground truth once if needed
    gt_raw = None
    if args.ground_truth:
        gt_raw = load_ground_truth(args.ground_truth)

    # ── Run each method ──────────────────────────────────────────────────
    results = {}
    for method_name, strategy in methods_to_run:
        print("\n" + "═"*55)
        print(f"  METHOD: {method_name}")
        print("═"*55)

        # Stage 2: Trilateration
        print("\n" + "─"*55)
        print("  STAGE 2 — Trilateration")
        print("─"*55)
        towers = run_trilateration(df, distance_strategy=strategy)

        # Stage 3: Export
        print("\n" + "─"*55)
        print("  STAGE 3 — Export")
        print("─"*55)
        suffix = method_name.lower()
        csv_path = out / f'calculated_towers_{suffix}.csv'
        map_path = out / f'towers_map_{suffix}.html'
        save_csv(towers, str(csv_path))
        build_map(towers, str(map_path))

        # Stage 4: Validate (optional)
        matched = None
        if gt_raw is not None:
            print("\n" + "─"*55)
            print("  STAGE 4 — Ground Truth Validation")
            print("─"*55)
            matched, unmatched = merge_with_ground_truth(towers, gt_raw)

            matched_path   = out / f'merged_towers_{suffix}.csv'
            unmatched_path = out / f'unmatched_towers_{suffix}.csv'
            val_map_path   = out / f'validation_map_{suffix}.html'

            matched.sort_values('error_m').to_csv(matched_path, index=False)
            unmatched.to_csv(unmatched_path, index=False)
            build_validation_map(matched, str(val_map_path))

            print(f"\n  ✅ {matched_path}")
            print(f"  ✅ {unmatched_path}")

        # Store results for comparison
        results[method_name] = {
            'towers': towers,
            'matched': matched,
            'strategy': strategy
        }

    # ── Generate comparison report if both methods ran ──────────────────
    if len(results) == 2 and gt_raw is not None:
        print("\n" + "═"*55)
        print("  GENERATING COMPARISON REPORT")
        print("═"*55)
        try:
            from compare_methods import generate_comparison_report
            comparison_path = out / 'method_comparison.xlsx'
            generate_comparison_report(results, str(comparison_path))
        except ImportError:
            print("  ⚠️  compare_methods.py not found, skipping comparison report")

    # ── Done ─────────────────────────────────────────────────────────────
    elapsed = time.time() - t0
    print(f"\n{'═'*55}")
    print(f"  DONE in {elapsed:.1f}s")
    print(f"  Output files in: {out.resolve()}")
    print(f"{'═'*55}\n")


if __name__ == '__main__':
    main()
