#!/usr/bin/env python3
"""
dipnrally_ranker_audit.py — Diagnostic harness per sibling-engine briefing.

PURPOSE
  Tests whether scan_dip_rally_grid's EV-as-ranker picks from the same
  region as joint-P-as-ranker among the 65/75-qualifying candidates.

  Methodology adapted from the sibling diprally-engine's audit harness.
  We do NOT change any production code — this is pure diagnostic.

METHODOLOGY
  1. Generate synthetic setups covering σ × drift spectrum (9 setups)
  2. For each setup, run MC + scan_dip_rally_grid to get candidate pairs
  3. Strict-filter to candidates meeting 65% dip AND 75% rally-conditional
  4. Rank the SAME qualifying candidates three ways:
       (a) by net_expected_value descending (current production ranker)
       (b) by p_round_trip descending (hit-rate ranker)
       (c) by sharpe_equiv descending (risk-adjusted ranker)
  5. Measure top-10 overlap between rankings — this is the key metric

INTERPRETATION (per briefing)
  - Mean top-10 EV∩P overlap 8-10/10: EV-as-ranker aligned, no change needed
  - Mean 3-7/10: partial misalignment, investigate calibration
  - Mean 0-2/10: structural misalignment, two-stage ranker fix warranted

  The diprally-engine reported 0/10 overlap across every setup tested.
  If THIS engine shows similar 0/10, the briefing's prescription transfers.
  If 8+/10, ignore the briefing — engine is aligned.

NO PRODUCTION CHANGES MADE.
  - Imports from swing_analyzer_dipnrally.py (read only)
  - Uses synthetic spot/σ/μ (no FMP, no Anthropic, no costs)
  - Writes no CSV, no dashboard, no anything outside this script's stdout

USAGE
  python3 tools/dipnrally_ranker_audit.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from swing_analyzer_dipnrally import (  # type: ignore
    run_mc_joint_conditional,
    scan_dip_rally_grid,
    JointConditionalResult,
)


# =============================================================================
# Synthetic setups — cover the σ × drift spectrum
# =============================================================================
# Normalised spot = $1000 across all setups so dollar values are directly
# comparable. Grid step $10 at $1000 spot = 1% resolution (matches SNDK
# at $1500 with $10 step ≈ 0.67%).

@dataclass
class Setup:
    label: str
    sigma: float    # annualised
    mu: float       # annualised
    spot: float = 1000.0


SETUPS = [
    Setup("normal  σ40% / bullish μ+20%",   0.40,  0.20),
    Setup("normal  σ40% / neutral μ+5%",    0.40,  0.05),
    Setup("normal  σ40% / bearish μ-10%",   0.40, -0.10),
    Setup("high    σ70% / bullish μ+20%",   0.70,  0.20),
    Setup("high    σ70% / neutral μ+5%",    0.70,  0.05),
    Setup("high    σ70% / bearish μ-10%",   0.70, -0.10),
    Setup("extreme σ100% / bullish μ+20%",  1.00,  0.20),   # SNDK-like
    Setup("extreme σ100% / neutral μ+5%",   1.00,  0.05),
    Setup("extreme σ100% / bearish μ-10%",  1.00, -0.10),
]

# Match production thresholds. Don't change these — we're auditing under prod conditions.
HORIZON = 60
N_PATHS = 60_000
CONVICTION_DIP = 0.65
CONVICTION_RALLY_COND = 0.75
GRID_STEP = 10.0
GRID_DEPTH = 0.40
GRID_REACH = 0.60
SPREAD = 2.0


# =============================================================================
# Ranking functions
# =============================================================================

def rank_by_ev(c: JointConditionalResult) -> float:
    return c.net_expected_value

def rank_by_p_round_trip(c: JointConditionalResult) -> float:
    return c.p_round_trip

def rank_by_sharpe_equiv(c: JointConditionalResult) -> float:
    """EV / downside-risk proxy. Higher = better risk-adjusted return."""
    return c.net_expected_value / max(abs(c.expected_bag_hold_loss), 1.0)


# =============================================================================
# Audit harness
# =============================================================================

def overlap_topk(a: list, b: list, k: int = 10) -> int:
    """Number of (dip, rally) pairs that appear in both lists' top-k."""
    set_a = set((round(c.dip_price, 2), round(c.rally_price, 2)) for c in a[:k])
    set_b = set((round(c.dip_price, 2), round(c.rally_price, 2)) for c in b[:k])
    return len(set_a & set_b)


def run_one_setup(setup: Setup) -> dict:
    """Run audit for a single synthetic setup. Returns row dict for the summary."""
    # MC paths
    paths = run_mc_joint_conditional(
        S0=setup.spot, sigma=setup.sigma, mu=setup.mu,
        horizon_days=HORIZON, n_paths=N_PATHS,
        vol_schedule=None,
        mean_reversion_strength=0.0,
        mean_reversion_anchor=None,
        seed=42,
    )

    # Grid scan (production function — returns candidates pre-filtered with -8pp slack)
    _best, candidates, _strict = scan_dip_rally_grid(
        S0=setup.spot, sigma=setup.sigma, mu=setup.mu,
        horizon_days=HORIZON, paths=paths,
        conviction_dip=CONVICTION_DIP,
        conviction_rally_cond=CONVICTION_RALLY_COND,
        capital_usd=10_000.0,
        spread_per_share_round_trip=SPREAD,
        vol_schedule=None,
        grid_step=GRID_STEP,
        grid_max_depth_pct=GRID_DEPTH,
        grid_max_reach_pct=GRID_REACH,
    )

    # Strict filter (drop the -8pp slack pre-filter)
    qualified = [
        c for c in candidates
        if c.p_dip_touched >= CONVICTION_DIP
           and c.p_rally_given_dip >= CONVICTION_RALLY_COND
    ]

    if len(qualified) < 10:
        return {
            "label": setup.label,
            "n_qualified": len(qualified),
            "skip": True,
            "skip_reason": "<10 qualified",
        }

    by_ev = sorted(qualified, key=rank_by_ev, reverse=True)
    by_prt = sorted(qualified, key=rank_by_p_round_trip, reverse=True)
    by_sharpe = sorted(qualified, key=rank_by_sharpe_equiv, reverse=True)

    overlap_ev_p = overlap_topk(by_ev, by_prt, k=10)
    overlap_ev_sharpe = overlap_topk(by_ev, by_sharpe, k=10)
    overlap_p_sharpe = overlap_topk(by_prt, by_sharpe, k=10)

    ev_top = by_ev[0]
    p_top = by_prt[0]

    return {
        "label": setup.label,
        "spot": setup.spot,
        "sigma": setup.sigma,
        "mu": setup.mu,
        "n_qualified": len(qualified),
        "overlap_ev_p": overlap_ev_p,
        "overlap_ev_sharpe": overlap_ev_sharpe,
        "overlap_p_sharpe": overlap_p_sharpe,
        "ev_pick": {
            "dip": ev_top.dip_price,
            "rally": ev_top.rally_price,
            "dip_pct": (setup.spot - ev_top.dip_price) / setup.spot * 100,
            "rally_pct": (ev_top.rally_price - setup.spot) / setup.spot * 100,
            "p_dip": ev_top.p_dip_touched,
            "p_rally_cond": ev_top.p_rally_given_dip,
            "p_round_trip": ev_top.p_round_trip,
            "ev": ev_top.net_expected_value,
            "gain_per_share": ev_top.expected_gain_per_share,
        },
        "p_pick": {
            "dip": p_top.dip_price,
            "rally": p_top.rally_price,
            "dip_pct": (setup.spot - p_top.dip_price) / setup.spot * 100,
            "rally_pct": (p_top.rally_price - setup.spot) / setup.spot * 100,
            "p_dip": p_top.p_dip_touched,
            "p_rally_cond": p_top.p_rally_given_dip,
            "p_round_trip": p_top.p_round_trip,
            "ev": p_top.net_expected_value,
            "gain_per_share": p_top.expected_gain_per_share,
        },
        "skip": False,
    }


def print_header():
    print()
    print("=" * 100)
    print("DIP×RALLY RANKER AUDIT (independent diagnostic per sibling-engine briefing)")
    print("=" * 100)
    print(f"  Setups: {len(SETUPS)} synthetic shapes covering σ × drift spectrum")
    print(f"  MC paths per setup: {N_PATHS:,}  Horizon: {HORIZON}d  Spread: ${SPREAD:.0f}")
    print(f"  Conviction filter: dip ≥{CONVICTION_DIP:.0%}, rally-cond ≥{CONVICTION_RALLY_COND:.0%}")
    print(f"  Grid: spot×{1-GRID_DEPTH:.2f} → spot×{1+GRID_REACH:.2f} in ${GRID_STEP:.0f} steps")
    print()


def print_per_setup(rows: list):
    print("=" * 100)
    print("PER-SETUP RESULTS")
    print("=" * 100)
    print(f"  {'Setup':<32} {'Qual':>5} {'EV∩P':>5} {'EV∩Sh':>6} {'P∩Sh':>5}")
    print(f"  {'-'*32} {'-'*5} {'-'*5} {'-'*6} {'-'*5}")
    for r in rows:
        if r.get("skip"):
            print(f"  {r['label']:<32} {r['n_qualified']:>5}   (skipped: {r['skip_reason']})")
            continue
        print(f"  {r['label']:<32} {r['n_qualified']:>5} {r['overlap_ev_p']:>3}/10 "
              f"{r['overlap_ev_sharpe']:>4}/10 {r['overlap_p_sharpe']:>3}/10")
    print()


def print_pick_comparison(rows: list):
    print("=" * 100)
    print("EV-PICK vs P-PICK COMPARISON  (does each ranker pick the same region?)")
    print("=" * 100)
    print(f"  {'Setup':<32} {'EV pick':>40} {'P pick':>40}")
    print(f"  {'-'*32} {'-'*40} {'-'*40}")
    for r in rows:
        if r.get("skip"):
            continue
        ev = r["ev_pick"]
        p = r["p_pick"]
        ev_str = (f"dip -{ev['dip_pct']:4.1f}% rally +{ev['rally_pct']:4.1f}% "
                  f"P_RT={ev['p_round_trip']:.0%} EV=${ev['ev']:+5.0f}")
        p_str = (f"dip -{p['dip_pct']:4.1f}% rally +{p['rally_pct']:4.1f}% "
                 f"P_RT={p['p_round_trip']:.0%} EV=${p['ev']:+5.0f}")
        print(f"  {r['label']:<32} {ev_str:>40} {p_str:>40}")
    print()


def print_interpretation(rows: list):
    valid = [r for r in rows if not r.get("skip")]
    if not valid:
        print("INTERPRETATION: No valid setups — investigate why no candidates qualify at 65/75 thresholds.")
        return

    overlaps_ev_p = [r["overlap_ev_p"] for r in valid]
    mean_overlap = sum(overlaps_ev_p) / len(overlaps_ev_p)
    min_overlap = min(overlaps_ev_p)
    max_overlap = max(overlaps_ev_p)

    # Average rally-distance divergence
    rally_diffs = [r["ev_pick"]["rally_pct"] - r["p_pick"]["rally_pct"] for r in valid]
    mean_rally_diff = sum(rally_diffs) / len(rally_diffs)

    print("=" * 100)
    print("INTERPRETATION (per briefing)")
    print("=" * 100)
    print(f"  Mean EV∩P top-10 overlap: {mean_overlap:.1f}/10  "
          f"(min {min_overlap}/10, max {max_overlap}/10)")
    print(f"  EV picks rally distance vs P picks: +{mean_rally_diff:+.1f}pp on average")
    print(f"    (positive = EV picks farther rally; would indicate jackpot bias)")
    print()

    if mean_overlap >= 8:
        print("  VERDICT: 8-10/10 → EV-as-ranker is ALIGNED with hit-rate ranking.")
        print("           No architectural change needed. This engine's two-stage filter")
        print("           (65/75 thresholds + EV ranker) functions equivalently to")
        print("           a P-ranker on this candidate space.")
    elif mean_overlap >= 3:
        print("  VERDICT: 3-7/10 → PARTIAL MISALIGNMENT.")
        print("           Per briefing: investigate calibration before architecture change.")
        print("           Specifically check: (1) is EV ranker pushing rally targets to")
        print("           the 75%-conditional-threshold boundary? (2) would a P-ranker")
        print("           with an EV floor give substantively different picks?")
    else:
        print("  VERDICT: 0-2/10 → STRUCTURAL MISALIGNMENT.")
        print("           Per briefing: two-stage ranker fix warranted.")
        print("           Recommended change: among 65/75-qualifying candidates,")
        print("           rank by p_round_trip (with EV floor for tie-break) instead")
        print("           of net_expected_value.")
    print()


def main():
    print_header()

    rows = []
    for i, setup in enumerate(SETUPS, 1):
        print(f"[{i}/{len(SETUPS)}] {setup.label}... ", end="", flush=True)
        row = run_one_setup(setup)
        if row.get("skip"):
            print(f"skipped ({row['skip_reason']})")
        else:
            print(f"qualified={row['n_qualified']}, "
                  f"EV∩P={row['overlap_ev_p']}/10, "
                  f"EV∩Sharpe={row['overlap_ev_sharpe']}/10")
        rows.append(row)

    print()
    print_per_setup(rows)
    print_pick_comparison(rows)
    print_interpretation(rows)


if __name__ == "__main__":
    main()
