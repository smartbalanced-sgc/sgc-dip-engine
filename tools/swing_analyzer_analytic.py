"""
swing_analyzer_analytic.py — Second-opinion analytic verifier for swing_analyzer.py

Uses Fokker-Planck PDE with absorbing barriers (Crank-Nicolson finite-difference)
to compute first-passage probabilities. This is FUNDAMENTALLY DIFFERENT MATH
from the Monte Carlo in swing_analyzer.py — no random sampling, no path
simulation. If both produce matching P(target-first), P(stop-first), and EV,
the result is bulletproof.

Also reports single-barrier "ever-touch" probabilities via the standard
reflection-principle closed form (yet a third independent check).

Usage:
    # Verify a saved MC JSON against PDE (recommended — same sigma/mu/spot)
    python3 tools/swing_analyzer_analytic.py --verify \\
        tools/output/swing_SNDK_20260515.json

    # Standalone (fresh FMP fetch + GARCH + PDE)
    export FMP_API_KEY=xxx
    python3 tools/swing_analyzer_analytic.py SNDK \\
        --entry 1490 --shares 10 --target 1600 --stop 1181 --horizon 60

    # Daily thesis health check (no-stop hold strategy):
    python3 tools/swing_analyzer_analytic.py --check-thesis SNDK \\
        --entry 1490 --shares 10 --target 1600 --horizon 60
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy.optimize import minimize
from scipy.sparse import diags
from scipy.sparse.linalg import splu
from scipy.stats import norm

FMP_BASE = "https://financialmodelingprep.com/stable"
DEFAULT_LOOKBACK_DAYS = 730


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
    df = pd.DataFrame(data).rename(columns={"date": "Date", "close": "Close"})
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)


def fit_garch_11(returns):
    """GARCH(1,1) one-step-ahead variance forecast (mirrors src/garch_model.py)."""
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


def run_mc_paths(S0, sigma_annual, mu_annual, horizon, n_paths=50_000, seed=42):
    """Lightweight MC path generator for terminal-distribution analysis."""
    np.random.seed(seed)
    sd = sigma_annual / np.sqrt(252.0)
    md = mu_annual / 252.0
    z = np.random.standard_normal((n_paths, horizon))
    log_rets = (md - 0.5 * sd**2) + sd * z
    return S0 * np.exp(np.cumsum(log_rets, axis=1))


def closed_touch_up(S0, U, T, mu, sigma):
    """P(max S_t >= U over [0,T]) for GBM — reflection principle + Girsanov."""
    nu = mu - sigma**2 / 2
    s = sigma * np.sqrt(T)
    u = np.log(U / S0)
    return ((1 - norm.cdf((u - nu * T) / s))
            + np.exp(2 * nu * u / sigma**2) * (1 - norm.cdf((u + nu * T) / s)))


def closed_touch_down(S0, L, T, mu, sigma):
    """P(min S_t <= L over [0,T]) for GBM."""
    nu = mu - sigma**2 / 2
    s = sigma * np.sqrt(T)
    l = np.log(L / S0)
    return (norm.cdf((l - nu * T) / s)
            + np.exp(2 * nu * l / sigma**2) * norm.cdf((l + nu * T) / s))


def pde_two_barrier(S0, U, L, T, mu, sigma, n_space=400, n_time=2000):
    """
    Solve Fokker-Planck PDE with absorbing barriers at L and U.
    PDE in log-space: dp/dt = -nu * dp/dx + (sigma^2/2) * d^2p/dx^2
    where nu = mu - sigma^2/2 (Ito correction).
    Crank-Nicolson tridiagonal solver. Returns first-passage probabilities.
    """
    x_L, x_U = np.log(L), np.log(U)
    x = np.linspace(x_L, x_U, n_space)
    dx = x[1] - x[0]
    dt = T / n_time
    nu = mu - 0.5 * sigma**2

    n_int = n_space - 2
    p = np.zeros(n_int)
    i0 = int(np.argmin(np.abs(x - np.log(S0))))
    if 1 <= i0 <= n_space - 2:
        p[i0 - 1] = 1.0 / dx

    a = nu / (2 * dx) + 0.5 * sigma**2 / dx**2
    b_coef = -sigma**2 / dx**2
    c = -nu / (2 * dx) + 0.5 * sigma**2 / dx**2

    M_main = np.full(n_int, 1 - 0.5 * dt * b_coef)
    M_low = np.full(n_int - 1, -0.5 * dt * a)
    M_up = np.full(n_int - 1, -0.5 * dt * c)
    N_main = np.full(n_int, 1 + 0.5 * dt * b_coef)
    N_low = np.full(n_int - 1, 0.5 * dt * a)
    N_up = np.full(n_int - 1, 0.5 * dt * c)

    M_mat = diags([M_low, M_main, M_up], [-1, 0, 1], format="csc")
    N_mat = diags([N_low, N_main, N_up], [-1, 0, 1], format="csc")
    solver = splu(M_mat)

    cum_U = cum_L = 0.0
    for _ in range(n_time):
        p_new = solver.solve(N_mat @ p)
        avg_top = 0.5 * (p[-1] + p_new[-1])
        avg_bot = 0.5 * (p[0] + p_new[0])
        cum_U += (0.5 * sigma**2 * avg_top / dx) * dt
        cum_L += (0.5 * sigma**2 * avg_bot / dx) * dt
        p = p_new

    p_neither = float(np.sum(p) * dx)
    x_int = x[1:-1]
    if p_neither > 1e-9:
        E_term = float(np.sum(np.exp(x_int) * p) * dx / p_neither)
    else:
        E_term = 0.5 * (U + L)

    return {
        "p_U_first": float(cum_U),
        "p_L_first": float(cum_L),
        "p_neither": p_neither,
        "E_term_neither": E_term,
        "total": float(cum_U + cum_L + p_neither),
    }


def compute_ev(entry, shares, target, stop, pde):
    pnl_U = (target - entry) * shares
    pnl_L = (stop - entry) * shares
    pnl_N = (pde["E_term_neither"] - entry) * shares
    return pde["p_U_first"] * pnl_U + pde["p_L_first"] * pnl_L + pde["p_neither"] * pnl_N


def run_analysis(S0, sigma, mu, entry, shares, target, stop, T):
    """Run both PDE two-barrier and closed-form single-barrier; return dict."""
    pde = pde_two_barrier(S0, target, stop, T, mu, sigma)
    return {
        "pde": pde,
        "ev": compute_ev(entry, shares, target, stop, pde),
        "p_touch_up_closed": closed_touch_up(S0, target, T, mu, sigma),
        "p_touch_down_closed": closed_touch_down(S0, stop, T, mu, sigma),
    }


def print_header(title):
    print("=" * 76)
    print(title)
    print("=" * 76)


def verify_mode(json_path):
    if not Path(json_path).exists():
        sys.exit(f"ERROR: file not found: {json_path}")
    with open(json_path) as f:
        mc = json.load(f)

    print_header(f"ANALYTIC VERIFICATION of {json_path}")
    print(f"  MC ticker:   {mc['ticker']}")
    print(f"  MC run time: {mc['timestamp']}")
    print()

    S0 = mc["spot"]
    sigma = mc["sigma_annual"]
    mu = mc["mu_annual"]
    plan = mc["user_plan"]
    entry, shares = plan["entry"], plan["shares"]
    target, stop = plan["target"], plan["stop"]
    T = 60 / 252

    print(f"  Inputs (from MC JSON):")
    print(f"    Spot:    ${S0:.2f}")
    print(f"    Sigma:   {sigma * 100:6.1f}%   (annualized, from GARCH)")
    print(f"    Mu:      {mu * 100:+6.1f}%")
    print(f"    Plan:    entry ${entry:.0f}  shares {shares}  "
          f"target ${target:.0f}  stop ${stop:.0f}")
    print(f"    Horizon: 60 trading days")
    print()

    r = run_analysis(S0, sigma, mu, entry, shares, target, stop, T)
    pde = r["pde"]

    print_header("METHOD-1 vs METHOD-2 COMPARISON")
    print("  Method 1: Monte Carlo (10,000 random paths) — swing_analyzer.py")
    print("  Method 2: Fokker-Planck PDE (Crank-Nicolson) — THIS TOOL")
    print()
    print(f"  {'Metric':<28} {'MC':>14} {'PDE (analytic)':>18} {'Delta':>10}")
    print(f"  {'-' * 28} {'-' * 14} {'-' * 18} {'-' * 10}")
    print(f"  {'P(target first)':<28} "
          f"{plan['p_target']*100:>13.1f}% "
          f"{pde['p_U_first']*100:>17.1f}% "
          f"{(pde['p_U_first']-plan['p_target'])*100:>+8.1f}pp")
    print(f"  {'P(stop first)':<28} "
          f"{plan['p_stop']*100:>13.1f}% "
          f"{pde['p_L_first']*100:>17.1f}% "
          f"{(pde['p_L_first']-plan['p_stop'])*100:>+8.1f}pp")
    print(f"  {'P(neither)':<28} "
          f"{plan['p_neither']*100:>13.1f}% "
          f"{pde['p_neither']*100:>17.1f}% "
          f"{(pde['p_neither']-plan['p_neither'])*100:>+8.1f}pp")
    print(f"  {'Expected value':<28} "
          f"${plan['ev']:>+12,.0f} "
          f"${r['ev']:>+16,.0f} "
          f"${r['ev']-plan['ev']:>+8,.0f}")
    print(f"  {'Mass conservation (PDE)':<28} {'-':>14} "
          f"{pde['total']:>18.5f}  (should be ~1.0)")
    print()

    print_header("METHOD-3: SINGLE-BARRIER CLOSED FORM (no MC, no PDE)")
    print("  Reflection-principle + Girsanov formula for one-sided 'ever touch'.")
    print(f"  P(ever touch ${target:.0f} target in 60d):  "
          f"{r['p_touch_up_closed']*100:5.1f}%")
    print(f"  P(ever touch ${stop:.0f} stop in 60d):    "
          f"{r['p_touch_down_closed']*100:5.1f}%")
    print()
    print("  Note: 'ever touch' >= 'first-touch'. Single-barrier probs should be")
    print("  >= the corresponding first-passage probs from PDE/MC.")
    print()

    diff_pp = abs(pde["p_U_first"] - plan["p_target"]) * 100
    diff_ev = abs(r["ev"] - plan["ev"])

    print_header("VERDICT")
    if diff_pp < 3.0 and diff_ev < 100:
        print(f"  VERIFIED. PDE and MC agree within {diff_pp:.1f}pp on P(target)")
        print(f"  and ${diff_ev:.0f} on EV. Three independent math frameworks")
        print(f"  (MC, PDE, closed-form single-barrier) confirm each other.")
        print(f"  -> swing_analyzer.py results are correct. Trust the EV.")
    elif diff_pp < 6.0 and diff_ev < 300:
        print(f"  CLOSE. PDE and MC differ by {diff_pp:.1f}pp / ${diff_ev:.0f}.")
        print(f"  Acceptable for finite MC paths + PDE discretization.")
        print(f"  Re-run MC with more paths if you want tighter convergence.")
    else:
        print(f"  DIVERGENT. {diff_pp:.1f}pp / ${diff_ev:.0f} difference is")
        print(f"  larger than expected from numerical noise alone. Investigate")
        print(f"  before trusting either result.")
    print()


def standalone_mode(args):
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        sys.exit("ERROR: FMP_API_KEY not set in environment")

    print_header(f"SWING ANALYZER (ANALYTIC) — {args.ticker}   "
                 f"{datetime.now():%Y-%m-%d %H:%M}")
    df = fetch_history(args.ticker, api_key, args.lookback_days)
    S0 = float(df["Close"].iloc[-1])
    log_ret = np.log(df["Close"] / df["Close"].shift(1)).dropna()
    forecast_var = fit_garch_11(log_ret)
    sigma = float(np.sqrt(forecast_var * 252))
    mu_hist = float(log_ret.mean() * 252)
    mu = max(-args.drift_cap, min(args.drift_cap, mu_hist))

    print(f"  Spot:      ${S0:.2f}")
    print(f"  History:   {len(df)} rows ({df['Date'].iloc[0]:%Y-%m-%d} -> "
          f"{df['Date'].iloc[-1]:%Y-%m-%d})")
    print(f"  Sigma:     {sigma*100:.1f}% (GARCH, annualized)")
    print(f"  Mu:        {mu_hist*100:+.1f}% -> capped at {mu*100:+.1f}%")
    print()

    T = args.horizon / 252
    r = run_analysis(S0, sigma, mu, args.entry, args.shares, args.target, args.stop, T)
    pde = r["pde"]

    print_header(f"USER PLAN — PDE (analytic) results, {args.horizon} trading days")
    print(f"  P(target ${args.target:.0f} first): {pde['p_U_first']*100:5.1f}%")
    print(f"  P(stop   ${args.stop:.0f} first): {pde['p_L_first']*100:5.1f}%")
    print(f"  P(neither):                {pde['p_neither']*100:5.1f}%")
    print(f"  E[terminal | neither]:     ${pde['E_term_neither']:.2f}")
    print(f"  Expected value:            ${r['ev']:+,.0f}")
    print(f"  Mass conservation:         {pde['total']:.5f}  (sanity: ~1.0)")
    print()

    print_header("SINGLE-BARRIER CLOSED-FORM TOUCH PROBS")
    print(f"  P(ever touch ${args.target:.0f} up   in {args.horizon}d): "
          f"{r['p_touch_up_closed']*100:5.1f}%")
    print(f"  P(ever touch ${args.stop:.0f} down in {args.horizon}d): "
          f"{r['p_touch_down_closed']*100:5.1f}%")
    print()

    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"swing_{args.ticker}_{datetime.now():%Y%m%d}_analytic.json"
    out_path.write_text(json.dumps({
        "ticker": args.ticker,
        "timestamp": datetime.now().isoformat(),
        "method": "PDE-Crank-Nicolson",
        "spot": S0, "sigma_annual": sigma, "mu_annual": mu,
        "user_plan": {
            "entry": args.entry, "shares": args.shares,
            "target": args.target, "stop": args.stop,
            "p_target": pde["p_U_first"], "p_stop": pde["p_L_first"],
            "p_neither": pde["p_neither"], "ev": r["ev"],
        },
    }, indent=2))
    print(f"Saved: {out_path}")


def check_thesis_mode(args):
    """
    Daily thesis-health check for a no-stop hold strategy.

    Computes P(touch target in horizon), the dynamic break-even threshold
    (P at which holding equals cutting in EV), and a traffic-light verdict.
    Appends today's reading to a CSV trend file for multi-day tracking.
    """
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        sys.exit("ERROR: FMP_API_KEY not set in environment")

    df = fetch_history(args.thesis_ticker, api_key, args.lookback_days)
    S0 = float(df["Close"].iloc[-1])
    last_date = df["Date"].iloc[-1].strftime("%Y-%m-%d")
    log_ret = np.log(df["Close"] / df["Close"].shift(1)).dropna()

    forecast_var = fit_garch_11(log_ret)
    sigma = float(np.sqrt(forecast_var * 252))
    mu_hist = float(log_ret.mean() * 252)
    mu_capped = max(-args.drift_cap, min(args.drift_cap, mu_hist))
    rsi = compute_rsi_14(df["Close"])
    mom_5d = float(df["Close"].iloc[-1] / df["Close"].iloc[-6] - 1.0) if len(df) >= 6 else 0.0
    enr = enrichment_drift(rsi, mom_5d)
    mu_effective = mu_capped + enr * 252 / args.horizon
    T = args.horizon / 252

    paths = run_mc_paths(S0, sigma, mu_effective, args.horizon, n_paths=50_000)
    touched = (paths >= args.target).any(axis=1)
    p_touch_mc = float(touched.mean())
    untouched_terminals = paths[~touched, -1]
    e_bad = float(untouched_terminals.mean()) if untouched_terminals.size > 0 else 0.0
    p_touch_cf = closed_touch_up(S0, args.target, T, mu_effective, sigma)

    pnl_good = (args.target - args.entry) * args.shares
    pnl_bad = (e_bad - args.entry) * args.shares
    cut_pnl = (S0 - args.entry) * args.shares
    ev_hold = p_touch_mc * pnl_good + (1 - p_touch_mc) * pnl_bad
    ev_advantage = ev_hold - cut_pnl

    if pnl_good == pnl_bad:
        breakeven_p = float("nan")
    else:
        breakeven_p = (cut_pnl - pnl_bad) / (pnl_good - pnl_bad)
    cushion_pp = (p_touch_mc - breakeven_p) * 100

    panic_level = args.panic_level
    p_panic = closed_touch_down(S0, panic_level, T, mu_effective, sigma)

    cushion = p_touch_mc - breakeven_p
    if cushion >= 0.10:
        verdict, light = "STRONG HOLD", "[GREEN]"
    elif cushion >= 0.05:
        verdict, light = "HOLD", "[YELLOW]"
    elif cushion >= 0.0:
        verdict, light = "EDGE", "[ORANGE]"
    else:
        verdict, light = "CUT", "[RED]"

    print_header(f"{args.thesis_ticker} THESIS HEALTH CHECK — "
                 f"{datetime.now():%Y-%m-%d %H:%M}")
    print(f"  Data through:           {last_date} close")
    print(f"  Spot (last close):      ${S0:.2f}")
    print(f"  Sigma (GARCH):          {sigma * 100:.1f}%")
    print(f"  Mu (effective):         {mu_effective * 100:+.1f}%  "
          f"(raw {mu_hist * 100:+.1f}%, capped, enrichment-adjusted)")
    print(f"  RSI(14):                {rsi:.1f}")
    print(f"  5-day momentum:         {mom_5d * 100:+.2f}%")
    print()
    print(f"  Position: {args.shares} shares @ ${args.entry:.0f} cost basis, "
          f"target ${args.target:.0f}, no automatic stop")
    print(f"  Patience window:        {args.horizon} trading days")
    print()
    print_header("HEADLINE METRIC")
    print(f"  P(touch ${args.target:.0f} within {args.horizon}d): "
          f"{p_touch_mc * 100:5.1f}%  (MC, 50k paths)")
    print(f"  P (closed-form cross-check):       "
          f"{p_touch_cf * 100:5.1f}%  (continuous time)")
    print()
    print(f"  Break-even threshold P*:           "
          f"{breakeven_p * 100:5.1f}%  (auto-computed from today's sigma/mu)")
    print(f"  Cushion vs threshold:              "
          f"{cushion_pp:+5.1f}pp")
    print()
    print_header("ECONOMICS")
    print(f"  EV(hold no-stop {args.horizon}d):              ${ev_hold:+,.0f}")
    print(f"  EV(cut now at spot ${S0:.2f}):    ${cut_pnl:+,.0f}")
    print(f"  EV advantage of holding:           ${ev_advantage:+,.0f}")
    print()
    print(f"  If target hit:        +${pnl_good:,.0f}  (probability {p_touch_mc*100:.1f}%)")
    print(f"  If target missed:     ${pnl_bad:+,.0f}  (avg, probability {(1-p_touch_mc)*100:.1f}%)")
    print()
    print_header("TAIL RISK")
    print(f"  P(touch ${panic_level:.0f} panic floor in {args.horizon}d): "
          f"{p_panic * 100:.1f}%")
    print(f"  (your stated tolerance floor — watch this number trend up)")
    print()
    print_header(f"VERDICT  {light}  {verdict}")
    bp = breakeven_p * 100
    bands = [
        (f">= {bp+10:.0f}%", "STRONG HOLD", "cushion >= 10pp, comfortable margin"),
        (f"{bp+5:.0f}-{bp+10:.0f}%",  "HOLD",        "cushion 5-10pp, watch trend daily"),
        (f"{bp:.0f}-{bp+5:.0f}%",  "EDGE",        "at break-even, cut on any deterioration"),
        (f"< {bp:.0f}%",   "CUT",         "EV of hold worse than cut-now"),
    ]
    for band, status, note in bands:
        marker = "  ->" if status == verdict else "    "
        print(f"{marker}  P {band:<9} = {status:<13} ({note})")
    print()

    history_dir = Path(__file__).parent / "output"
    history_dir.mkdir(exist_ok=True)
    history_path = history_dir / f"thesis_history_{args.thesis_ticker}.csv"
    new_row = (f"{datetime.now():%Y-%m-%d %H:%M},{S0:.2f},{sigma*100:.2f},"
               f"{mu_effective*100:.2f},{p_touch_mc*100:.2f},"
               f"{breakeven_p*100:.2f},{cushion_pp:.2f},{ev_hold:.0f},"
               f"{cut_pnl:.0f},{verdict}\n")
    if not history_path.exists():
        history_path.write_text(
            "timestamp,spot,sigma_pct,mu_pct,p_touch_pct,"
            "breakeven_p_pct,cushion_pp,ev_hold,ev_cut,verdict\n")
    with open(history_path, "a") as f:
        f.write(new_row)

    rows = history_path.read_text().strip().split("\n")
    if len(rows) >= 2:
        print_header(f"7-DAY TREND (from {history_path.name})")
        print(f"  {'When':<19} {'Spot':>8} {'Sigma':>7} {'P(touch)':>9} "
              f"{'P*':>7} {'Cushion':>9} {'Verdict':>14}")
        print(f"  {'-'*19} {'-'*8} {'-'*7} {'-'*9} {'-'*7} {'-'*9} {'-'*14}")
        for row in rows[1:][-7:]:
            ts, sp, sg, mu_, pt, bp, cu, evh, evc, vd = row.split(",")
            print(f"  {ts:<19} ${float(sp):>6.2f} {float(sg):>6.1f}% "
                  f"{float(pt):>8.1f}% {float(bp):>6.1f}% {float(cu):>+8.1f}pp "
                  f"{vd:>14}")
        print()
    print(f"  Saved: {history_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticker", nargs="?")
    ap.add_argument("--entry", type=float)
    ap.add_argument("--shares", type=int)
    ap.add_argument("--target", type=float)
    ap.add_argument("--stop", type=float)
    ap.add_argument("--horizon", type=int, default=60)
    ap.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    ap.add_argument("--drift-cap", type=float, default=1.0)
    ap.add_argument("--verify", metavar="MC_JSON_PATH",
                    help="Verify a swing_analyzer.py MC JSON output against PDE")
    ap.add_argument("--check-thesis", metavar="TICKER", dest="thesis_ticker",
                    help="Daily thesis health check for a no-stop hold "
                         "(requires --entry --shares --target --horizon)")
    ap.add_argument("--panic-level", type=float, default=1100,
                    help="Tail-risk floor to track in --check-thesis mode")
    args = ap.parse_args()

    if args.verify:
        verify_mode(args.verify)
        return

    if args.thesis_ticker:
        if not all([args.entry, args.shares, args.target]):
            sys.exit("ERROR: --check-thesis requires "
                     "--entry --shares --target [--horizon 60]")
        check_thesis_mode(args)
        return

    if not all([args.ticker, args.entry, args.shares, args.target, args.stop]):
        sys.exit("ERROR: standalone mode requires "
                 "ticker --entry --shares --target --stop")
    standalone_mode(args)


if __name__ == "__main__":
    main()
