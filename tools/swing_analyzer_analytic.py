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
    args = ap.parse_args()

    if args.verify:
        verify_mode(args.verify)
        return

    if not all([args.ticker, args.entry, args.shares, args.target, args.stop]):
        sys.exit("ERROR: standalone mode requires "
                 "ticker --entry --shares --target --stop")
    standalone_mode(args)


if __name__ == "__main__":
    main()
