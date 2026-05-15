"""
swing_analyzer.py — Standalone swing-trade Monte Carlo analyzer

Standalone tool, NOT part of the daily cron. Use ad-hoc when considering
a discretionary trade outside the monthly DCA flow.

Mirrors src/monte_carlo.py mechanics (GBM, GARCH(1,1) vol, enrichment drift)
but does NOT touch any sacred file.

Usage:
    export FMP_API_KEY=xxx
    python3 tools/swing_analyzer.py SNDK \\
        --entry 1490 --shares 10 \\
        --target 1600 --stop 1181 \\
        --horizon 60

Outputs (printed and saved to tools/output/swing_<TICKER>_<YYYYMMDD>.txt):
    - Calibrated sigma (GARCH) and mu (capped historical drift)
    - Touch probability table around target/stop
    - Path classification (target-first vs stop-first vs neither)
    - Expected value of user-supplied plan + 4 nearby alternatives
    - Sensitivity sweep across 7 alternative regimes
    - Terminal price distribution
    - One-line verdict
"""
import argparse
import os
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy.optimize import minimize

FMP_BASE = "https://financialmodelingprep.com/stable"
DEFAULT_LOOKBACK_DAYS = 730
DEFAULT_HORIZON = 60
DEFAULT_PATHS = 10_000


def fetch_history(ticker, api_key, lookback_days):
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    url = f"{FMP_BASE}/historical-price-eod/full"
    params = {"symbol": ticker, "from": start, "to": end, "apikey": api_key}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"No FMP history for {ticker}")
    df = pd.DataFrame(data)
    df = df.rename(columns={"date": "Date", "close": "Close",
                            "open": "Open", "high": "High",
                            "low": "Low", "volume": "Volume"})
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    return df


def fit_garch_11(returns):
    """GARCH(1,1) one-step-ahead variance forecast.
    Mirrors src/garch_model.py — keep formula in sync if either changes."""
    r = returns.replace([np.inf, -np.inf], np.nan).dropna()
    if len(r) < 50:
        return r.var()

    def neg_ll(params):
        omega, alpha, beta = params
        if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 1:
            return 1e10
        T = len(r)
        s2 = np.zeros(T)
        s2[0] = r.var()
        for t in range(1, T):
            s2[t] = omega + alpha * r.iloc[t-1]**2 + beta * s2[t-1]
        return 0.5 * np.sum(np.log(2 * np.pi * s2) + r.values**2 / s2)

    try:
        res = minimize(neg_ll, [0.01, 0.05, 0.90], method="L-BFGS-B",
                       bounds=[(1e-6, 1), (0, 1), (0, 1)])
        omega, alpha, beta = res.x
        last_var = r.tail(20).var()
        return omega + alpha * r.iloc[-1]**2 + beta * last_var
    except Exception:
        return r.tail(90).var()


def compute_rsi_14(closes):
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else 50.0


def enrichment_drift(rsi, mom_5d):
    rsi_drift = (50.0 - rsi) / 500.0
    mom_drift = -mom_5d / 1000.0
    return max(-0.10, min(0.10, rsi_drift + mom_drift))


def run_mc(s0, sigma_annual, mu_annual, horizon, n_paths, seed=42):
    np.random.seed(seed)
    sd = sigma_annual / np.sqrt(252.0)
    md = mu_annual / 252.0
    z = np.random.standard_normal((n_paths, horizon))
    log_rets = (md - 0.5 * sd**2) + sd * z
    paths = s0 * np.exp(np.cumsum(log_rets, axis=1))
    return np.hstack([np.full((n_paths, 1), s0), paths])


def touch_prob(paths, level, direction):
    arr = paths[:, 1:]
    if direction == "up":
        return float((arr >= level).any(axis=1).mean())
    return float((arr <= level).any(axis=1).mean())


def path_class(paths, up, dn):
    arr = paths[:, 1:]
    INF = 10**9
    up_idx = np.where((arr >= up).any(axis=1), np.argmax(arr >= up, axis=1), INF)
    dn_idx = np.where((arr <= dn).any(axis=1), np.argmax(arr <= dn, axis=1), INF)
    sell_first = (up_idx < dn_idx).mean()
    stop_first = (dn_idx < up_idx).mean()
    neither = ((up_idx == INF) & (dn_idx == INF))
    term = arr[neither, -1] if neither.any() else np.array([0.0])
    return sell_first, stop_first, neither.mean(), float(term.mean())


def ev(paths, entry, shares, sell, stop):
    ps, pst, pn, term_avg = path_class(paths, sell, stop)
    pnl_sell = (sell - entry) * shares
    pnl_stop = (stop - entry) * shares
    pnl_term = (term_avg - entry) * shares
    return ps * pnl_sell + pst * pnl_stop + pn * pnl_term, ps, pst, pn, pnl_term


def fmt_pct(x):
    return f"{x*100:5.1f}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ticker")
    ap.add_argument("--entry", type=float, required=True, help="Your entry price")
    ap.add_argument("--shares", type=int, required=True)
    ap.add_argument("--target", type=float, required=True, help="Sell-limit price")
    ap.add_argument("--stop", type=float, required=True, help="Hard-stop price")
    ap.add_argument("--horizon", type=int, default=DEFAULT_HORIZON, help="Trading days")
    ap.add_argument("--paths", type=int, default=DEFAULT_PATHS)
    ap.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    ap.add_argument("--drift-cap", type=float, default=1.0,
                    help="Max abs annualized drift from historical mean")
    args = ap.parse_args()

    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        sys.exit("ERROR: FMP_API_KEY not set in environment")

    out_lines = []

    def p(line=""):
        print(line)
        out_lines.append(line)

    p("=" * 76)
    p(f"SWING ANALYZER — {args.ticker}   {datetime.now():%Y-%m-%d %H:%M}")
    p("=" * 76)

    df = fetch_history(args.ticker, api_key, args.lookback_days)
    s0 = float(df["Close"].iloc[-1])
    p(f"Spot (last close):  ${s0:.2f}")
    p(f"History rows:       {len(df)}")
    p(f"Date range:         {df['Date'].iloc[0]:%Y-%m-%d} -> {df['Date'].iloc[-1]:%Y-%m-%d}")

    log_ret = np.log(df["Close"] / df["Close"].shift(1)).dropna()
    forecast_var = fit_garch_11(log_ret)
    sigma_annual = float(np.sqrt(forecast_var * 252))
    mu_hist_annual = float(log_ret.mean() * 252)
    mu_annual = max(-args.drift_cap, min(args.drift_cap, mu_hist_annual))

    rsi = compute_rsi_14(df["Close"])
    mom_5d = float(df["Close"].iloc[-1] / df["Close"].iloc[-6] - 1.0) if len(df) >= 6 else 0.0
    enr = enrichment_drift(rsi, mom_5d)
    mu_effective = mu_annual + enr * 252 / args.horizon

    p()
    p(f"Sigma (GARCH, annualized):   {sigma_annual*100:6.1f}%")
    p(f"Mu (historical, annualized): {mu_hist_annual*100:+6.1f}%  -> capped at "
      f"{mu_annual*100:+.1f}%")
    p(f"RSI(14):                     {rsi:6.1f}")
    p(f"5-day momentum:              {mom_5d*100:+6.2f}%")
    p(f"Enrichment drift (per-day):  {enr/args.horizon:+.5f}  "
      f"(applied as +{enr*252/args.horizon*100:+.1f}% annualized over horizon)")
    p()

    paths = run_mc(s0, sigma_annual, mu_effective, args.horizon, args.paths)

    p("=" * 76)
    p(f"USER PLAN — entry ${args.entry:.2f}, sell ${args.target:.2f}, "
      f"stop ${args.stop:.2f}, {args.shares} shares")
    p("=" * 76)
    e, ps, pst, pn, pt = ev(paths, args.entry, args.shares, args.target, args.stop)
    p(f"  P(target first):  {fmt_pct(ps)}   ->  ${(args.target - args.entry) * args.shares:+,.0f}")
    p(f"  P(stop first):    {fmt_pct(pst)}   ->  ${(args.stop - args.entry) * args.shares:+,.0f}")
    p(f"  P(neither):       {fmt_pct(pn)}   ->  ${pt:+,.0f} avg")
    p(f"  Expected value:   ${e:+,.0f}")
    p()

    p("=" * 76)
    p("NEARBY PLANS")
    p("=" * 76)
    p(f"{'Sell':>8} {'Stop':>8}  {'P(sell)':>9} {'P(stop)':>9} {'P(neither)':>11} {'EV':>10}")
    p("-" * 76)
    for sell_mult, stop_mult in [(1.0, 1.0), (0.97, 1.0), (1.03, 1.0),
                                  (1.0, 1.05), (1.0, 0.95)]:
        sell = args.target * sell_mult
        stop = args.stop * stop_mult
        e2, ps2, pst2, pn2, _ = ev(paths, args.entry, args.shares, sell, stop)
        p(f"  ${sell:>6.0f} ${stop:>6.0f}   {fmt_pct(ps2)}   {fmt_pct(pst2)}    "
          f"{fmt_pct(pn2)}    ${e2:+8,.0f}")
    p()

    p("=" * 76)
    p(f"TOUCH PROBABILITIES ({args.horizon} trading days)")
    p("=" * 76)
    up_levels = sorted({s0, args.entry, args.target * 0.95, args.target,
                        args.target * 1.05, args.target * 1.10})
    p(f"  {'UP level':>10}    P(touch)")
    for lvl in up_levels:
        p(f"  ${lvl:>8.2f}    {fmt_pct(touch_prob(paths, lvl, 'up'))}")
    p()
    dn_levels = sorted({args.stop * 1.10, args.stop * 1.05, args.stop,
                        args.stop * 0.95, args.stop * 0.85}, reverse=True)
    p(f"  {'DOWN level':>10}  P(touch)")
    for lvl in dn_levels:
        p(f"  ${lvl:>8.2f}    {fmt_pct(touch_prob(paths, lvl, 'down'))}")
    p()

    p("=" * 76)
    p(f"TERMINAL DISTRIBUTION (Day {args.horizon})")
    p("=" * 76)
    term = paths[:, -1]
    for pct in [10, 25, 40, 50, 60, 75, 90]:
        p(f"  p{pct:>2}   ${np.percentile(term, pct):>8.2f}")
    p(f"  mean ${term.mean():>8.2f}")
    p(f"  P(Day {args.horizon} >= entry ${args.entry:.0f}): "
      f"{fmt_pct(float((term >= args.entry).mean()))}")
    p(f"  P(Day {args.horizon} >= target ${args.target:.0f}): "
      f"{fmt_pct(float((term >= args.target).mean()))}")
    p()

    p("=" * 76)
    p("SENSITIVITY — alternative regimes")
    p("=" * 76)
    p(f"  {'Scenario':<42} {'P(target)':>10} {'P(stop)':>9} {'EV':>10}")
    p("  " + "-" * 73)
    scens = [
        ("Base (calibrated)", sigma_annual, mu_effective),
        ("Drift -5% annual", sigma_annual, mu_effective - 0.05),
        ("Drift -15% annual", sigma_annual, mu_effective - 0.15),
        ("Drift -25% annual (capitulation)", sigma_annual, mu_effective - 0.25),
        ("Vol +25% (regime change)", sigma_annual * 1.25, mu_effective),
        ("Vol -25% (vol crush)", sigma_annual * 0.75, mu_effective),
        ("Hostile: vol+25%, drift-15%", sigma_annual * 1.25, mu_effective - 0.15),
        ("Drift = 0 (premium dies)", sigma_annual, 0.0),
    ]
    for name, s, m in scens:
        ps2 = run_mc(s0, s, m, args.horizon, args.paths, seed=43)
        e3, pa, pb, _, _ = ev(ps2, args.entry, args.shares, args.target, args.stop)
        p(f"  {name:<42} {fmt_pct(pa):>10} {fmt_pct(pb):>9} ${e3:+8,.0f}")
    p()

    p("=" * 76)
    p("VERDICT")
    p("=" * 76)
    base_p_target = ps
    if e > 0 and base_p_target >= 0.55:
        verdict = f"POSITIVE EV (${e:+,.0f}) and P(target)>=55%. Plan is mathematically sound."
    elif e > -100 and base_p_target >= 0.50:
        verdict = f"NEAR-BREAKEVEN EV (${e:+,.0f}) with coin-flip win rate. Marginal."
    elif e < -500:
        verdict = f"NEGATIVE EV (${e:+,.0f}). Plan loses money in expectation. Reconsider."
    else:
        verdict = (f"NEGATIVE EV (${e:+,.0f}). Watch sensitivity rows — if hostile "
                   "scenarios dominate, cut.")
    p(f"  {verdict}")
    p()
    p(f"  Win/loss size ratio: "
      f"${(args.target - args.entry) * args.shares:+,.0f} win vs "
      f"${(args.stop - args.entry) * args.shares:+,.0f} stop  "
      f"= {abs((args.target - args.entry) / (args.stop - args.entry)):.2f}x")
    p()

    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"swing_{args.ticker}_{datetime.now():%Y%m%d}.txt"
    out_path.write_text("\n".join(out_lines) + "\n")
    print(f"Saved: {out_path}")

    summary = {
        "ticker": args.ticker, "timestamp": datetime.now().isoformat(),
        "spot": s0, "sigma_annual": sigma_annual, "mu_annual": mu_effective,
        "rsi": rsi, "mom_5d": mom_5d,
        "user_plan": {"entry": args.entry, "shares": args.shares,
                       "target": args.target, "stop": args.stop,
                       "p_target": ps, "p_stop": pst, "p_neither": pn, "ev": e},
    }
    json_path = out_dir / f"swing_{args.ticker}_{datetime.now():%Y%m%d}.json"
    json_path.write_text(json.dumps(summary, indent=2))
    print(f"Saved: {json_path}")


if __name__ == "__main__":
    main()
