#!/usr/bin/env python3
"""
dipnrally_backtest_recent.py — Empirical backtest of recent recommendations.

Walks the round_trip_history CSV, fetches actual price action since each
recommendation date, and classifies what actually happened:

  - ROUND-TRIP: dip touched, then rally touched before horizon (profit)
  - BAG-HOLD: dip touched, rally NOT touched within horizon (loss)
  - RALLY-FIRST: rally touched BEFORE dip (no entry, no P&L)
  - NEITHER: neither barrier touched (no trade)
  - PENDING: horizon not yet expired

Then compares against the model's predicted scenario probabilities to
show whether the model's calibration matches realized outcomes.

This is an INTERIM backtest — recommendations less than 60 days old
haven't fully expired so their final status may still change. We
report current status as of today's price action.

Usage:
    python3 tools/dipnrally_backtest_recent.py [TICKER]
    (defaults to SNDK if no ticker specified)
"""
from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from swing_analyzer_dipnrally import fetch_history, DEFAULT_LOOKBACK_DAYS  # type: ignore


def classify_outcome(prices, dip_target, rally_target, horizon_days, elapsed_days):
    """Walk daily highs and lows. Determine actual outcome.

    prices: list of dicts with Date, Close (and ideally High/Low — but the
            historical-price-eod/full endpoint returns Close-only on Starter
            plan, so we use Close as a conservative approximation).
    Returns: dict {outcome, day_dip_first_touched, day_rally_first_touched,
                   terminal_price, status}
    """
    dip_first_day = None
    rally_first_day = None

    for i, row in enumerate(prices):
        close = float(row["Close"])
        if dip_first_day is None and close <= dip_target:
            dip_first_day = i
        if rally_first_day is None and close >= rally_target:
            rally_first_day = i
        if dip_first_day is not None and rally_first_day is not None:
            break

    terminal_price = float(prices[-1]["Close"]) if prices else None
    days_observed = len(prices) - 1  # number of days after rec_date

    if dip_first_day is not None and rally_first_day is not None:
        if dip_first_day < rally_first_day:
            outcome = "ROUND-TRIP ✓"
        elif rally_first_day < dip_first_day:
            outcome = "RALLY-FIRST (no entry)"
        else:
            outcome = "SAME-DAY-BOTH (ambiguous → rally-first conservative)"
    elif dip_first_day is not None and rally_first_day is None:
        if days_observed >= horizon_days:
            outcome = "BAG-HOLD ✗ (dip filled, rally never)"
        else:
            outcome = "PENDING (dip filled, rally pending)"
    elif rally_first_day is not None and dip_first_day is None:
        outcome = "RALLY-FIRST (no entry, no P&L)"
    else:
        if days_observed >= horizon_days:
            outcome = "NEITHER (no entry)"
        else:
            outcome = "PENDING (no barriers touched yet)"

    return {
        "outcome": outcome,
        "dip_first_day": dip_first_day,
        "rally_first_day": rally_first_day,
        "terminal_price": terminal_price,
        "days_observed": days_observed,
    }


def compute_hypothetical_pnl(dip_target, rally_target, outcome_result, capital=10000.0, spread=2.0):
    """If the user had placed the limit orders, what would the actual P&L be?"""
    o = outcome_result["outcome"]
    if "ROUND-TRIP" in o:
        shares = capital / dip_target
        gain_per_share = rally_target - dip_target - spread
        return shares * gain_per_share, "win"
    elif "BAG-HOLD" in o:
        shares = capital / dip_target
        loss_per_share = dip_target - outcome_result["terminal_price"]
        return -shares * loss_per_share, "loss"
    elif "RALLY-FIRST" in o or "SAME-DAY" in o:
        return 0.0, "no entry"
    elif "NEITHER" in o:
        return 0.0, "no entry"
    else:  # PENDING
        if outcome_result["dip_first_day"] is not None:
            # Dip already filled, unrealized
            shares = capital / dip_target
            unrealized = (outcome_result["terminal_price"] - dip_target) * shares
            return unrealized, "unrealized"
        return 0.0, "pending"


def main():
    ticker = (sys.argv[1] if len(sys.argv) > 1 else "SNDK").upper()
    api_key = os.getenv("FMP_API_KEY")
    if not api_key:
        print("ERROR: FMP_API_KEY not set")
        return 1

    history_csv = Path(__file__).parent / "output" / f"round_trip_history_{ticker}.csv"
    if not history_csv.exists():
        print(f"ERROR: no history found at {history_csv}")
        print(f"       Run `python3 tools/swing_analyzer_dipnrally.py {ticker}` at least once to build history.")
        return 1

    with open(history_csv, "r") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print(f"ERROR: history CSV is empty")
        return 1

    print(f"\nBacktest: {ticker} recent recommendations from {history_csv.name}")
    print(f"  {len(rows)} CSV rows found\n")

    # Fetch full price history covering all recommendation dates + horizon window
    earliest_date = min(r["date"] for r in rows)
    earliest_dt = datetime.strptime(earliest_date, "%Y-%m-%d")
    days_back = max(DEFAULT_LOOKBACK_DAYS, (datetime.now() - earliest_dt).days + 70)
    print(f"  Fetching {days_back} days of {ticker} history from FMP...")
    history_df = fetch_history(ticker, api_key, days_back)
    price_records = history_df.to_dict("records")
    # Normalise dates to YYYY-MM-DD strings for matching
    for r in price_records:
        r["Date"] = r["Date"].strftime("%Y-%m-%d") if hasattr(r["Date"], "strftime") else str(r["Date"])[:10]

    print(f"  Price history: {len(price_records)} bars, "
          f"{price_records[0]['Date']} → {price_records[-1]['Date']}\n")

    # Per-recommendation analysis
    print("=" * 110)
    print(f"{'Rec Date':<12} {'Spot@rec':>10} {'Dip→Rally':>16} {'Predicted':>15} {'Actual outcome':<32} {'P&L if traded':>14}")
    print(f"{'='*12} {'='*10} {'='*16} {'='*15} {'='*32} {'='*14}")

    summary = {"round_trip": 0, "bag_hold": 0, "rally_first": 0, "neither": 0, "pending": 0}
    total_pnl = 0.0
    realized_count = 0

    for rec in rows:
        rec_date = rec["date"]
        if not rec.get("recommended_dip") or not rec.get("recommended_rally"):
            continue

        try:
            spot_at_rec = float(rec["spot"])
            dip = float(rec["recommended_dip"])
            rally = float(rec["recommended_rally"])
            p_rt = float(rec.get("p_round_trip", 0))
            p_bh = float(rec.get("p_bag_hold", 0))
            p_rf = float(rec.get("p_no_trade_rally_first", 0))
            horizon = int(rec.get("horizon_days", 60))
            net_ev = float(rec.get("net_expected_value", 0))
        except (ValueError, TypeError):
            continue

        # Slice prices from rec_date forward (excluding rec_date itself since that's the
        # close used as spot — subsequent action starts from rec_date+1)
        future_prices = [p for p in price_records if p["Date"] > rec_date]
        if not future_prices:
            outcome = "no data after rec date"
            print(f"{rec_date:<12} ${spot_at_rec:>9.0f} ${dip:>5.0f}→${rally:<5.0f} "
                  f"P_RT={p_rt:.0%}  {'no data':<32} {'n/a':>14}")
            continue

        result = classify_outcome(future_prices, dip, rally, horizon, len(future_prices))
        pnl, pnl_kind = compute_hypothetical_pnl(dip, rally, result)

        # Track summary
        o = result["outcome"]
        if "ROUND-TRIP" in o:
            summary["round_trip"] += 1
            realized_count += 1
            total_pnl += pnl
        elif "BAG-HOLD" in o:
            summary["bag_hold"] += 1
            realized_count += 1
            total_pnl += pnl
        elif "RALLY-FIRST" in o or "SAME-DAY" in o:
            summary["rally_first"] += 1
            realized_count += 1
        elif "NEITHER" in o:
            summary["neither"] += 1
            realized_count += 1
        else:  # PENDING
            summary["pending"] += 1

        predicted_str = f"P(RT)={p_rt:.0%},P(BH)={p_bh:.0%}"
        pnl_str = f"${pnl:+.0f} ({pnl_kind})"
        print(f"{rec_date:<12} ${spot_at_rec:>9.0f} ${dip:>5.0f}→${rally:<5.0f} "
              f"{predicted_str:>15} {result['outcome']:<32} {pnl_str:>14}")

    print()
    print("=" * 110)
    print("SUMMARY")
    print("=" * 110)
    n = sum(summary.values())
    print(f"  Total recommendations:  {n}")
    print(f"  Round-trip completed:   {summary['round_trip']:>3}  ({summary['round_trip']/max(n,1):.0%})")
    print(f"  Bag-hold (loss):        {summary['bag_hold']:>3}  ({summary['bag_hold']/max(n,1):.0%})")
    print(f"  Rally-first (no entry): {summary['rally_first']:>3}  ({summary['rally_first']/max(n,1):.0%})")
    print(f"  Neither touched:        {summary['neither']:>3}  ({summary['neither']/max(n,1):.0%})")
    print(f"  Still pending:          {summary['pending']:>3}  ({summary['pending']/max(n,1):.0%})")
    print()
    print(f"  Total hypothetical P&L if user had traded every recommendation: ${total_pnl:+.0f}")
    print(f"  (Computed from {realized_count} fully-resolved or rally-first trades)")
    print()

    # Calibration check: how well do model's average probabilities match realized?
    realized_total = realized_count
    if realized_total > 0:
        print("=" * 110)
        print("MODEL CALIBRATION CHECK")
        print("=" * 110)
        avg_predicted_rt = sum(
            float(r.get("p_round_trip", 0)) for r in rows
            if r.get("recommended_dip") and r.get("recommended_rally")
        ) / max(len(rows), 1)
        actual_rt_rate = summary["round_trip"] / realized_total
        print(f"  Model's average predicted P(round-trip):  {avg_predicted_rt:.0%}")
        print(f"  Actual realized round-trip rate:          {actual_rt_rate:.0%}  ({summary['round_trip']}/{realized_total})")
        if abs(avg_predicted_rt - actual_rt_rate) <= 0.15:
            print(f"  → Within ±15pp of predicted (acceptable for small N)")
        else:
            print(f"  → Material divergence (small N — would need 30+ samples for confidence)")
        print()

    print("  NOTE: Most recommendations are still within their 60-day horizon (pending).")
    print("        Calibration of joint probabilities requires N≥30 resolved trades.")
    print("        Current N is too small for statistical confidence — interpret directionally only.")
    print()


if __name__ == "__main__":
    sys.exit(main() or 0)
