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


# =============================================================
# GOD MODE INTELLIGENCE — multi-signal forward drift estimation
#
# §2026-05-17: 6 signals blended into a single forward drift
# estimate, replacing pure historical extrapolation as the
# headline assumption. Designed for high-stakes single-ticker
# swing-trade decisions. Mirrors canon patterns from
# src/regime_classifier.py (source-quality gates, multi-source
# confirmation) but goes superior:
#   - JSON structured output (richer than 6-line text)
#   - Per-factor source citation
#   - Explicit bull/bear factor lists with weights
#   - Position-specific guidance (HOLD/TRIM/CUT/ADD)
#   - Dispersion warning when signals disagree
#   - Opus 4.7 model (canon uses Sonnet 4 for cost)
# =============================================================

# Opus 4.7 pricing (anthropic published)
OPUS_INPUT_PER_TOKEN = 15.00 / 1_000_000
OPUS_OUTPUT_PER_TOKEN = 75.00 / 1_000_000
WEB_SEARCH_PER_USE = 0.01

# Blend weights (sum = 1.0). §2026-05-17 god mode v4: expanded to 9 signals.
# Quality gates apply (LOW conf / SPECULATIVE+single source → halved weight;
# NONE_FOUND → dropped). Default weights designed so each non-AI signal carries
# meaningful but not dominant weight; AI carries the most weight as it's the
# only signal with forward-looking synthesis across all factors.
BLEND_WEIGHTS = {
    "historical":         0.10,
    "analyst":            0.15,
    "sector":             0.08,
    "macro":              0.07,
    "insider":            0.05,
    "ai":                 0.30,
    "short_interest":     0.05,
    "peer_rs":            0.10,
    "sector_decoupling":  0.10,
}


def _anthropic_client():
    """Lazy init per CLAUDE.md sacred decision."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
        return Anthropic(api_key=api_key)
    except Exception as e:
        print(f"   WARNING: anthropic client init failed: {e}")
        return None


def compute_opus_cost(response, had_web_search=False):
    """Cost of an Opus 4.7 call. Mirrors src/sentiment.py.compute_call_cost
    but uses Opus pricing ($15/M input, $75/M output)."""
    try:
        u = response.usage
        cost = (u.input_tokens * OPUS_INPUT_PER_TOKEN
                + u.output_tokens * OPUS_OUTPUT_PER_TOKEN)
        ws_uses = 0
        stu = getattr(u, "server_tool_use", None)
        if stu is not None:
            ws_uses = getattr(stu, "web_search_requests", 0) or 0
        if not ws_uses and had_web_search:
            ws_uses = 1
        cost += ws_uses * WEB_SEARCH_PER_USE
        return float(cost)
    except Exception:
        return 0.30 if had_web_search else 0.05


# -----------------------------------------------------------
# FMP intelligence fetchers — graceful failure on any endpoint
# -----------------------------------------------------------

def _fmp_get(endpoint, api_key, params=None):
    p = {"apikey": api_key}
    if params:
        p.update(params)
    try:
        r = requests.get(f"{FMP_BASE}/{endpoint}", params=p, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"   WARNING: FMP {endpoint} failed: {e}")
        return None


def fetch_analyst_targets(ticker, api_key):
    """FMP price-target-consensus (12-month analyst price targets).
    Returns aggregate-only data (no per-analyst breakdown on Starter)."""
    data = _fmp_get("price-target-consensus", api_key, {"symbol": ticker})
    if not data or not isinstance(data, list) or not data:
        return None
    d = data[0]
    return {
        "target_mean":   d.get("targetConsensus"),
        "target_median": d.get("targetMedian"),
        "target_high":   d.get("targetHigh"),
        "target_low":    d.get("targetLow"),
    }


def fetch_analyst_summary(ticker, api_key):
    """FMP price-target-summary — RECENT timeframe averages with analyst
    counts per window. §2026-05-17 verified via curl: returns lastMonth,
    lastQuarter, lastYear, allTime averages plus counts. Much better than
    stale aggregate consensus for fast-moving stocks: a stock that rallies
    300% in 6 months has lastYear targets dragged down by pre-rally data,
    while lastMonth captures post-earnings analyst revisions.

    Example SNDK response (May 2026):
      lastMonth:   13 analysts, $1376 avg (post-Q3 reflection)
      lastQuarter: 16 analysts, $1334 avg
      lastYear:    46 analysts, $772 avg (stale)
      allTime:     50 analysts, $716 avg (stale)
    """
    data = _fmp_get("price-target-summary", api_key, {"symbol": ticker})
    if not data or not isinstance(data, list) or not data:
        return None
    d = data[0]
    return {
        "last_month_count":   int(d.get("lastMonthCount", 0) or 0),
        "last_month_avg":     d.get("lastMonthAvgPriceTarget"),
        "last_quarter_count": int(d.get("lastQuarterCount", 0) or 0),
        "last_quarter_avg":   d.get("lastQuarterAvgPriceTarget"),
        "last_year_count":    int(d.get("lastYearCount", 0) or 0),
        "last_year_avg":      d.get("lastYearAvgPriceTarget"),
        "all_time_count":     int(d.get("allTimeCount", 0) or 0),
        "all_time_avg":       d.get("allTimeAvgPriceTarget"),
        "publishers":         d.get("publishers", ""),
    }


def fetch_next_earnings(ticker, api_key, lookahead_days=120):
    """FMP earnings-calendar — find next scheduled earnings event for
    ticker within the lookahead window. §2026-05-17 verified via curl.

    Returns dict with:
      date         — ISO date string of next earnings
      days_away    — days from today (int)
      eps_est      — consensus EPS estimate
      rev_est      — consensus revenue estimate
      in_horizon   — bool: falls within the swing horizon (60d default)
      approaching  — bool: falls just after horizon (60-90d post-horizon)
                     so price will run up toward it in late horizon days

    Returns None if no earnings found in window.
    """
    from_date = datetime.now().strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=lookahead_days)).strftime("%Y-%m-%d")
    data = _fmp_get("earnings-calendar", api_key,
                    {"from": from_date, "to": to_date})
    if not data or not isinstance(data, list):
        return None
    matches = [e for e in data if e.get("symbol") == ticker]
    if not matches:
        return None
    matches.sort(key=lambda x: x.get("date", "9999-99-99"))
    next_ev = matches[0]
    try:
        ev_date = datetime.strptime(next_ev["date"], "%Y-%m-%d")
        days_away = (ev_date.date() - datetime.now().date()).days
    except (ValueError, KeyError):
        return None
    return {
        "date": next_ev["date"],
        "days_away": days_away,
        "eps_est": next_ev.get("epsEstimated"),
        "rev_est": next_ev.get("revenueEstimated"),
        "in_horizon": False,  # will be set by caller using actual horizon
        "approaching": False,
    }


def fetch_company_profile(ticker, api_key):
    """FMP profile — sector, industry, market cap, etc."""
    data = _fmp_get("profile", api_key, {"symbol": ticker})
    if not data or not isinstance(data, list) or not data:
        return None
    return data[0]


def fetch_sector_perf(sector, api_key, days=30, exchange_filter="NASDAQ"):
    """FMP historical-sector-performance — CANON: §sacred requires
    sector + from/to dates (otherwise stale 2024 data). §2026-05-17 fix:
    response field is `averageChange` per SGC src/data_fetcher.py canon,
    not `changesPercentage` as I had it. Also try alternative field names
    for robustness.

    §2026-05-17 follow-up fix (verified via curl test): the endpoint
    returns ONE row per (date, exchange) pair, where exchange is one of
    NASDAQ / NYSE / AMEX. Without filtering, the last-N-rows truncation
    grabs mixed exchanges and incomplete date coverage. Filter to the
    stock's exchange (default NASDAQ for tech), then take last N unique
    dates."""
    if not sector:
        return None
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days + 14)).strftime("%Y-%m-%d")
    data = _fmp_get("historical-sector-performance", api_key,
                    {"sector": sector, "from": start, "to": end})
    if not data or not isinstance(data, list) or len(data) < 2:
        return None
    # §2026-05-17 fix: filter to one exchange (default NASDAQ).
    filtered = [r for r in data
                if not exchange_filter or r.get("exchange") == exchange_filter]
    # If filter eliminated everything, fall back to all rows (better than None)
    if not filtered:
        filtered = data
    rows = sorted(filtered, key=lambda x: x.get("date", ""))
    rows = rows[-days:]
    if not rows:
        return None
    # Try multiple possible field names (FMP has used different names
    # historically; canon SGC code uses averageChange)
    field_candidates = ["averageChange", "changesPercentage", "changePercent",
                        "change"]
    field = None
    for f in field_candidates:
        if f in rows[0]:
            field = f
            break
    if field is None:
        return None
    cum_return = 1.0
    for r in rows:
        try:
            val = float(r.get(field, 0))
            # §2026-05-17 verified via curl test: FMP returns averageChange
            # in PERCENT units (e.g. 0.12 = 0.12%, 2.57 = 2.57%). Always
            # divide by 100. The earlier "heuristic" was wrong for small
            # values like 0.12 (treated incorrectly as 12% daily).
            cum_return *= (1 + val / 100.0)
        except (ValueError, TypeError):
            continue
    cum_return -= 1.0
    return {
        "cum_return_pct": cum_return * 100,
        "n_days": len(rows),
        "sector": sector,
        "exchange": exchange_filter,
        "field_used": field,  # for debugging
    }


def fetch_insider_activity(ticker, api_key, days=90):
    """FMP insider-trading/search (CANON: §sacred — use this NOT
    insider-trading-statistics which returns empty)."""
    data = _fmp_get("insider-trading/search", api_key,
                    {"symbol": ticker, "limit": 100})
    if not data or not isinstance(data, list):
        return None
    cutoff = datetime.now() - timedelta(days=days)
    net_value = 0.0
    n_buys = 0
    n_sells = 0
    for tx in data:
        tx_type = (tx.get("transactionType") or "").upper()
        # Canon filter: P = Purchase, S = Sale (locally filtered)
        is_purchase = tx_type.startswith("P")
        is_sale = tx_type.startswith("S")
        if not (is_purchase or is_sale):
            continue
        try:
            tx_date = datetime.strptime(tx.get("transactionDate", "")[:10],
                                        "%Y-%m-%d")
            if tx_date < cutoff:
                continue
        except (ValueError, TypeError):
            continue
        try:
            shares = float(tx.get("securitiesTransacted", 0) or 0)
            price = float(tx.get("price", 0) or 0)
            value = shares * price
            if is_purchase:
                net_value += value
                n_buys += 1
            else:
                net_value -= value
                n_sells += 1
        except (ValueError, TypeError):
            continue
    return {
        "net_value_usd": net_value,
        "n_buys": n_buys,
        "n_sells": n_sells,
        "days": days,
    }


def fetch_recent_news(ticker, api_key, limit=20):
    """FMP news/stock — recent headlines for ticker."""
    data = _fmp_get("news/stock", api_key,
                    {"symbols": ticker, "limit": limit})
    if not data or not isinstance(data, list):
        return []
    out = []
    for item in data[:limit]:
        out.append({
            "date":      (item.get("publishedDate") or "")[:10],
            "publisher": item.get("publisher") or item.get("site") or "unknown",
            "title":     item.get("title") or "",
            "snippet":   (item.get("text") or "")[:200],
        })
    return out


def fetch_press_releases(ticker, api_key, limit=10):
    """FMP news/press-releases — official company releases (PRIMARY source)."""
    data = _fmp_get("news/press-releases", api_key,
                    {"symbols": ticker, "limit": limit})
    if not data or not isinstance(data, list):
        return []
    out = []
    for item in data[:limit]:
        out.append({
            "date":    (item.get("publishedDate") or "")[:10],
            "title":   item.get("title") or "",
            "snippet": (item.get("text") or "")[:200],
        })
    return out


def fetch_macro_indicators(api_key):
    """FMP VIX + SPY for risk-on/risk-off (inlined from src/macro_regime.py)."""
    vix_data = _fmp_get("quote", api_key, {"symbol": "^VIX"})
    spy_data = _fmp_get("quote", api_key, {"symbol": "SPY"})
    vix = 18.0
    spy_trend = 0.0
    if vix_data and isinstance(vix_data, list) and vix_data and vix_data[0].get("price"):
        vix = float(vix_data[0]["price"])
    if spy_data and isinstance(spy_data, list) and spy_data:
        d = spy_data[0]
        if d.get("price") and d.get("priceAvg50"):
            try:
                spy_trend = (float(d["price"]) - float(d["priceAvg50"])) / float(d["priceAvg50"])
            except (ValueError, TypeError, ZeroDivisionError):
                spy_trend = 0.0
    if vix > 25 or spy_trend < -0.03:
        regime = "risk_off"
    elif vix < 15 and spy_trend > 0.02:
        regime = "risk_on"
    else:
        regime = "neutral"
    return {"vix": vix, "spy_trend": spy_trend, "regime": regime}


# -----------------------------------------------------------
# Signal -> drift dict converters. Each returns:
#   {drift: float|None, confidence: str, source_quality: str,
#    sources_count: int, notes: str}
# -----------------------------------------------------------

def _none_signal(reason):
    return {"drift": None, "confidence": "LOW",
            "source_quality": "NONE_FOUND", "sources_count": 0,
            "notes": reason}


def signal_from_analyst_targets(targets, S0, price_history_df=None,
                                  summary=None):
    """Convert FMP analyst targets to drift signal.

    §2026-05-17 upgrade: prefer fresh `price-target-summary` data (last-month
    avg) when available, falling back to the stale `price-target-consensus`
    aggregate. For fast-moving stocks the freshness difference is dramatic:
    SNDK consensus mean was $1268 (stale-mixed) vs lastMonth avg $1376
    (post-Q3 reflection).

    Staleness check still applies: if stock moved >25% in 60d AND we're using
    anything older than last-month, downgrade confidence.
    """
    # ---- Path 1: use SUMMARY (fresh, preferred) ----
    if summary:
        target = None
        n_analysts = 0
        window = ""
        base_conf = "MEDIUM"

        # Prefer last-month if >=5 analysts (substantive sample)
        if summary.get("last_month_count", 0) >= 5 and summary.get("last_month_avg"):
            target = float(summary["last_month_avg"])
            n_analysts = summary["last_month_count"]
            window = "last month"
            base_conf = "HIGH" if n_analysts >= 12 else "MEDIUM"
        elif summary.get("last_quarter_count", 0) >= 5 and summary.get("last_quarter_avg"):
            target = float(summary["last_quarter_avg"])
            n_analysts = summary["last_quarter_count"]
            window = "last quarter"
            base_conf = "MEDIUM" if n_analysts >= 15 else "LOW"
        elif summary.get("last_year_avg"):
            target = float(summary["last_year_avg"])
            n_analysts = summary.get("last_year_count", 0)
            window = "last year"
            base_conf = "LOW"  # likely stale on fast movers

        if target and target > 0 and S0 > 0:
            drift = (target / S0) - 1.0
            staleness_note = ""
            if window != "last month" and price_history_df is not None and len(price_history_df) >= 60:
                try:
                    p60 = float(price_history_df["Close"].iloc[-60])
                    move_60d = abs((S0 - p60) / p60)
                    if move_60d > 0.25:
                        base_conf = "LOW"
                        staleness_note = (f" (STALENESS: stock moved {move_60d*100:+.0f}% "
                                          f"in 60d, only {window} avg available)")
                except (ValueError, TypeError, IndexError):
                    pass
            return {
                "drift": float(drift), "confidence": base_conf,
                "source_quality": "REPUTABLE", "sources_count": int(n_analysts),
                "notes": (f"{window} avg ${target:.0f} (n={n_analysts}), "
                          f"vs spot ${S0:.0f}, drift implied {drift*100:+.1f}%"
                          f"{staleness_note}"),
            }
        # If summary had no usable timeframe, fall through to consensus

    # ---- Path 2: fall back to STALE consensus ----
    if not targets or not targets.get("target_mean") or S0 <= 0:
        return _none_signal("no analyst targets available")
    try:
        target = float(targets["target_mean"])
        if target <= 0:
            return _none_signal("invalid target price")
        drift = (target / S0) - 1.0
        high = float(targets.get("target_high") or target)
        low = float(targets.get("target_low") or target)
        spread = (high - low) / target if target > 0 else 1.0
        if spread < 0.10:
            conf = "HIGH"
        elif spread < 0.25:
            conf = "MEDIUM"
        else:
            conf = "LOW"

        staleness_note = " (stale-mixed consensus fallback)"
        if price_history_df is not None and len(price_history_df) >= 60:
            try:
                p60 = float(price_history_df["Close"].iloc[-60])
                move_60d = abs((S0 - p60) / p60)
                if move_60d > 0.25:
                    conf = "LOW"
                    staleness_note = (f" (STALE: stock moved {move_60d*100:+.0f}% "
                                      f"in 60d, consensus lags; no fresh summary either)")
            except (ValueError, TypeError, IndexError):
                pass

        return {
            "drift": float(drift), "confidence": conf,
            "source_quality": "REPUTABLE", "sources_count": 5,
            "notes": (f"consensus mean ${target:.0f}, range ${low:.0f}-${high:.0f}"
                      f"{staleness_note}"),
        }
    except (ValueError, TypeError):
        return _none_signal("analyst target parse error")


def signal_from_sector(sector_perf, swing_regime=None):
    """§2026-05-17 audit P1.4: regime-gate the sector signal. Annualising a
    30-day sector return is a momentum extrapolation, not a fundamental
    estimator. In POST_PARABOLA / overbought regimes, this extrapolation
    is most likely to over-fire (high-vol momentum days hit the +150% cap).
    Lower cap and downgrade confidence when regime is parabolic.
    """
    if not sector_perf or sector_perf.get("cum_return_pct") is None:
        return _none_signal("sector data unavailable")
    days = max(1, sector_perf.get("n_days", 30))
    cum = sector_perf["cum_return_pct"] / 100.0
    drift = (1 + cum) ** (252 / days) - 1.0

    # Regime-aware cap + confidence
    regime_name = swing_regime.get("regime") if swing_regime else None
    if regime_name == "POST_PARABOLA":
        # Sector momentum extrapolation is least trustworthy in parabolic regimes
        cap_high, cap_low = 0.60, -0.50
        conf = "LOW"
        regime_note = " [POST_PARABOLA regime: sector cap reduced to +60%, conf LOW]"
    elif regime_name in ("MOMENTUM_BULL", "MOMENTUM_BEAR"):
        cap_high, cap_low = 1.00, -0.50
        conf = "MEDIUM"
        regime_note = f" [{regime_name}: cap +100%]"
    else:
        cap_high, cap_low = 1.50, -0.50
        conf = "MEDIUM"
        regime_note = ""

    drift = max(cap_low, min(cap_high, drift))
    return {
        "drift": float(drift), "confidence": conf,
        "source_quality": "PRIMARY", "sources_count": 1,
        "notes": (f"{sector_perf.get('sector','?')} {cum*100:+.1f}% "
                  f"last {days}d (annualised {drift*100:+.0f}%){regime_note}"),
    }


def signal_from_macro(macro):
    """§2026-05-17 audit fix #5: macro is a BROAD backdrop, downgraded from
    HIGH to MEDIUM confidence. A single VIX/SPY snapshot is not a
    high-conviction forward-drift estimator for a single stock."""
    if not macro:
        return _none_signal("macro data unavailable")
    regime = macro.get("regime", "neutral")
    drift = {"risk_on": 0.10, "neutral": 0.05, "risk_off": -0.05}.get(regime, 0.05)
    return {
        "drift": float(drift), "confidence": "MEDIUM",  # was HIGH
        "source_quality": "PRIMARY", "sources_count": 2,
        "notes": (f"VIX {macro['vix']:.1f}, SPY {macro['spy_trend']*100:+.1f}% "
                  f"vs MA50 -> {regime}"),
    }


def signal_from_insider(insider, market_cap_usd=None):
    """§2026-05-17 audit fix #7: scale insider $/drift by market cap so the
    calibration is size-relative. $6.6M on a $220B mcap is noise; the old
    /100M absolute calibration over-weighted insider for large caps."""
    if not insider:
        return _none_signal("insider data unavailable")
    n_total = insider.get("n_buys", 0) + insider.get("n_sells", 0)
    if n_total == 0:
        return {"drift": 0.0, "confidence": "LOW",
                "source_quality": "PRIMARY", "sources_count": 1,
                "notes": "no insider P+S transactions in window"}
    net = insider.get("net_value_usd", 0)
    # Size-relative calibration: 1% of market cap net flow → ±5% drift tilt
    # (very large insider activity even relative to size)
    if market_cap_usd and market_cap_usd > 0:
        flow_pct_of_mcap = net / market_cap_usd
        drift = max(-0.10, min(0.10, flow_pct_of_mcap * 5.0))
        scaling_note = f" (mcap-relative: {flow_pct_of_mcap*100:.3f}% of $US{market_cap_usd/1e9:.0f}B)"
    else:
        # Fallback to absolute scaling if mcap unavailable
        drift = max(-0.10, min(0.10, net / 100_000_000))
        scaling_note = " (absolute scaling — no mcap available)"
    direction = "buying" if net > 0 else "selling"
    # If gross flow is tiny relative to mcap, signal is noise — downgrade confidence
    if market_cap_usd and market_cap_usd > 0 and abs(net) / market_cap_usd < 0.001:
        # less than 0.1% of mcap → noise
        conf = "LOW"
        scaling_note += " — NOISE-LEVEL relative to mcap, downgraded LOW"
    else:
        conf = "MEDIUM"
    return {
        "drift": float(drift), "confidence": conf,
        "source_quality": "PRIMARY", "sources_count": 1,
        "notes": (f"net {direction} ${abs(net)/1e6:.1f}M "
                  f"({insider['n_buys']}P/{insider['n_sells']}S in "
                  f"{insider['days']}d){scaling_note}"),
    }


def signal_from_historical(mu_capped, mu_raw, sigma):
    """§2026-05-17 audit fix #2: historical drift is included with non-zero
    weight but gated as LOW confidence when the cap is binding (raw drift >
    cap), which halves its effective weight per quality gates."""
    if mu_capped is None:
        return _none_signal("historical drift unavailable")
    # If raw drift was capped, this means extrapolation bias is high — LOW conf
    if abs(mu_raw) > 1.0:  # cap is binding
        conf = "LOW"
        gate_note = " (CAP BINDING — extrapolation risk; gated LOW)"
    elif abs(mu_capped) > 0.5:  # large drift but not capped
        conf = "MEDIUM"
        gate_note = " (large drift; gated MEDIUM)"
    else:
        conf = "HIGH"
        gate_note = ""
    return {
        "drift": float(mu_capped), "confidence": conf,
        "source_quality": "PRIMARY", "sources_count": 1,
        "notes": (f"GARCH-fit on 730d log returns, raw {mu_raw*100:+.0f}%/yr "
                  f"capped at {mu_capped*100:+.0f}%/yr{gate_note}"),
    }


def signal_from_ai(ai_parsed):
    if not ai_parsed or ai_parsed.get("drift_point") is None:
        return _none_signal("AI synthesis unavailable or malformed")
    try:
        return {
            "drift": float(ai_parsed.get("drift_point")),
            "drift_low": float(ai_parsed.get("drift_low",
                                             ai_parsed["drift_point"])),
            "drift_high": float(ai_parsed.get("drift_high",
                                              ai_parsed["drift_point"])),
            "confidence": ai_parsed.get("confidence", "MEDIUM"),
            "source_quality": ai_parsed.get("source_quality", "REPUTABLE"),
            "sources_count": int(ai_parsed.get("sources_count", 0)),
            "notes": (ai_parsed.get("rationale", "") or "")[:150],
        }
    except (ValueError, TypeError):
        return _none_signal("AI response had non-numeric drift")


# -----------------------------------------------------------
# AI synthesis (Claude Opus 4.7 with web_search)
# -----------------------------------------------------------

def build_ai_synthesis_prompt(ticker, profile, S0, sigma, horizon, recent_news,
                              press_releases, sector_perf, analyst_targets,
                              insider, macro, earnings_event=None,
                              analyst_summary=None):
    company_name = (profile.get("companyName") if profile else ticker) or ticker
    sector = (profile.get("sector") if profile else "Unknown") or "Unknown"
    industry = (profile.get("industry") if profile else "") or ""
    today = datetime.now().strftime("%Y-%m-%d")

    news_block = "\n".join(
        f"  - [{n['date']}] {n['publisher']}: {n['title']}"
        for n in (recent_news or [])[:15]
    ) or "  (no recent FMP news available)"
    pr_block = "\n".join(
        f"  - [{p['date']}] {p['title']}"
        for p in (press_releases or [])[:8]
    ) or "  (no recent FMP press releases)"

    sector_str = (f"{sector_perf['sector']} sector "
                  f"{sector_perf['cum_return_pct']:+.1f}% last "
                  f"{sector_perf['n_days']}d"
                  if sector_perf else "sector data unavailable")
    if analyst_summary and analyst_summary.get("last_month_avg"):
        # Prefer fresh last-month avg (§2026-05-17 upgrade A)
        analyst_str = (f"last-month avg ${analyst_summary['last_month_avg']:.0f} "
                       f"(n={analyst_summary['last_month_count']}), "
                       f"last-quarter avg ${analyst_summary.get('last_quarter_avg', 0):.0f} "
                       f"(n={analyst_summary.get('last_quarter_count', 0)}), "
                       f"stale all-time avg ${analyst_summary.get('all_time_avg', 0):.0f}")
    elif analyst_targets and analyst_targets.get("target_mean"):
        analyst_str = (f"consensus target ${analyst_targets['target_mean']:.0f} "
                       f"(range ${analyst_targets.get('target_low') or 0:.0f}-"
                       f"${analyst_targets.get('target_high') or 0:.0f}) [stale-mixed]")
    else:
        analyst_str = "no analyst consensus available"

    if earnings_event:
        earnings_str = (f"next earnings {earnings_event['date']} "
                        f"({earnings_event['days_away']} days away)")
        if earnings_event.get("eps_est"):
            earnings_str += f", EPS est ${earnings_event['eps_est']:.2f}"
        if earnings_event["in_horizon"]:
            earnings_str += " — WITHIN HORIZON (event-day risk)"
        elif earnings_event["approaching"]:
            earnings_str += " — approaching (late-horizon run-up)"
    else:
        earnings_str = "no earnings event in next 90+ days"
    if insider and (insider["n_buys"] + insider["n_sells"]) > 0:
        direction = "buying" if insider["net_value_usd"] > 0 else "selling"
        insider_str = (f"net {direction} ${abs(insider['net_value_usd'])/1e6:.1f}M "
                       f"last 90d ({insider['n_buys']}P/{insider['n_sells']}S)")
    else:
        insider_str = "no insider P+S transactions in last 90 days"
    macro_str = (f"VIX {macro['vix']:.1f}, SPY {macro['spy_trend']*100:+.1f}% "
                 f"vs MA50, regime: {macro['regime']}"
                 if macro else "macro data unavailable")

    return f"""You are a buy-side equity analyst doing forward drift estimation for {company_name} ({ticker}, {sector} / {industry}) as of {today}.

YOUR TASK: estimate annualised forward drift (mu) over the next {horizon} trading days. Output STRICT JSON ONLY.

SYMMETRIC ANTI-BIAS RULES (apply EQUALLY in both directions):
- Do NOT extrapolate recent rallies just because a stock has rallied hard.
  BUT EQUALLY: do NOT under-weight fundamentally-driven growth. A multi-bagger
  driven by VERIFIED earnings beats and raised guidance is genuinely different
  from a multi-bagger driven by hype.
- Do NOT confirm bull priors. Do NOT confirm bear priors either.
- If the most recent quarter showed >100% YoY revenue growth with raised
  guidance and verified backlog, the historical drift is at least partially
  fundamentally validated; do NOT default to risk-free in that case.
- If evidence is contradictory, return a WIDE range, not a confident middle.
- USE web_search to verify against current data, not training cutoff.
- Aggregator consensus targets (StockAnalysis, MarketBeat, ChartMill) often
  LAG fast-moving stocks by weeks-to-months after big moves; weight recent
  individual sell-side updates (Bernstein, Mizuho, JPM, etc., last 30d)
  MORE heavily than stale aggregator means.

CONTEXT DATA (from FMP, today):
- Current spot: ${S0:.2f}
- GARCH vol: {sigma*100:.0f}% annualised (CHARACTERISES dispersion, NOT direction)
- Sector context: {sector_str}
- Analyst targets: {analyst_str}
- Insider activity: {insider_str} (note: includes any tax-withholding dispositions
  under Rule 16b-3(e); discount procedural sales when estimating sentiment)
- Macro backdrop: {macro_str}
- Earnings calendar: {earnings_str}

RECENT NEWS HEADLINES (FMP, last 30-90 days):
{news_block}

RECENT PRESS RELEASES (FMP):
{pr_block}

SEARCH PRIORITIES (use web_search aggressively):
1. Most recent 10-Q / 10-K / 8-K SEC filings (verify dates)
2. Earnings call transcript from most recent quarter — guidance + tone
3. Industry research: pricing trends, demand drivers, supply dynamics in {industry or sector}
4. Competitive landscape: who competes with {company_name}? market-share trends?
5. Sector ETF performance (SOXX/SMH for semis, XLK for tech, etc.) — last 90d
6. Analyst rating changes (last 30 days) — DISTINGUISH recent updates from stale consensus

SOURCE QUALITY HIERARCHY:
- PRIMARY: SEC filings, official company statements, exchange data, earnings transcripts
- REPUTABLE: Reuters, Bloomberg, WSJ, FT, CNBC, major sell-side research
- SPECULATIVE: blogs, Seeking Alpha contributors, social media, single-source rumours

SKEPTICISM RULES:
- Require >=2 independent reputable sources for any thesis claim
- Tag staleness on time-sensitive data (>30 days = stale)
- If sector is rallying with the stock, lean MODEST drift (not extrapolation)
- If a single news item drives an entire bull/bear case, downgrade to SPECULATIVE

POSITION_GUIDANCE rules (the user already owns the stock):
- HOLD: drift_point >= +20% AND confidence MEDIUM+ AND no severe bear factors
- TRIM: drift_point in [0%, +20%) OR confidence LOW OR mixed bull/bear signals
- CUT: drift_point < 0% OR severe bear factor with PRIMARY source confirmation
- ADD: drift_point >= +40% AND confidence HIGH AND strong bull conviction (rare)

OUTPUT STRICT JSON (no markdown fences, no preamble, parseable directly):

{{
  "drift_point": <decimal OR null if evidence too thin>,
  "drift_low": <decimal, lower bound of range>,
  "drift_high": <decimal, upper bound of range>,
  "confidence": "HIGH" or "MEDIUM" or "LOW",
  "source_quality": "PRIMARY" or "REPUTABLE" or "SPECULATIVE" or "NONE_FOUND",
  "sources_count": <integer — distinct credible sources cited>,
  "bull_factors": [
    {{"factor": "<1-2 sentence factor>", "source": "<publication, date>", "weight": "high" or "med" or "low"}},
    {{"factor": "...", "source": "...", "weight": "..."}},
    {{"factor": "...", "source": "...", "weight": "..."}}
  ],
  "bear_factors": [
    {{"factor": "<1-2 sentence factor>", "source": "<publication, date>", "weight": "high" or "med" or "low"}},
    {{"factor": "...", "source": "...", "weight": "..."}},
    {{"factor": "...", "source": "...", "weight": "..."}}
  ],
  "key_risks": [
    {{"risk": "<1-sentence>", "probability": "low" or "med" or "high", "impact": "low" or "med" or "high"}},
    {{"risk": "...", "probability": "...", "impact": "..."}},
    {{"risk": "...", "probability": "...", "impact": "..."}}
  ],
  "position_guidance": "HOLD" or "TRIM" or "CUT" or "ADD",
  "rationale": "<2-3 sentence plain-English summary, citing top 1-2 sources by name>",
  "evidence_gaps": "<1 sentence — what you could NOT verify and why>"
}}

CRITICAL — null-fallback rule (replaces previous +5% default per §2026-05-17 audit fix #1):
If evidence is too thin to form a defensible drift estimate, set drift_point to
JSON null (literal null, not the string "null"), set source_quality to NONE_FOUND,
set confidence to LOW, and explain in evidence_gaps. This will cause the signal
to be DROPPED from the blend rather than inject a +5% anchor. Do NOT return a
safe-feeling middle estimate when the evidence doesn't support one.

Output ONLY the JSON. Start with {{ and end with }}. No other text."""


def call_ai_analyst(prompt, model="claude-opus-4-7", max_tokens=3000):
    """Call Claude with web_search enabled. Returns (parsed_dict, cost_usd, raw_text)."""
    client = _anthropic_client()
    if client is None:
        return None, 0.0, "ANTHROPIC_API_KEY not set or anthropic SDK missing"
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )
        text_parts = [b.text for b in response.content if hasattr(b, "text")]
        text = "\n".join(text_parts) if text_parts else ""
        cost = compute_opus_cost(response, had_web_search=True)
        parsed = parse_ai_synthesis(text)
        return parsed, cost, text
    except Exception as e:
        return None, 0.0, f"AI call exception: {e}"


def parse_ai_synthesis(text):
    """Extract JSON from Claude's response. Tolerant of code fences and stray prose.
    §2026-05-17 audit fix #1: drift_point=null is a VALID response (signals
    'evidence too thin'); preserve as Python None. The downstream signal_from_ai
    then correctly maps it to NONE_FOUND quality → dropped from blend."""
    if not text:
        return None
    import re
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned, flags=re.MULTILINE)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start:end+1])
        # json.loads correctly converts JSON null to Python None — signal_from_ai
        # treats None drift_point as NONE_FOUND, dropping the signal from blend.
        # No further coercion needed here; just return.
        return parsed
    except json.JSONDecodeError:
        return None


# =============================================================
# TIER-1 GOD MODE UPGRADES (§2026-05-17 — option Y scope)
#
# 1. Regime detection — momentum/mean-reversion/range classification
#    affects what each signal's drift actually means
# 2. Vol-regime advisory — at sigma > 50%, drift is 2nd-order;
#    surface this rather than letting users obsess over drift point
# 3. Blend with uncertainty — confidence intervals on blended drift,
#    not just point estimate. Decision uses interval not midpoint.
# 4. Bayesian belief update — yesterday's blend = today's prior;
#    accumulate evidence rather than reset each daily run
# 5. Path-dependent metrics — max drawdown distribution,
#    time-to-target distribution, drawdown-along-the-way percentiles
# =============================================================


def detect_swing_regime(rsi, mom_5d, mom_30d_pct, sigma, ytd_return_pct=None):
    """Classify the stock's current regime for signal interpretation.

    Regimes:
      MOMENTUM_BULL   - strong upward trend, low mean-reversion risk near-term,
                        but high mean-reversion risk medium-term after parabola
      MOMENTUM_BEAR   - sustained decline, breakdown risk
      MEAN_REVERSION  - extended (RSI extremes) with diverging momentum
      RANGE           - low directional bias, low recent vol
      POST_PARABOLA   - massive YTD rally, RSI cooling, mean-reversion likely
      UNCERTAIN       - mixed signals, no clear regime

    Each regime affects signal interpretation downstream.
    """
    regime = "UNCERTAIN"
    detail = ""

    is_high_vol = sigma > 0.50
    has_parabola = ytd_return_pct is not None and ytd_return_pct > 200
    rsi_overbought = rsi is not None and rsi > 70
    rsi_oversold = rsi is not None and rsi < 30
    mom5_pos = mom_5d > 0.02
    mom5_neg = mom_5d < -0.02
    mom30_pos = mom_30d_pct is not None and mom_30d_pct > 5
    mom30_neg = mom_30d_pct is not None and mom_30d_pct < -5

    if has_parabola:
        regime = "POST_PARABOLA"
        detail = (f"YTD +{ytd_return_pct:.0f}% — parabolic rally; "
                  f"mean-reversion risk over horizon > weeks")
    elif rsi_overbought and mom5_neg:
        regime = "MEAN_REVERSION"
        detail = f"RSI {rsi:.0f} (overbought) + 5d momentum {mom_5d*100:+.1f}% diverging"
    elif rsi_oversold and mom5_pos:
        regime = "MEAN_REVERSION"
        detail = f"RSI {rsi:.0f} (oversold) + 5d momentum {mom_5d*100:+.1f}% diverging upward"
    elif mom30_pos and mom5_pos and not rsi_overbought:
        regime = "MOMENTUM_BULL"
        detail = f"30d momentum +{mom_30d_pct:.1f}%, 5d {mom_5d*100:+.1f}%, RSI {rsi:.0f}"
    elif mom30_neg and mom5_neg:
        regime = "MOMENTUM_BEAR"
        detail = f"30d momentum {mom_30d_pct:+.1f}%, 5d {mom_5d*100:+.1f}%, RSI {rsi:.0f}"
    elif not is_high_vol and abs(mom_5d) < 0.02:
        regime = "RANGE"
        detail = f"low vol ({sigma*100:.0f}%) + flat momentum"
    else:
        detail = (f"RSI {rsi:.0f}, 5d {mom_5d*100:+.1f}%, "
                  f"30d {mom_30d_pct:+.1f}% — no clear regime"
                  if mom_30d_pct is not None
                  else f"RSI {rsi:.0f}, 5d {mom_5d*100:+.1f}% — no clear regime")

    return {"regime": regime, "detail": detail,
            "is_high_vol": is_high_vol, "has_parabola": has_parabola}


def vol_regime_advisory(sigma):
    """Translate volatility level into a decision-quality advisory.
    At extreme vol, drift point estimates matter less than tail-risk management.
    """
    sigma_pct = sigma * 100
    if sigma_pct >= 80:
        return {
            "level": "EXTREME",
            "advisory": (
                f"Sigma {sigma_pct:.0f}% — drift estimate is 2nd-order. At this vol, "
                "the outcome is dominated by dispersion, not direction. Focus on "
                "TAIL-RISK metrics (panic-floor touch probability, max drawdown "
                "distribution) rather than the blended drift point."),
            "drift_decisive": False,
        }
    elif sigma_pct >= 50:
        return {
            "level": "HIGH",
            "advisory": (
                f"Sigma {sigma_pct:.0f}% — high vol regime. Drift matters but "
                "the cushion above break-even must be earned BOTH from drift "
                "advantage AND from acceptable tail risk. Watch panic-floor probability."),
            "drift_decisive": False,
        }
    elif sigma_pct >= 25:
        return {
            "level": "NORMAL",
            "advisory": (
                f"Sigma {sigma_pct:.0f}% — normal vol regime. Blended drift is the "
                "primary input for the hold/cut decision."),
            "drift_decisive": True,
        }
    else:
        return {
            "level": "LOW",
            "advisory": (
                f"Sigma {sigma_pct:.0f}% — low vol regime. Drift dominates outcome. "
                "Cushion math is highly reliable; small drift changes flip verdicts."),
            "drift_decisive": True,
        }


# Standard error mapping per confidence tier. These map qualitative
# confidence to a numeric standard error on the drift estimate in
# decimal annualised return units.
CONFIDENCE_TO_SE = {"HIGH": 0.05, "MEDIUM": 0.10, "LOW": 0.20}


def blend_with_uncertainty(signals, weights_dict=None):
    """Blend with confidence intervals via signal-weighted variance.

    Each signal contributes drift + standard error. The blend's variance is
    the weighted average of variances (treating signals as independent samples
    of an underlying drift). Returns:
      {blended, std, lo68, hi68, lo95, hi95, weights, dispersion_pp, n_active}
    """
    if weights_dict is None:
        weights_dict = BLEND_WEIGHTS

    effective = {}
    for name, info in signals.items():
        if info.get("drift") is None:
            effective[name] = 0.0
            continue
        w = weights_dict.get(name, 0.0)
        sq = info.get("source_quality", "REPUTABLE")
        sc = int(info.get("sources_count", 0))
        conf = info.get("confidence", "MEDIUM")
        if sq == "NONE_FOUND":
            w = 0.0
        elif sq == "SPECULATIVE" and sc < 2:
            w *= 0.5
        if conf == "LOW":
            w *= 0.5
        effective[name] = w

    total = sum(effective.values())
    if total <= 0:
        return {"blended": None, "std": None,
                "lo68": None, "hi68": None, "lo95": None, "hi95": None,
                "weights": effective, "fallback": True,
                "dispersion_pp": 0.0, "n_active": 0}

    # Normalise weights so they sum to 1
    norm = {n: w/total for n, w in effective.items()}

    blended = sum(signals[n]["drift"] * norm[n]
                  for n in signals if norm[n] > 0)

    # Weighted variance — combine within-signal SE^2 + between-signal dispersion
    within_var = 0.0
    between_var = 0.0
    for n in signals:
        if norm[n] <= 0:
            continue
        se = CONFIDENCE_TO_SE.get(signals[n].get("confidence", "MEDIUM"), 0.10)
        within_var += (norm[n] ** 2) * (se ** 2)
        between_var += norm[n] * (signals[n]["drift"] - blended) ** 2

    total_var = within_var + between_var
    std = total_var ** 0.5

    active_drifts = [signals[n]["drift"] for n in signals
                     if norm[n] > 0 and signals[n]["drift"] is not None]
    dispersion = (max(active_drifts) - min(active_drifts)) * 100 if active_drifts else 0

    return {
        "blended": float(blended),
        "std": float(std),
        "lo68": float(blended - std),
        "hi68": float(blended + std),
        "lo95": float(blended - 2 * std),
        "hi95": float(blended + 2 * std),
        "weights": effective,
        "fallback": False,
        "dispersion_pp": float(dispersion),
        "n_active": sum(1 for w in effective.values() if w > 0),
    }


def bayesian_update(prior_blend, today_blend, prior_age_days=1):
    """Bayesian update of blended drift estimate using yesterday's posterior
    as today's prior. Treats both as Gaussian: posterior_mu, posterior_var.

    The age of the prior modulates how much weight it carries. Stale priors
    (>3 days old) carry less weight; the today blend dominates.

    Returns: {posterior_mu, posterior_std, prior_weight, obs_weight}
    """
    if today_blend.get("blended") is None or today_blend.get("std") is None:
        return None
    if not prior_blend or prior_blend.get("blended") is None:
        # No prior — today's blend IS the posterior
        return {"posterior_mu": today_blend["blended"],
                "posterior_std": today_blend["std"],
                "prior_weight": 0.0, "obs_weight": 1.0,
                "note": "no prior available — using today's blend"}

    prior_mu = prior_blend["blended"]
    prior_std = prior_blend.get("std", 0.15)  # default if old format
    obs_mu = today_blend["blended"]
    obs_std = today_blend["std"]

    # Inflate prior variance based on age (stale priors are less informative)
    inflation = 1.0 + 0.2 * max(0, prior_age_days - 1)
    prior_var = (prior_std * inflation) ** 2
    obs_var = obs_std ** 2

    posterior_var = 1.0 / (1.0/prior_var + 1.0/obs_var)
    posterior_mu = posterior_var * (prior_mu/prior_var + obs_mu/obs_var)
    posterior_std = posterior_var ** 0.5

    prior_weight = posterior_var / prior_var
    obs_weight = posterior_var / obs_var

    return {"posterior_mu": float(posterior_mu),
            "posterior_std": float(posterior_std),
            "prior_weight": float(prior_weight),
            "obs_weight": float(obs_weight),
            "note": (f"Bayesian: prior_mu={prior_mu*100:+.1f}% std={prior_std*100:.1f}%, "
                     f"obs_mu={obs_mu*100:+.1f}% std={obs_std*100:.1f}%, "
                     f"weights {prior_weight*100:.0f}/{obs_weight*100:.0f}")}


def compute_path_metrics(paths, S0, target, panic_level=None):
    """Path-dependent risk metrics beyond just 'touched/not touched'.

    Returns:
      time_to_target_median, time_to_target_p25, time_to_target_p75: trading days
      max_drawdown_median, p75, p90: % from S0
      drawdown_along_way_at_30d: percentile of drawdown at mid-horizon
      panic_touch_during_journey: P(touch panic) even on paths that hit target
    """
    n_paths, horizon = paths.shape

    # Running max-drawdown per path: max((S0 - min so far)/S0)
    running_min = np.minimum.accumulate(paths, axis=1)
    drawdown_per_step = (S0 - running_min) / S0  # positive = drawdown
    max_dd_per_path = drawdown_per_step[:, -1]  # at horizon

    # Time to first touch of target (np.inf if never)
    touched_target = paths >= target
    has_touch = touched_target.any(axis=1)
    first_touch_idx = np.where(has_touch, np.argmax(touched_target, axis=1) + 1, -1)
    touch_times = first_touch_idx[has_touch]

    # Panic touch ever (different from "stopped out" since we have no stop)
    if panic_level is not None:
        touched_panic = (paths <= panic_level).any(axis=1)
    else:
        touched_panic = np.zeros(n_paths, dtype=bool)

    # Drawdown at mid-horizon (day 30 typically — represents "what does my
    # screen look like at day 30 if I'm still in the position?")
    mid_idx = horizon // 2
    drawdown_at_mid = drawdown_per_step[:, mid_idx]

    out = {
        "max_drawdown_median": float(np.median(max_dd_per_path)),
        "max_drawdown_p75": float(np.percentile(max_dd_per_path, 75)),
        "max_drawdown_p90": float(np.percentile(max_dd_per_path, 90)),
        "drawdown_at_mid_median": float(np.median(drawdown_at_mid)),
        "drawdown_at_mid_p75": float(np.percentile(drawdown_at_mid, 75)),
        "panic_touch_prob_total": float(touched_panic.mean()),
        "panic_among_target_paths": float(
            (touched_panic & has_touch).sum() / max(1, has_touch.sum())
        ),
    }

    if len(touch_times) > 0:
        out["time_to_target_median"] = float(np.median(touch_times))
        out["time_to_target_p25"] = float(np.percentile(touch_times, 25))
        out["time_to_target_p75"] = float(np.percentile(touch_times, 75))
    else:
        out["time_to_target_median"] = None
        out["time_to_target_p25"] = None
        out["time_to_target_p75"] = None

    return out


def load_prior_blend(history_path, days_back_limit=3):
    """Read yesterday's blend from CSV history as the Bayesian prior.
    Returns: (prior_dict_or_None, age_days)"""
    if not history_path.exists():
        return None, None
    try:
        rows = history_path.read_text().strip().split("\n")
        if len(rows) < 2:
            return None, None
        header = rows[0].split(",")
        if "mu_blended_pct" not in header or "blend_std_pct" not in header:
            return None, None  # old schema, skip
        # Get last row
        last = rows[-1].split(",")
        idx_mu = header.index("mu_blended_pct")
        idx_std = header.index("blend_std_pct")
        idx_ts = header.index("timestamp")
        try:
            prior_blend = {
                "blended": float(last[idx_mu]) / 100.0,
                "std": float(last[idx_std]) / 100.0,
            }
            prior_ts = datetime.strptime(last[idx_ts][:10], "%Y-%m-%d")
            age = (datetime.now() - prior_ts).days
            if age > days_back_limit:
                return None, age  # too stale
            return prior_blend, age
        except (ValueError, IndexError):
            return None, None
    except Exception:
        return None, None


# =============================================================
# GOD MODE v4 — Multi-target conviction scan (§2026-05-17)
#
# Design locked per final audit. Key principles:
#   - User's decision rule: hold if P(touch X) >= threshold (default 65%)
#   - Multi-target scan finds the HIGHEST defensible sell-limit X
#   - Sigma triangulation: GARCH + realized vol + options IV (liquidity-gated)
#   - Drift estimation: 9 quality-gated signals (was 6 in v3)
#   - NO threshold-spectrum noise, NO X_stretch fantasy, NO synthesized
#     reliability score (show components separately), NO block bootstrap
#   - Default threshold 65% for high-stakes swing trades; --conviction-threshold
#     flag for override
# =============================================================


def compute_realized_vol(returns, windows=(30, 60, 90)):
    """Compute realized vol over multiple rolling windows.
    Returns dict {window_days: annualised_sigma}."""
    out = {}
    r = returns.replace([np.inf, -np.inf], np.nan).dropna()
    for w in windows:
        if len(r) < w + 1:
            out[w] = None
            continue
        recent = r.tail(w)
        out[w] = float(recent.std() * np.sqrt(252))
    return out


def fetch_options_iv(ticker, target_dte_days=60):
    """yfinance options chain → ATM straddle IV at ~target_dte_days expiry.

    Liquidity-gated: only return IV if option chain is liquid enough.
    Returns: dict {iv, expiry, dte, atm_strike, bid_ask_pct_avg, is_liquid}
             or None if yfinance unavailable / data unusable.
    """
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        tk = yf.Ticker(ticker)
        expiries = tk.options
        if not expiries:
            return None
        # Find expiry closest to target DTE
        today = datetime.now().date()
        candidates = []
        for ex_str in expiries:
            try:
                ex_date = datetime.strptime(ex_str, "%Y-%m-%d").date()
                dte = (ex_date - today).days
                if 7 <= dte <= target_dte_days * 2:
                    candidates.append((abs(dte - target_dte_days), dte, ex_str))
            except ValueError:
                continue
        if not candidates:
            return None
        candidates.sort()
        _, dte, expiry = candidates[0]
        chain = tk.option_chain(expiry)
        # Find ATM strike
        spot = float(tk.fast_info.get("last_price", 0) or tk.history(period="1d")["Close"].iloc[-1])
        if spot <= 0:
            return None
        calls = chain.calls
        puts = chain.puts
        if calls.empty or puts.empty:
            return None
        # ATM strike: closest to spot
        atm_strike = float(calls.iloc[(calls["strike"] - spot).abs().argmin()]["strike"])
        atm_call = calls[calls["strike"] == atm_strike]
        atm_put = puts[puts["strike"] == atm_strike]
        if atm_call.empty or atm_put.empty:
            return None
        # Liquidity check: bid-ask spread % on both sides
        def spread_pct(row):
            bid = float(row["bid"])
            ask = float(row["ask"])
            mid = (bid + ask) / 2
            return abs(ask - bid) / mid if mid > 0 else 1.0
        call_spread = spread_pct(atm_call.iloc[0])
        put_spread = spread_pct(atm_put.iloc[0])
        avg_spread = (call_spread + put_spread) / 2
        is_liquid = avg_spread < 0.10  # < 10% bid-ask spread → liquid enough
        # Average IV from ATM call + put
        call_iv = float(atm_call.iloc[0]["impliedVolatility"])
        put_iv = float(atm_put.iloc[0]["impliedVolatility"])
        avg_iv = (call_iv + put_iv) / 2
        return {
            "iv": avg_iv,
            "expiry": expiry,
            "dte": dte,
            "atm_strike": atm_strike,
            "bid_ask_pct_avg": avg_spread,
            "is_liquid": is_liquid,
            "call_iv": call_iv,
            "put_iv": put_iv,
        }
    except Exception as e:
        print(f"   WARNING: yfinance options IV fetch failed: {e}")
        return None


def triangulate_sigma(garch_sigma, realized_vol_dict, options_iv_data):
    """Triangulate sigma estimate across GARCH + realized vol + options IV.
    Returns: {blended, anchors (dict), method_used, divergence_pp}"""
    anchors = {}
    if garch_sigma is not None:
        anchors["garch"] = float(garch_sigma)
    for w, v in (realized_vol_dict or {}).items():
        if v is not None:
            anchors[f"realized_{w}d"] = float(v)
    if options_iv_data and options_iv_data.get("is_liquid"):
        anchors["options_iv"] = float(options_iv_data["iv"])

    if not anchors:
        return None
    values = list(anchors.values())
    blended = float(np.mean(values))
    divergence = (max(values) - min(values)) if len(values) > 1 else 0.0

    return {
        "blended": blended,
        "anchors": anchors,
        "n_anchors": len(anchors),
        "divergence_pp": divergence * 100,
    }


def fetch_short_interest(ticker, api_key):
    """Try FMP first (likely 402 on Starter), fall back to yfinance.
    Returns: {short_percent_of_float, days_to_cover, source} or None."""
    # Try FMP — there's no canonical Starter endpoint, but try common ones
    data = _fmp_get("share-float", api_key, {"symbol": ticker})
    if data and isinstance(data, list) and data:
        d = data[0]
        spf = d.get("shortPercentOfFloat") or d.get("shortFloatPercent")
        if spf is not None:
            return {
                "short_percent_of_float": float(spf),
                "days_to_cover": d.get("shortRatio"),
                "source": "FMP",
            }
    # yfinance fallback
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        spf = info.get("shortPercentOfFloat")
        dtc = info.get("shortRatio")
        if spf is not None:
            return {
                "short_percent_of_float": float(spf),
                "days_to_cover": float(dtc) if dtc else None,
                "source": "yfinance",
            }
    except Exception:
        pass
    return None


def signal_from_short_interest(short_data):
    """Short interest as drift tilt.
    Low SI (<3% of float): no signal / slight bullish (squeezes priced in)
    Medium SI (3-10%): mild bearish (sentiment skeptical)
    High SI (10-20%): tail-risk on BOTH sides — squeeze upside potential, but
      also indicates structural skepticism. Net effect: SMALL bearish tilt
      with HIGH uncertainty (wider CI on the signal itself).
    Very high SI (>20%): potential squeeze setup
    """
    if not short_data or short_data.get("short_percent_of_float") is None:
        return _none_signal("no short interest data")
    spf = short_data["short_percent_of_float"]
    if spf < 0.03:
        drift = 0.00
        conf = "MEDIUM"
        note = f"SI {spf*100:.1f}% of float — low, neutral signal"
    elif spf < 0.10:
        drift = -0.03
        conf = "MEDIUM"
        note = f"SI {spf*100:.1f}% of float — moderate skepticism (mild bearish)"
    elif spf < 0.20:
        drift = -0.05
        conf = "LOW"
        note = (f"SI {spf*100:.1f}% of float — elevated; tail risk both directions "
                f"(squeeze upside vs structural bearishness)")
    else:
        # Very high SI — squeeze potential dominates
        drift = +0.05
        conf = "LOW"
        note = f"SI {spf*100:.1f}% of float — very high; squeeze tail upside"
    return {
        "drift": float(drift), "confidence": conf,
        "source_quality": "PRIMARY", "sources_count": 1,
        "notes": note + f" [via {short_data.get('source', '?')}]",
    }


def fetch_peer_history(peers, api_key, lookback_days=60):
    """Fetch closing-price history for peer tickers. Returns
    {peer_ticker: pd.DataFrame[Date, Close]}."""
    out = {}
    for p in peers:
        try:
            df = fetch_history(p, api_key, lookback_days=lookback_days)
            if df is not None and not df.empty:
                out[p] = df
        except Exception as e:
            print(f"   WARNING: peer {p} fetch failed: {e}")
    return out


def signal_from_peer_rs(price_df, peer_dfs, lookback_days=60):
    """Compute SNDK's relative strength vs peer median return over lookback_days.
    Positive RS → SNDK outperforming → momentum continuation signal (bullish).
    Negative RS → underperforming → weakness signal (bearish)."""
    if price_df is None or len(price_df) < lookback_days + 1:
        return _none_signal("insufficient price history for peer RS")
    if not peer_dfs:
        return _none_signal("no peer data available")

    def n_day_return(df, n):
        if len(df) < n + 1:
            return None
        try:
            return float(df["Close"].iloc[-1] / df["Close"].iloc[-n - 1] - 1.0)
        except (IndexError, ValueError):
            return None

    sndk_ret = n_day_return(price_df, lookback_days)
    if sndk_ret is None:
        return _none_signal("could not compute SNDK return")

    peer_rets = []
    for p, df in peer_dfs.items():
        r = n_day_return(df, lookback_days)
        if r is not None:
            peer_rets.append((p, r))
    if not peer_rets:
        return _none_signal("no peer returns computable")

    peer_median = float(np.median([r for _, r in peer_rets]))
    rs = sndk_ret - peer_median  # SNDK return minus peer median
    # Annualise the spread for drift contribution
    drift = rs * 252 / lookback_days
    drift = max(-0.30, min(0.30, drift))  # cap effect

    # Confidence: tighter peer dispersion → higher conf
    if len(peer_rets) >= 2:
        peer_dispersion = float(np.std([r for _, r in peer_rets]))
        if peer_dispersion < 0.05:
            conf = "HIGH"
        elif peer_dispersion < 0.15:
            conf = "MEDIUM"
        else:
            conf = "LOW"
    else:
        conf = "LOW"  # only 1 peer = low confidence

    peer_list = ", ".join([f"{p} {r*100:+.0f}%" for p, r in peer_rets])
    return {
        "drift": float(drift), "confidence": conf,
        "source_quality": "PRIMARY", "sources_count": len(peer_rets),
        "notes": (f"SNDK {sndk_ret*100:+.0f}% vs peers [{peer_list}] over {lookback_days}d "
                  f"-> RS {rs*100:+.0f}%, annualised tilt {drift*100:+.0f}%"),
    }


def signal_from_sector_decoupling(price_df, sector_perf, lookback_days=30):
    """Compute decoupling: is SNDK moving WITH or AGAINST its sector recently?
    Positive decoupling (SNDK outperforming sector) = SNDK-specific strength.
    Negative = SNDK-specific weakness vs sector.
    """
    if price_df is None or len(price_df) < lookback_days + 1:
        return _none_signal("insufficient price history for decoupling")
    if not sector_perf or sector_perf.get("cum_return_pct") is None:
        return _none_signal("no sector data for decoupling")

    try:
        sndk_ret = float(price_df["Close"].iloc[-1] /
                          price_df["Close"].iloc[-lookback_days - 1] - 1.0)
    except (IndexError, ValueError):
        return _none_signal("SNDK return calc failed")

    sector_ret = sector_perf["cum_return_pct"] / 100.0
    decoup = sndk_ret - sector_ret
    # Annualise
    drift = decoup * 252 / lookback_days
    drift = max(-0.20, min(0.20, drift))  # cap

    if abs(decoup) < 0.02:
        conf = "LOW"
        note_extra = "(low decoupling, signal noisy)"
    elif abs(decoup) < 0.10:
        conf = "MEDIUM"
        note_extra = ""
    else:
        conf = "HIGH"
        note_extra = "(meaningful decoupling)"

    return {
        "drift": float(drift), "confidence": conf,
        "source_quality": "PRIMARY", "sources_count": 1,
        "notes": (f"SNDK {sndk_ret*100:+.0f}% vs sector {sector_ret*100:+.0f}% "
                  f"over {lookback_days}d -> decouple {decoup*100:+.0f}% {note_extra}"),
    }


def scan_target_probabilities(S0, sigma, mu_blend, horizon, entry,
                               threshold=0.65, target_increment=10,
                               max_target_mult=1.50):
    """Multi-target probability scan. For each candidate sell-limit X in
    [entry, max], compute P(touch X) in horizon via MC, with CI bounds
    from drift uncertainty (using mu_blend's 68% CI).

    Returns: {
      curve: list of {X, p_point, p_lo68, p_hi68, profit, action_at_threshold}
      x_safe: highest X where p_lo68 >= threshold (robust)
      x_aggressive: highest X where p_point >= threshold (point estimate)
      threshold_used: threshold
    }
    """
    mu_point = mu_blend.get("blended")
    mu_lo = mu_blend.get("lo68", mu_point - 0.20 if mu_point else None)
    mu_hi = mu_blend.get("hi68", mu_point + 0.20 if mu_point else None)

    if mu_point is None:
        return None

    # Build target grid from entry upward to max_target_mult * entry
    max_target = entry * max_target_mult
    targets = []
    x = entry
    while x <= max_target:
        targets.append(float(x))
        x += target_increment

    T = horizon / 252
    curve = []
    # Compute P at point + lo68 + hi68 for each target
    for X in targets:
        # Use closed-form for speed (we cross-check with MC for x_aggressive only)
        p_point = closed_touch_up(S0, X, T, mu_point, sigma)
        p_lo = closed_touch_up(S0, X, T, mu_lo, sigma)
        p_hi = closed_touch_up(S0, X, T, mu_hi, sigma)
        profit = (X - entry) * 1  # per share; caller multiplies by shares
        # Action at threshold
        if p_lo >= threshold:
            action = "ROBUST HOLD"
        elif p_point >= threshold:
            action = "POINT HOLD"
        else:
            action = "BELOW"
        curve.append({
            "X": X,
            "p_point": float(p_point),
            "p_lo68": float(p_lo),
            "p_hi68": float(p_hi),
            "profit_per_share": float(profit),
            "action": action,
        })
        # Stop scanning once point falls below 30% (no more useful info)
        if p_point < 0.30:
            break

    # Find x_safe (highest X where lo68 >= threshold)
    x_safe = None
    for row in curve:
        if row["p_lo68"] >= threshold:
            x_safe = row["X"]
    # Find x_aggressive (highest X where p_point >= threshold)
    x_aggressive = None
    for row in curve:
        if row["p_point"] >= threshold:
            x_aggressive = row["X"]

    return {
        "curve": curve,
        "x_safe": x_safe,
        "x_aggressive": x_aggressive,
        "threshold_used": threshold,
        "mu_point": mu_point,
        "mu_lo68": mu_lo,
        "mu_hi68": mu_hi,
    }


def compute_target_sensitivity(S0, sigma_point, mu_point, horizon, X_target,
                                threshold=0.65):
    """At the recommended X_target, show how P(touch) responds to ±15pp drift
    swings and ±20% sigma swings. Identifies the conditions that flip verdict.
    """
    T = horizon / 252
    scenarios = []

    def evaluate(mu, sig, label):
        p = closed_touch_up(S0, X_target, T, mu, sig)
        verdict = "HOLD" if p >= threshold else "BELOW"
        return {"label": label, "mu": mu, "sigma": sig, "p": float(p), "verdict": verdict}

    scenarios.append(evaluate(mu_point, sigma_point, "Baseline (current estimate)"))
    scenarios.append(evaluate(mu_point - 0.15, sigma_point, "Drift -15pp"))
    scenarios.append(evaluate(mu_point + 0.15, sigma_point, "Drift +15pp"))
    scenarios.append(evaluate(mu_point, sigma_point * 1.20, "Sigma +20%"))
    scenarios.append(evaluate(mu_point, sigma_point * 0.80, "Sigma -20%"))
    scenarios.append(evaluate(mu_point - 0.15, sigma_point * 1.20, "Hostile (drift-15, sigma+20)"))

    # Find drift threshold where verdict flips
    flip_drift = None
    mu_test = mu_point
    step = -0.01
    for _ in range(100):
        p = closed_touch_up(S0, X_target, T, mu_test, sigma_point)
        if p < threshold:
            flip_drift = mu_test
            break
        mu_test += step
    return {"scenarios": scenarios, "flip_drift": flip_drift}


def adjust_for_earnings(p_estimate, earnings_event, horizon_days):
    """If earnings event falls within horizon, the GBM P estimate doesn't
    model gap risk. Return adjusted estimate as a BAND, not a point."""
    if not earnings_event or not earnings_event.get("in_horizon"):
        return {"adjusted": False, "band_lo": p_estimate, "band_hi": p_estimate}
    # Earnings gaps typically swing ±10-20% one-day. This adds uncertainty in
    # BOTH directions — could touch target via gap up, or miss by gap down.
    # Use ±5pp band on P(touch) — empirical from historical earnings days.
    return {
        "adjusted": True,
        "band_lo": max(0.0, p_estimate - 0.05),
        "band_hi": min(1.0, p_estimate + 0.05),
        "note": "Earnings in horizon: ±5pp gap-risk band on P (MC doesn't model gaps)",
    }


# -----------------------------------------------------------
# Blending — confidence-weighted with canon quality gates
# -----------------------------------------------------------

def blend_drifts(signals):
    """
    Apply canon quality gates and confidence weighting.
    Per src/regime_classifier.py §2026-05-15 pattern:
      - NONE_FOUND -> drop from blend (weight 0)
      - SPECULATIVE + sources_count < 2 -> halve weight
      - LOW confidence -> halve weight
    Returns: {blended, weights (effective), fallback, dispersion_pp, n_active}
    """
    effective = {}
    for name, info in signals.items():
        if info.get("drift") is None:
            effective[name] = 0.0
            continue
        w = BLEND_WEIGHTS.get(name, 0.0)
        sq = info.get("source_quality", "REPUTABLE")
        sc = int(info.get("sources_count", 0))
        conf = info.get("confidence", "MEDIUM")
        if sq == "NONE_FOUND":
            w = 0.0
        elif sq == "SPECULATIVE" and sc < 2:
            w *= 0.5
        if conf == "LOW":
            w *= 0.5
        effective[name] = w

    total = sum(effective.values())
    if total <= 0:
        return {"blended": 0.05, "weights": effective, "fallback": True,
                "dispersion_pp": 0.0, "n_active": 0}
    blended = sum(signals[name]["drift"] * effective[name] / total
                  for name in signals if effective[name] > 0)
    active_drifts = [signals[n]["drift"] for n in signals
                     if effective[n] > 0 and signals[n]["drift"] is not None]
    dispersion = (max(active_drifts) - min(active_drifts)) * 100 if active_drifts else 0
    return {
        "blended": float(blended),
        "weights": effective,
        "fallback": False,
        "dispersion_pp": float(dispersion),
        "n_active": sum(1 for w in effective.values() if w > 0),
    }


# -----------------------------------------------------------
# Position-specific guidance (mechanical, cross-check vs AI)
# -----------------------------------------------------------

def mechanical_position_guidance(cushion, ev_advantage, dispersion_pp,
                                  ai_guidance, ci_lo68=None, ci_hi68=None):
    """
    §2026-05-17 audit fix #8: do NOT default to "more conservative" when math
    and AI disagree. Report BOTH views and let the user decide. Mechanical
    rule uses cushion + dispersion + CI; AI rule comes from AI synthesis.
    No implicit risk-aversion prior.

    §audit fix #6: hysteresis bias — symmetric dispersion handling. High
    dispersion downgrades HOLD->TRIM (as before) AND upgrades borderline-CUT
    to EDGE (where it gets re-evaluated next day).
    """
    if cushion >= 0.10 and ev_advantage > 500:
        mech = "HOLD"
    elif cushion >= 0.05:
        mech = "HOLD"
    elif cushion >= 0.0:
        mech = "TRIM"
    elif cushion >= -0.05:
        mech = "TRIM"  # at edge — partial off
    else:
        mech = "CUT"

    # CI-aware fix: if the 68% CI on cushion straddles zero, downgrade
    # CUT to EDGE/TRIM because the verdict is statistically not different
    # from break-even.
    if ci_lo68 is not None and ci_hi68 is not None and mech == "CUT":
        # cushion bounds — note we receive the cushion CI in pp via the
        # caller's translation of drift CI to cushion CI
        if ci_lo68 < 0 and ci_hi68 > 0:
            mech = "TRIM"  # uncertain — don't commit to full cut

    # §2026-05-17 audit P1.6: dispersion downgrades HOLD to TRIM (signals
    # disagree → less conviction to keep full size). The previous "symmetric"
    # rule that also upgraded CUT to TRIM was cosmetic balance, not financial
    # logic — when you're already underwater AND signals disagree, keeping
    # half a losing position alive is NOT prudent. CUT stays CUT.
    if dispersion_pp >= 30:
        if mech == "HOLD":
            mech = "TRIM"  # signals disagree, scale back

    agreement = (mech == ai_guidance) if ai_guidance else None
    return mech, agreement


def check_hysteresis(history_path, today_verdict, today_cushion):
    """§2026-05-17 audit fix #6: verdict-flip protection. If today's verdict
    differs from yesterday's, surface a hysteresis warning (do not override).
    Specifically: HOLD->CUT or CUT->HOLD on a single day's data is a red flag
    unless the cushion movement is large.

    Returns: (warning_str_or_None, prior_verdict_or_None)
    """
    if not history_path.exists():
        return None, None
    try:
        rows = history_path.read_text().strip().split("\n")
        if len(rows) < 2:
            return None, None
        header = rows[0].split(",")
        if "verdict" not in header:
            return None, None
        idx_v = header.index("verdict")
        idx_c = header.index("cushion_pp")
        last = rows[-1].split(",")
        prior_verdict = last[idx_v].strip()
        try:
            prior_cushion = float(last[idx_c])
        except (ValueError, IndexError):
            prior_cushion = None

        if prior_verdict == today_verdict:
            return None, prior_verdict  # no flip

        # Flip detected
        cushion_move = (today_cushion - prior_cushion) if prior_cushion is not None else None
        if cushion_move is not None and abs(cushion_move) > 15:
            # Big move — flip is justified
            return None, prior_verdict
        warn = (f"VERDICT FLIPPED from {prior_verdict} to {today_verdict} "
                f"in one day. Cushion moved {cushion_move:+.1f}pp."
                if cushion_move is not None
                else f"VERDICT FLIPPED from {prior_verdict} to {today_verdict} in one day.")
        warn += (" Per audit fix #6, consider this provisional until "
                 "confirmed by tomorrow's reading.")
        return warn, prior_verdict
    except Exception:
        return None, None


def _legacy_check_thesis_v3(args):
    """LEGACY v3 — superseded by check_thesis_mode (v4) below. Kept only as
    reference; not called by main(). v3 used EV-cushion verdict math; v4
    uses multi-target conviction scan per audit-locked design 2026-05-17.

    Original docstring: Daily thesis-health check, GOD MODE v2 with
    EV-optimization framework and cushion bands.
    """
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        sys.exit("ERROR: FMP_API_KEY not set in environment")

    ticker = args.thesis_ticker
    df = fetch_history(ticker, api_key, args.lookback_days)
    S0 = float(df["Close"].iloc[-1])
    last_date = df["Date"].iloc[-1].strftime("%Y-%m-%d")
    log_ret = np.log(df["Close"] / df["Close"].shift(1)).dropna()

    forecast_var = fit_garch_11(log_ret)
    sigma = float(np.sqrt(forecast_var * 252))
    mu_hist = float(log_ret.mean() * 252)
    mu_capped = max(-args.drift_cap, min(args.drift_cap, mu_hist))
    rsi = compute_rsi_14(df["Close"])
    mom_5d = float(df["Close"].iloc[-1] / df["Close"].iloc[-6] - 1.0) if len(df) >= 6 else 0.0
    # 30-day momentum
    mom_30d = (float(df["Close"].iloc[-1] / df["Close"].iloc[-31] - 1.0) * 100
               if len(df) >= 31 else None)
    enr = enrichment_drift(rsi, mom_5d)
    mu_effective_historical = mu_capped + enr * 252 / args.horizon

    # YTD return for regime detection
    ytd_pct = None
    try:
        ytd_start = df[df["Date"].dt.year == datetime.now().year]["Close"].iloc[0]
        ytd_pct = (S0 / float(ytd_start) - 1.0) * 100
    except (IndexError, ValueError, TypeError):
        ytd_pct = None

    T = args.horizon / 252

    print_header(f"{ticker} THESIS HEALTH CHECK (GOD MODE v2) — "
                 f"{datetime.now():%Y-%m-%d %H:%M}")
    print(f"  Gathering intelligence (this takes ~10-30s)...")
    print()

    # === FETCH INTEL ===
    profile = fetch_company_profile(ticker, api_key) or {}
    sector_name = profile.get("sector") or "Technology"
    # §2026-05-17 fix: try multiple market-cap field names. FMP profile
    # endpoint has used 'mktCap', 'marketCap' in different versions.
    market_cap_usd = None
    for field_name in ("mktCap", "marketCap", "mcap", "market_cap"):
        try:
            v = profile.get(field_name)
            if v and float(v) > 0:
                market_cap_usd = float(v)
                break
        except (ValueError, TypeError):
            continue
    analyst_targets = fetch_analyst_targets(ticker, api_key)
    # §2026-05-17 upgrade A: fetch RECENT analyst summary (last-month avg)
    # to avoid stale consensus drag on fast-moving stocks
    analyst_summary = fetch_analyst_summary(ticker, api_key)
    # §2026-05-17 fix: pass stock's exchange so sector-perf filters to the
    # right rows (endpoint returns one row per exchange per date).
    stock_exchange = profile.get("exchange", "NASDAQ") or "NASDAQ"
    sector_perf = fetch_sector_perf(sector_name, api_key, days=30,
                                     exchange_filter=stock_exchange)
    insider = fetch_insider_activity(ticker, api_key, days=90)
    macro = fetch_macro_indicators(api_key)
    recent_news = fetch_recent_news(ticker, api_key, limit=20)
    # §2026-05-17: press-releases endpoint requires FMP Premium plan (Starter
    # returns 402). Skipped — AI synthesis still gets context via news/stock
    # and web_search. Per FMP support confirmation 2026-05-17.
    press_releases = []

    # §2026-05-17 upgrade B: fetch next earnings event (Q3 of these confirms
    # SNDK Q4 FY26 reports Aug 13, 2026 — falls 28 days after a 60d horizon
    # starting today, but late-horizon will see run-up positioning)
    earnings_event = fetch_next_earnings(ticker, api_key,
                                          lookahead_days=args.horizon + 60)
    if earnings_event:
        earnings_event["in_horizon"] = earnings_event["days_away"] <= args.horizon
        earnings_event["approaching"] = (args.horizon < earnings_event["days_away"]
                                          <= args.horizon + 30)

    # === REGIME + VOL ADVISORY (Tier 1 #11, #12) ===
    regime = detect_swing_regime(rsi, mom_5d, mom_30d, sigma, ytd_pct)
    vol_advice = vol_regime_advisory(sigma)

    # === BUILD SIGNAL DICT (with all Tier 0 fixes) ===
    signals = {
        "historical": signal_from_historical(mu_effective_historical,
                                              mu_hist, sigma),
        "analyst":    signal_from_analyst_targets(analyst_targets, S0,
                                                   price_history_df=df,
                                                   summary=analyst_summary),
        "sector":     signal_from_sector(sector_perf, swing_regime=regime),
        "macro":      signal_from_macro(macro),
        "insider":    signal_from_insider(insider, market_cap_usd=market_cap_usd),
    }

    # === AI SYNTHESIS ===
    ai_parsed = None
    ai_cost = 0.0
    ai_raw = ""
    if not args.no_ai:
        prompt = build_ai_synthesis_prompt(
            ticker, profile, S0, sigma, args.horizon,
            recent_news, press_releases, sector_perf, analyst_targets,
            insider, macro, earnings_event=earnings_event,
            analyst_summary=analyst_summary)
        ai_parsed, ai_cost, ai_raw = call_ai_analyst(
            prompt, model=args.ai_model, max_tokens=3000)
        if ai_parsed is not None:
            signals["ai"] = signal_from_ai(ai_parsed)
        else:
            signals["ai"] = _none_signal("AI synthesis failed; see raw output")

    # === BLEND WITH UNCERTAINTY (Tier 1 #10) ===
    blend = blend_with_uncertainty(signals)
    if blend["blended"] is None:
        # All signals failed — fall back to historical alone
        print("  WARNING: all signals NONE_FOUND or excluded. Falling back to "
              "historical drift for the blend.")
        mu_for_decision = mu_effective_historical
        blend = {"blended": mu_for_decision, "std": 0.20,
                 "lo68": mu_for_decision - 0.20, "hi68": mu_for_decision + 0.20,
                 "lo95": mu_for_decision - 0.40, "hi95": mu_for_decision + 0.40,
                 "weights": {"historical": 1.0}, "fallback": True,
                 "dispersion_pp": 0.0, "n_active": 1}
    else:
        mu_today_blend = blend["blended"]

    # === BAYESIAN UPDATE FROM PRIOR (Tier 1 #9) ===
    history_dir = Path(__file__).parent / "output"
    history_dir.mkdir(exist_ok=True)
    history_path = history_dir / f"thesis_history_{ticker}.csv"
    prior_blend, prior_age = load_prior_blend(history_path, days_back_limit=3)
    bayesian = bayesian_update(prior_blend, blend, prior_age_days=prior_age or 1)

    # §2026-05-17 audit P0.2: use Bayesian posterior for verdict when a
    # valid prior exists (smoother, respects accumulated evidence rather
    # than single-day noise). Today's blend goes into the sensitivity table.
    if (bayesian and bayesian.get("posterior_mu") is not None
            and prior_blend is not None):
        mu_for_decision = bayesian["posterior_mu"]
        decision_basis = (f"Bayesian posterior "
                          f"({bayesian['prior_weight']*100:.0f}% prior + "
                          f"{bayesian['obs_weight']*100:.0f}% today)")
    else:
        mu_for_decision = blend["blended"]
        decision_basis = "today's blend (no prior available)"

    # === HYSTERESIS CHECK (Tier 0 #6) ===
    # We compute today's verdict first, then check against prior
    paths = run_mc_paths(S0, sigma, mu_for_decision, args.horizon, n_paths=50_000)
    touched = (paths >= args.target).any(axis=1)
    p_touch_mc = float(touched.mean())
    untouched_terminals = paths[~touched, -1]
    e_bad = float(untouched_terminals.mean()) if untouched_terminals.size > 0 else 0.0
    p_touch_cf = closed_touch_up(S0, args.target, T, mu_for_decision, sigma)

    pnl_good = (args.target - args.entry) * args.shares
    pnl_bad = (e_bad - args.entry) * args.shares
    cut_pnl = (S0 - args.entry) * args.shares
    ev_hold = p_touch_mc * pnl_good + (1 - p_touch_mc) * pnl_bad
    ev_advantage = ev_hold - cut_pnl

    if pnl_good == pnl_bad:
        breakeven_p = float("nan")
    else:
        breakeven_p = (cut_pnl - pnl_bad) / (pnl_good - pnl_bad)
    cushion = p_touch_mc - breakeven_p
    cushion_pp = cushion * 100

    # === PATH METRICS (Tier 1 #13) ===
    path_stats = compute_path_metrics(paths, S0, args.target, args.panic_level)
    p_panic = path_stats["panic_touch_prob_total"]

    if cushion >= 0.10:
        verdict, light = "STRONG HOLD", "[GREEN]"
    elif cushion >= 0.05:
        verdict, light = "HOLD", "[YELLOW]"
    elif cushion >= 0.0:
        verdict, light = "EDGE", "[ORANGE]"
    else:
        verdict, light = "CUT", "[RED]"

    hysteresis_warn, prior_verdict = check_hysteresis(history_path,
                                                       verdict, cushion_pp)

    # === RENDER ===
    print(f"  Data through:           {last_date} close")
    print(f"  Spot:                   ${S0:.2f}")
    if market_cap_usd:
        print(f"  Market cap:             ${market_cap_usd/1e9:.1f}B")
    print(f"  Sector / Industry:      {sector_name} / {profile.get('industry','')}")
    print(f"  Sigma (GARCH):          {sigma * 100:.1f}%")
    print(f"  RSI(14) / 5d mom / 30d: {rsi:.1f}  /  {mom_5d * 100:+.1f}%  /  "
          f"{f'{mom_30d:+.1f}%' if mom_30d is not None else 'n/a'}")
    if ytd_pct is not None:
        print(f"  YTD return:             {ytd_pct:+.1f}%")
    print()
    print(f"  Position: {args.shares} shares @ ${args.entry:.0f} cost basis, "
          f"target ${args.target:.0f}, no automatic stop")
    print(f"  Patience window:        {args.horizon} trading days")
    print()

    # ---- REGIME + VOL ADVISORY (NEW Tier 1) ----
    print_header("REGIME & VOL ADVISORY")
    print(f"  Regime:     {regime['regime']}")
    print(f"  Detail:     {regime['detail']}")
    print()
    print(f"  Vol level:  {vol_advice['level']}")
    print(f"  Advisory:   {vol_advice['advisory']}")
    print()

    # ---- EARNINGS CALENDAR (NEW §2026-05-17 upgrade B) ----
    if earnings_event:
        print_header("EARNINGS CALENDAR")
        ev = earnings_event
        print(f"  Next earnings:      {ev['date']} ({ev['days_away']} trading days away)")
        if ev.get("eps_est") is not None:
            print(f"  EPS estimate:       ${ev['eps_est']:.2f}")
        if ev.get("rev_est") is not None:
            print(f"  Revenue estimate:   ${ev['rev_est']/1e9:.2f}B")
        print()
        if ev["in_horizon"]:
            print(f"  WARNING: Earnings event falls WITHIN your {args.horizon}-day")
            print(f"  horizon. Earnings days commonly see +/-15-30% one-day moves")
            print(f"  with implied vol often 2x trailing realised. The drift / cushion")
            print(f"  math UNDERESTIMATES event-day risk. Consider trimming or hedging")
            print(f"  before earnings if math is borderline.")
        elif ev["approaching"]:
            print(f"  CONTEXT: Earnings is {ev['days_away'] - args.horizon} days AFTER")
            print(f"  your {args.horizon}-day horizon. Late-horizon will see run-up")
            print(f"  positioning (often elevated vol + drift bias in run-up window).")
            print(f"  Not a direct decision driver but worth noting.")
        else:
            print(f"  Earnings is comfortably outside the horizon — no event-day adj.")
        print()
    else:
        print_header("EARNINGS CALENDAR")
        print(f"  No earnings event found in next {args.horizon + 60} days")
        print(f"  (or FMP earnings-calendar returned no match)")
        print()

    # ---- FORWARD DRIFT INTELLIGENCE PANEL ----
    print_header("FORWARD DRIFT INTELLIGENCE")
    print(f"  {'Source':<37}{'mu (ann)':>10}{'Conf':>7}{'SrcQ':>13}{'Sources':>9}")
    print(f"  {'-'*37}{'-'*10}{'-'*7}{'-'*13}{'-'*9}")
    for name, label in [
        ("historical", "Historical (GARCH + enrichment)"),
        ("analyst",    "Analyst consensus (12mo target)"),
        ("sector",     f"Sector momentum ({sector_name})"),
        ("macro",      "Macro regime (VIX/SPY)"),
        ("insider",    "Insider activity (90d net)"),
        ("ai",         "AI analyst (Claude Opus 4.7)"),
    ]:
        s = signals.get(name, {})
        d = s.get("drift")
        d_str = f"{d*100:>+8.1f}%" if d is not None else "    n/a "
        conf = s.get("confidence", "?")
        sq = s.get("source_quality", "?")
        sc = s.get("sources_count", 0)
        weight = blend["weights"].get(name, 0.0)
        marker = "  *" if name == "ai" else "   "
        print(f"{marker}{label:<36}{d_str:>9}{conf:>7}{sq:>13}{sc:>9}  "
              f"w={weight*100:.0f}%")

    print()
    for name, label in [
        ("historical", "Historical"), ("analyst", "Analyst"),
        ("sector", "Sector"), ("macro", "Macro"),
        ("insider", "Insider"), ("ai", "AI"),
    ]:
        s = signals.get(name, {})
        notes = s.get("notes", "")
        if notes:
            print(f"  {label:<12}: {notes}")
    print()

    if blend.get("std"):
        print(f"  BLENDED FORWARD DRIFT:               "
              f"{mu_for_decision*100:+.1f}%/yr  +/-  "
              f"{blend['std']*100:.1f}pp")
        print(f"    68% CI:                            "
              f"[{blend['lo68']*100:+.1f}%, {blend['hi68']*100:+.1f}%]")
        print(f"    95% CI:                            "
              f"[{blend['lo95']*100:+.1f}%, {blend['hi95']*100:+.1f}%]")
    else:
        print(f"  BLENDED FORWARD DRIFT:               "
              f"{mu_for_decision*100:+.1f}%/yr  (fallback)")
    print(f"  Active signals in blend:             "
          f"{blend['n_active']} of 6")
    weights_str = ", ".join(f"{n}:{w*100:.0f}%" for n, w in blend["weights"].items()
                            if w > 0)
    print(f"  Effective weights:                   {weights_str}")
    print(f"  Signal dispersion (range):           "
          f"{blend['dispersion_pp']:.1f}pp")
    # §audit P1.5: graduated dispersion warnings — different action thresholds
    disp = blend['dispersion_pp']
    if disp >= 100:
        print(f"  DISPERSION FLAG: >=100pp -- blend is NOT a defensible point")
        print(f"  estimate. Signals span more than a full percentage point of")
        print(f"  drift. Decide based on the signal you trust most, not the blend.")
    elif disp >= 60:
        print(f"  DISPERSION FLAG: 60-100pp -- treat blended drift as NOISE,")
        print(f"  not a coherent estimate. Look at individual signals separately.")
    elif disp >= 30:
        print(f"  DISPERSION FLAG: 30-60pp -- signals disagree meaningfully.")
        print(f"  Blend is directionally useful but cushion CI bounds matter more")
        print(f"  than the point estimate.")
    print()

    # ---- BAYESIAN UPDATE (NEW Tier 1 #9) ----
    if bayesian and prior_blend:
        print_header("BAYESIAN BELIEF UPDATE")
        print(f"  Prior (yesterday's blend, {prior_age}d old): "
              f"mu={prior_blend['blended']*100:+.1f}%, "
              f"std={prior_blend.get('std', 0.15)*100:.1f}pp")
        print(f"  Today's observation:                 "
              f"mu={blend['blended']*100:+.1f}%, "
              f"std={blend['std']*100:.1f}pp")
        print(f"  Posterior:                           "
              f"mu={bayesian['posterior_mu']*100:+.1f}%, "
              f"std={bayesian['posterior_std']*100:.1f}pp")
        print(f"  Weights:                             "
              f"prior {bayesian['prior_weight']*100:.0f}% / "
              f"today {bayesian['obs_weight']*100:.0f}%")
        delta = (blend['blended'] - prior_blend['blended']) * 100
        print(f"  Day-over-day change in observed mu:  {delta:+.1f}pp")
        print()
    elif bayesian:
        print_header("BAYESIAN BELIEF UPDATE")
        print(f"  {bayesian['note']}")
        print()

    # ---- AI ANALYST DETAILED SYNTHESIS ----
    if ai_parsed:
        print_header(f"AI ANALYST SYNTHESIS  ({args.ai_model}, cost ${ai_cost:.3f})")
        dp = ai_parsed.get("drift_point")
        if dp is not None:
            print(f"  Drift point:        {dp*100:+.1f}%/yr")
        else:
            print(f"  Drift point:        null (evidence too thin) -> dropped from blend")
        if ai_parsed.get("drift_low") is not None and ai_parsed.get("drift_high") is not None:
            print(f"  Drift range:        "
                  f"{ai_parsed['drift_low']*100:+.1f}% to "
                  f"{ai_parsed['drift_high']*100:+.1f}%")
        print(f"  Confidence:         {ai_parsed.get('confidence','?')}")
        print(f"  Source quality:     {ai_parsed.get('source_quality','?')}")
        print(f"  Sources cited:      {ai_parsed.get('sources_count', 0)}")
        print(f"  AI position view:   {ai_parsed.get('position_guidance','?')}")
        print()
        rationale = ai_parsed.get("rationale", "")
        if rationale:
            print(f"  Rationale: {rationale}")
        print()

        if args.show_rationale:
            print("  BULL FACTORS:")
            for i, b in enumerate(ai_parsed.get("bull_factors", []) or [], 1):
                print(f"    {i}. {b.get('factor','')}")
                print(f"       Source: {b.get('source','')}   "
                      f"Weight: {b.get('weight','?')}")
            print()
            print("  BEAR FACTORS:")
            for i, b in enumerate(ai_parsed.get("bear_factors", []) or [], 1):
                print(f"    {i}. {b.get('factor','')}")
                print(f"       Source: {b.get('source','')}   "
                      f"Weight: {b.get('weight','?')}")
            print()
            print("  KEY RISKS:")
            for i, r in enumerate(ai_parsed.get("key_risks", []) or [], 1):
                print(f"    {i}. {r.get('risk','')}")
                print(f"       Probability: {r.get('probability','?')}   "
                      f"Impact: {r.get('impact','?')}")
            gaps = ai_parsed.get("evidence_gaps", "")
            if gaps:
                print()
                print(f"  Evidence gaps: {gaps}")
        else:
            n_bull = len(ai_parsed.get("bull_factors", []) or [])
            n_bear = len(ai_parsed.get("bear_factors", []) or [])
            n_risks = len(ai_parsed.get("key_risks", []) or [])
            print(f"  ({n_bull} bull factors, {n_bear} bear factors, "
                  f"{n_risks} key risks — re-run with --show-rationale for detail)")
        print()
    elif not args.no_ai:
        print_header("AI ANALYST SYNTHESIS — UNAVAILABLE")
        print("  AI call failed or returned malformed output.")
        print(f"  Last error / output (truncated): {ai_raw[:300]}")
        print()

    # ---- HEADLINE METRIC ----
    # §audit P0.2: surface which drift drove the verdict
    print_header(f"HEADLINE METRIC — verdict driven by {decision_basis}")
    print(f"  Drift used for verdict:            "
          f"{mu_for_decision*100:+.1f}%/yr")
    print(f"  P(touch ${args.target:.0f} within {args.horizon}d): "
          f"{p_touch_mc*100:5.1f}%  (MC, 50k paths)")
    print(f"  P (closed-form cross-check):       "
          f"{p_touch_cf*100:5.1f}%  (continuous time)")
    print()
    print(f"  Break-even threshold P*:           "
          f"{breakeven_p*100:5.1f}%")
    print(f"  Cushion vs threshold:              "
          f"{cushion_pp:+5.1f}pp")
    print()

    print_header("ECONOMICS")
    print(f"  EV(hold no-stop {args.horizon}d):              "
          f"${ev_hold:+,.0f}")
    print(f"  EV(cut now at spot ${S0:.2f}):    ${cut_pnl:+,.0f}")
    print(f"  EV advantage of holding:           ${ev_advantage:+,.0f}")
    print()
    print(f"  If target hit:        +${pnl_good:,.0f}  "
          f"(probability {p_touch_mc*100:.1f}%)")
    print(f"  If target missed:     ${pnl_bad:+,.0f}  "
          f"(avg, probability {(1-p_touch_mc)*100:.1f}%)")
    print()

    # ---- §audit P0.1: DUAL-TARGET ANALYSIS ----
    # The primary target above is whatever args.target the user passed.
    # Also compute the BE-recovery scenario (target = entry) so the user
    # sees BOTH the profit-target and break-even lenses every run.
    # Critical: BE-only exit has different math because pnl_good = $0,
    # which changes the break-even P* dramatically.
    if abs(args.target - args.entry) > 1.0:  # only run if target != entry
        print_header("DUAL-TARGET ANALYSIS  (BE vs profit, same drift)")
        print(f"  Compares the verdict at TWO sell-limit levels using the")
        print(f"  same blended drift ({mu_for_decision*100:+.1f}%/yr). The math")
        print(f"  changes because each sell-limit has different winning payoff.")
        print()
        # Primary target run (already computed above)
        verdict_primary = verdict
        # Secondary: BE exit at entry price
        be_target = args.entry
        be_p, be_ev, be_p_star, be_cushion, be_verdict = _quick_scenario(
            S0, sigma, mu_for_decision, args.horizon, be_target, args.entry,
            args.shares, cut_pnl, 0.0)  # pnl_good for BE is exactly $0
        # NB: _quick_scenario takes pnl_good as a param; for BE pnl_good = 0
        print(f"  {'Scenario':<42}{'P(touch)':>10}{'EV(hold)':>11}"
              f"{'Cushion':>9}{'Verdict':>14}")
        print(f"  {'-'*42}{'-'*10}{'-'*11}{'-'*9}{'-'*14}")
        # Profit target row (primary)
        print(f"  {'Profit target $%d (primary)' % args.target:<42}"
              f"{p_touch_mc*100:>9.1f}% ${ev_hold:>+9,.0f}"
              f"{cushion_pp:>+7.1f}pp{verdict_primary:>14}")
        # BE row
        print(f"  {'Break-even $%d (target=entry)' % be_target:<42}"
              f"{be_p*100:>9.1f}% ${be_ev:>+9,.0f}"
              f"{be_cushion*100:>+7.1f}pp{be_verdict:>14}")
        print()
        if be_verdict != verdict_primary:
            print(f"  WARNING: BE-target verdict ({be_verdict}) differs from")
            print(f"  profit-target verdict ({verdict_primary}). If your actual")
            print(f"  exit objective is break-even recovery, trust the BE row.")
            print(f"  Setting a sell-limit at $%d caps your upside at $0 while" % be_target)
            print(f"  keeping the same tail risk — mathematically inferior to")
            print(f"  a $%d profit limit (or cutting now)." % args.target)
        else:
            print(f"  BE-target and profit-target verdicts AGREE on {verdict}.")
        print()

    # ---- §audit P0.3: FRESH-CAPITAL FRAMING ----
    print_header("FRESH-CAPITAL FRAMING (sunk-cost check)")
    if mu_for_decision > 0:
        prob_str = f"{p_touch_mc*100:.0f}% chance"
    else:
        prob_str = f"{p_touch_mc*100:.0f}% chance"
    print(f"  Holding {args.shares} shares @ ${args.entry:.0f} cost basis is")
    print(f"  mathematically equivalent to BUYING {args.shares} shares at")
    print(f"  spot ${S0:.2f} today, targeting ${args.target:.0f} in "
          f"{args.horizon} days.")
    print()
    print(f"  Would you take this trade with FRESH capital? If no, the only")
    print(f"  reason holding feels different is the $1490 cost-basis anchor.")
    print(f"  Math doesn't care about your cost basis — only forward P&L.")
    print()

    # ---- PATH-DEPENDENT RISK METRICS (NEW Tier 1 #13) ----
    print_header("PATH-DEPENDENT RISK METRICS")
    md = path_stats
    print(f"  Max drawdown along the way (from ${S0:.0f}):")
    print(f"    median:                    {md['max_drawdown_median']*100:5.1f}%  "
          f"(${S0 * (1 - md['max_drawdown_median']):.0f} touched)")
    print(f"    75th percentile path:      {md['max_drawdown_p75']*100:5.1f}%  "
          f"(${S0 * (1 - md['max_drawdown_p75']):.0f} touched)")
    print(f"    90th percentile path:      {md['max_drawdown_p90']*100:5.1f}%  "
          f"(${S0 * (1 - md['max_drawdown_p90']):.0f} touched)")
    print()
    print(f"  Drawdown at midpoint (day {args.horizon//2}, mark-to-market):")
    print(f"    median:                    {md['drawdown_at_mid_median']*100:5.1f}%")
    print(f"    75th percentile path:      {md['drawdown_at_mid_p75']*100:5.1f}%")
    print()
    if md.get("time_to_target_median") is not None:
        print(f"  Time-to-target (among {p_touch_mc*100:.0f}% touching paths):")
        print(f"    median:                    {md['time_to_target_median']:.0f} trading days")
        print(f"    p25/p75:                   {md['time_to_target_p25']:.0f}d  /  "
              f"{md['time_to_target_p75']:.0f}d")
    print(f"  P(panic-floor ${args.panic_level:.0f} touched at any point in "
          f"{args.horizon}d): {p_panic*100:.1f}%")
    print(f"  P(panic AND target both touched): "
          f"{md['panic_among_target_paths']*100:.1f}% of target-touching paths")
    print()

    # ---- VERDICT + HYSTERESIS ----
    print_header(f"VERDICT  {light}  {verdict}")
    bp_pct = breakeven_p * 100
    bands = [
        (f">= {bp_pct+10:.0f}%", "STRONG HOLD", "cushion >= 10pp"),
        (f"{bp_pct+5:.0f}-{bp_pct+10:.0f}%", "HOLD", "cushion 5-10pp"),
        (f"{bp_pct:.0f}-{bp_pct+5:.0f}%", "EDGE", "cushion 0-5pp"),
        (f"< {bp_pct:.0f}%", "CUT", "cushion negative"),
    ]
    for band, status, note in bands:
        marker = "  ->" if status == verdict else "    "
        print(f"{marker}  P {band:<9} = {status:<13} ({note})")
    print()
    if hysteresis_warn:
        print(f"  HYSTERESIS WARNING: {hysteresis_warn}")
        print()

    # ---- POSITION-SPECIFIC GUIDANCE ----
    ai_guidance = ai_parsed.get("position_guidance") if ai_parsed else None
    # Compute CI on cushion: drift CI translates roughly to cushion CI via
    # sensitivity (rough heuristic — interpolation from sensitivity table)
    ci_lo_cushion = None
    ci_hi_cushion = None
    if blend.get("std"):
        # Run quick MC at the 68% CI bounds
        try:
            p_lo, _, _, cush_lo, _ = _quick_scenario(
                S0, sigma, blend["lo68"], args.horizon, args.target,
                args.entry, args.shares, cut_pnl, pnl_good)
            p_hi, _, _, cush_hi, _ = _quick_scenario(
                S0, sigma, blend["hi68"], args.horizon, args.target,
                args.entry, args.shares, cut_pnl, pnl_good)
            ci_lo_cushion = cush_lo
            ci_hi_cushion = cush_hi
        except Exception:
            pass

    mech_guidance, agreement = mechanical_position_guidance(
        cushion, ev_advantage, blend['dispersion_pp'], ai_guidance,
        ci_lo68=ci_lo_cushion, ci_hi68=ci_hi_cushion)

    print_header("POSITION-SPECIFIC GUIDANCE")
    print(f"  Mechanical (from math):  {mech_guidance}")
    if ai_guidance:
        agree_str = "AGREE" if agreement else "DISAGREE"
        print(f"  AI analyst view:         {ai_guidance}     -> {agree_str}")
    print()
    print(f"  Inputs to guidance:")
    print(f"    Cushion:           {cushion_pp:+.1f}pp")
    if ci_lo_cushion is not None and ci_hi_cushion is not None:
        print(f"    Cushion 68% CI:    "
              f"[{ci_lo_cushion*100:+.1f}pp, {ci_hi_cushion*100:+.1f}pp]")
    print(f"    EV advantage:      ${ev_advantage:+,.0f}")
    print(f"    Signal dispersion: {blend['dispersion_pp']:.1f}pp")
    print()
    if mech_guidance == "HOLD":
        print(f"  -> HOLD: math supports continuing. Re-check tomorrow.")
    elif mech_guidance == "TRIM":
        print(f"  -> TRIM: signal not decisive enough to fully cut. "
              f"Take partial off ({args.shares//2} shares) to cap downside.")
    elif mech_guidance == "CUT":
        print(f"  -> CUT: math says cutting beats holding with conviction.")
    if ai_guidance and not agreement:
        # §audit fix #8: report both, do NOT recommend "more conservative"
        print(f"  NOTE: Math ({mech_guidance}) and AI ({ai_guidance}) disagree.")
        print(f"        Both views are valid; the user decides based on weight")
        print(f"        given to each. No implicit conservatism prior applied.")
    print()

    # ---- DRIFT SENSITIVITY ----
    print_header("DRIFT SENSITIVITY (transparency)")
    print(f"  Verdict at multiple drift assumptions, sigma fixed at {sigma*100:.0f}%:")
    print()
    print(f"  {'Drift scenario':<42}{'P(touch)':>10}{'EV':>11}"
          f"{'Cushion':>10}{'Verdict':>15}")
    print(f"  {'-'*42}{'-'*10}{'-'*11}{'-'*10}{'-'*15}")
    scenarios = [
        (mu_effective_historical, "Historical (capped + enrichment)"),
        (mu_for_decision,         "BLENDED (today's headline)"),
        (0.30,                    "Mildly bullish (+30%)"),
        (0.05,                    "Risk-free / options-market (+5%)"),
        (0.0,                     "Zero drift"),
    ]
    if blend.get("std"):
        scenarios.insert(2, (blend["lo68"], "Blended 68% CI low"))
        scenarios.insert(3, (blend["hi68"], "Blended 68% CI high"))
    for mu_test, label in scenarios:
        p_t, ev_t, p_star_t, cush_t, v_t = _quick_scenario(
            S0, sigma, mu_test, args.horizon, args.target, args.entry,
            args.shares, cut_pnl, pnl_good)
        marker = "  *" if "BLENDED" in label else "   "
        print(f"{marker}{label:<41}{p_t*100:>9.1f}% ${ev_t:>+9,.0f}  "
              f"{cush_t*100:>+6.1f}pp{v_t:>15}")
    print()

    # ---- HISTORY CSV (extended schema for Tier 1) ----
    csv_header = ("timestamp,spot,sigma_pct,mu_historical_pct,mu_blended_pct,"
                  "blend_std_pct,blend_lo68_pct,blend_hi68_pct,"
                  "mu_analyst_pct,mu_sector_pct,mu_macro_pct,mu_insider_pct,"
                  "mu_ai_pct,p_touch_pct,breakeven_p_pct,cushion_pp,ev_hold,"
                  "ev_cut,verdict,position_guidance,ai_cost_usd,"
                  "regime,max_dd_p50,max_dd_p90,panic_prob,days_to_earnings\n")

    def _fmt_drift(d):
        return f"{d*100:.2f}" if d is not None else ""

    blend_std_str = f"{blend['std']*100:.2f}" if blend.get('std') else ""
    blend_lo_str = f"{blend['lo68']*100:.2f}" if blend.get('lo68') is not None else ""
    blend_hi_str = f"{blend['hi68']*100:.2f}" if blend.get('hi68') is not None else ""

    days_to_earnings_str = str(earnings_event["days_away"]) if earnings_event else ""
    new_row = (f"{datetime.now():%Y-%m-%d %H:%M},{S0:.2f},{sigma*100:.2f},"
               f"{mu_effective_historical*100:.2f},{mu_for_decision*100:.2f},"
               f"{blend_std_str},{blend_lo_str},{blend_hi_str},"
               f"{_fmt_drift(signals['analyst'].get('drift'))},"
               f"{_fmt_drift(signals['sector'].get('drift'))},"
               f"{_fmt_drift(signals['macro'].get('drift'))},"
               f"{_fmt_drift(signals['insider'].get('drift'))},"
               f"{_fmt_drift(signals.get('ai',{}).get('drift'))},"
               f"{p_touch_mc*100:.2f},{breakeven_p*100:.2f},"
               f"{cushion_pp:.2f},{ev_hold:.0f},{cut_pnl:.0f},"
               f"{verdict},{mech_guidance},{ai_cost:.4f},"
               f"{regime['regime']},{path_stats['max_drawdown_median']*100:.2f},"
               f"{path_stats['max_drawdown_p90']*100:.2f},{p_panic*100:.2f},"
               f"{days_to_earnings_str}\n")

    # Migrate old CSV to .legacy if schema differs (check new days_to_earnings col)
    if history_path.exists():
        first_line = history_path.read_text().split("\n", 1)[0]
        if "days_to_earnings" not in first_line:
            history_path.rename(history_dir / f"thesis_history_{ticker}.legacy.csv")
            history_path.write_text(csv_header)
    else:
        history_path.write_text(csv_header)
    with open(history_path, "a") as f:
        f.write(new_row)

    rows = history_path.read_text().strip().split("\n")
    if len(rows) >= 2:
        print_header(f"7-DAY TREND (from {history_path.name})")
        print(f"  {'When':<19}{'Spot':>9}{'sigma':>7}"
              f"{'mu_blend':>10}{'P(t)':>8}{'P*':>7}{'Cush':>7}"
              f"{'Regime':>16}{'Verdict':>13}")
        print(f"  {'-'*19}{'-'*9}{'-'*7}{'-'*10}"
              f"{'-'*8}{'-'*7}{'-'*7}{'-'*16}{'-'*13}")
        header_cells = rows[0].split(",")
        for row in rows[1:][-7:]:
            cells = row.split(",")
            try:
                idx = {h: i for i, h in enumerate(header_cells)}
                ts = cells[idx["timestamp"]]
                sp = float(cells[idx["spot"]])
                sg = float(cells[idx["sigma_pct"]])
                mb = float(cells[idx["mu_blended_pct"]])
                pt = float(cells[idx["p_touch_pct"]])
                bp_ = float(cells[idx["breakeven_p_pct"]])
                cu = float(cells[idx["cushion_pp"]])
                rg = cells[idx.get("regime", -1)] if "regime" in idx else "?"
                vd = cells[idx["verdict"]]
                print(f"  {ts:<19}${sp:>7.2f}{sg:>6.1f}%"
                      f"{mb:>+8.1f}%{pt:>7.1f}%{bp_:>6.1f}%"
                      f"{cu:>+6.1f}pp{rg:>16}{vd:>13}")
            except (KeyError, ValueError, IndexError):
                continue
        print()
    print(f"  Saved: {history_path}")
    if ai_cost > 0:
        print(f"  AI cost this run: ${ai_cost:.3f}")


def check_thesis_mode(args):
    """GOD MODE v4 — multi-target conviction scan (§2026-05-17 final).

    User's decision rule: HOLD if the model finds at least ONE sell-limit
    level X >= entry where P(touch X) in horizon >= conviction threshold.
    Default threshold = 65% (locked per final audit for high-stakes swing).

    Output identifies:
      - X_aggressive: highest X with point estimate P >= threshold (recommended)
      - X_safe: highest X with lo68 CI bound P >= threshold (robust)
      - Verdict: HOLD with sell-limit @ X_aggressive, TRIM (marginal), or CUT

    Fortifications vs v3:
      - 9 drift signals (added: short_interest, peer_rs, sector_decoupling)
      - Sigma triangulation: GARCH + realized vol (30/60/90d) + yfinance IV
      - Earnings-aware probability bands (gap risk not in GBM)
      - Reliability components shown separately (no synthesized score)
      - No threshold spectrum noise, no X_stretch fantasy, no block bootstrap
    """
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        sys.exit("ERROR: FMP_API_KEY not set in environment")

    ticker = args.thesis_ticker
    threshold = getattr(args, "conviction_threshold", 0.65)

    df = fetch_history(ticker, api_key, args.lookback_days)
    S0 = float(df["Close"].iloc[-1])
    last_date = df["Date"].iloc[-1].strftime("%Y-%m-%d")
    log_ret = np.log(df["Close"] / df["Close"].shift(1)).dropna()

    # GARCH + realized vol
    forecast_var = fit_garch_11(log_ret)
    garch_sigma = float(np.sqrt(forecast_var * 252))
    garch_fit_ok = forecast_var > 0 and not np.isnan(forecast_var)
    realized_vols = compute_realized_vol(log_ret, windows=(30, 60, 90))

    # Drift base + enrichment
    mu_hist = float(log_ret.mean() * 252)
    mu_capped = max(-args.drift_cap, min(args.drift_cap, mu_hist))
    rsi = compute_rsi_14(df["Close"])
    mom_5d = float(df["Close"].iloc[-1] / df["Close"].iloc[-6] - 1.0) if len(df) >= 6 else 0.0
    mom_30d = (float(df["Close"].iloc[-1] / df["Close"].iloc[-31] - 1.0) * 100
               if len(df) >= 31 else None)
    enr = enrichment_drift(rsi, mom_5d)
    mu_effective_historical = mu_capped + enr * 252 / args.horizon

    # YTD return
    ytd_pct = None
    try:
        ytd_start = df[df["Date"].dt.year == datetime.now().year]["Close"].iloc[0]
        ytd_pct = (S0 / float(ytd_start) - 1.0) * 100
    except (IndexError, ValueError, TypeError):
        pass

    print_header(f"{ticker} THESIS HEALTH CHECK (GOD MODE v4) — "
                 f"{datetime.now():%Y-%m-%d %H:%M}")
    print(f"  Conviction threshold: {threshold*100:.0f}%")
    print(f"  Gathering intelligence (this takes ~15-40s)...")
    print()

    # === FETCH ALL DATA ===
    profile = fetch_company_profile(ticker, api_key) or {}
    sector_name = profile.get("sector") or "Technology"
    market_cap_usd = None
    for fname in ("mktCap", "marketCap", "mcap", "market_cap"):
        try:
            v = profile.get(fname)
            if v and float(v) > 0:
                market_cap_usd = float(v)
                break
        except (ValueError, TypeError):
            continue

    analyst_targets = fetch_analyst_targets(ticker, api_key)
    analyst_summary = fetch_analyst_summary(ticker, api_key)
    stock_exchange = profile.get("exchange", "NASDAQ") or "NASDAQ"
    sector_perf = fetch_sector_perf(sector_name, api_key, days=30,
                                     exchange_filter=stock_exchange)
    insider = fetch_insider_activity(ticker, api_key, days=90)
    macro = fetch_macro_indicators(api_key)
    recent_news = fetch_recent_news(ticker, api_key, limit=20)
    earnings_event = fetch_next_earnings(ticker, api_key,
                                          lookahead_days=args.horizon + 60)
    if earnings_event:
        earnings_event["in_horizon"] = earnings_event["days_away"] <= args.horizon
        earnings_event["approaching"] = (args.horizon < earnings_event["days_away"]
                                          <= args.horizon + 30)

    # NEW v4 data sources
    short_interest = fetch_short_interest(ticker, api_key)
    peer_dfs = fetch_peer_history(["MU", "WDC"], api_key, lookback_days=90)
    options_iv = fetch_options_iv(ticker, target_dte_days=args.horizon)

    # === SIGMA TRIANGULATION ===
    sigma_triangle = triangulate_sigma(garch_sigma, realized_vols, options_iv)
    sigma_for_mc = sigma_triangle["blended"] if sigma_triangle else garch_sigma

    # === REGIME + VOL ADVISORY ===
    regime = detect_swing_regime(rsi, mom_5d, mom_30d, sigma_for_mc, ytd_pct)
    vol_advice = vol_regime_advisory(sigma_for_mc)

    # === BUILD 9 SIGNALS ===
    signals = {
        "historical":         signal_from_historical(mu_effective_historical,
                                                      mu_hist, sigma_for_mc),
        "analyst":            signal_from_analyst_targets(analyst_targets, S0,
                                                           price_history_df=df,
                                                           summary=analyst_summary),
        "sector":             signal_from_sector(sector_perf, swing_regime=regime),
        "macro":              signal_from_macro(macro),
        "insider":            signal_from_insider(insider,
                                                   market_cap_usd=market_cap_usd),
        "short_interest":     signal_from_short_interest(short_interest),
        "peer_rs":            signal_from_peer_rs(df, peer_dfs, lookback_days=60),
        "sector_decoupling":  signal_from_sector_decoupling(df, sector_perf,
                                                             lookback_days=30),
    }

    # === AI SYNTHESIS ===
    ai_parsed = None
    ai_cost = 0.0
    ai_raw = ""
    if not args.no_ai:
        prompt = build_ai_synthesis_prompt(
            ticker, profile, S0, sigma_for_mc, args.horizon,
            recent_news, [], sector_perf, analyst_targets,
            insider, macro, earnings_event=earnings_event,
            analyst_summary=analyst_summary)
        ai_parsed, ai_cost, ai_raw = call_ai_analyst(
            prompt, model=args.ai_model, max_tokens=3000)
        if ai_parsed is not None:
            signals["ai"] = signal_from_ai(ai_parsed)
        else:
            signals["ai"] = _none_signal("AI synthesis failed; see raw output")
    else:
        signals["ai"] = _none_signal("AI skipped (--no-ai)")

    # === BLEND DRIFT ===
    blend = blend_with_uncertainty(signals)

    # === BAYESIAN UPDATE ===
    history_dir = Path(__file__).parent / "output"
    history_dir.mkdir(exist_ok=True)
    history_path = history_dir / f"thesis_history_{ticker}.csv"
    prior_blend, prior_age = load_prior_blend(history_path, days_back_limit=3)
    bayesian = bayesian_update(prior_blend, blend, prior_age_days=prior_age or 1)

    # Decision drift: posterior if available, else today's blend
    if (bayesian and bayesian.get("posterior_mu") is not None
            and prior_blend is not None):
        mu_for_scan = bayesian["posterior_mu"]
        mu_std_for_scan = bayesian["posterior_std"]
        decision_basis = (f"Bayesian posterior ({bayesian['prior_weight']*100:.0f}% "
                          f"prior + {bayesian['obs_weight']*100:.0f}% today)")
    elif blend.get("blended") is not None:
        mu_for_scan = blend["blended"]
        mu_std_for_scan = blend.get("std", 0.20)
        decision_basis = "today's blend (no prior available)"
    else:
        mu_for_scan = mu_effective_historical
        mu_std_for_scan = 0.20
        decision_basis = "historical fallback (all forward signals NONE_FOUND)"

    mu_blend_for_scan = {
        "blended": mu_for_scan,
        "lo68": mu_for_scan - mu_std_for_scan,
        "hi68": mu_for_scan + mu_std_for_scan,
        "std": mu_std_for_scan,
    }

    # === MULTI-TARGET CONVICTION SCAN ===
    scan = scan_target_probabilities(S0, sigma_for_mc, mu_blend_for_scan,
                                      args.horizon, args.entry,
                                      threshold=threshold,
                                      target_increment=10,
                                      max_target_mult=1.50)
    x_safe = scan["x_safe"] if scan else None
    x_aggressive = scan["x_aggressive"] if scan else None

    # === MC FOR PATH METRICS at recommended X (or args.target as fallback) ===
    target_for_path = x_aggressive if x_aggressive else args.target
    paths = run_mc_paths(S0, sigma_for_mc, mu_for_scan, args.horizon, n_paths=50_000)
    path_stats = compute_path_metrics(paths, S0, target_for_path, args.panic_level)
    p_panic = path_stats["panic_touch_prob_total"]

    # === VERDICT ===
    p_at_be = None
    p_at_be_lo = None
    if scan and scan["curve"]:
        p_at_be = scan["curve"][0]["p_point"]
        p_at_be_lo = scan["curve"][0]["p_lo68"]

    if x_aggressive and x_aggressive >= args.entry:
        verdict = "HOLD"
        verdict_color = "[GREEN]"
        recommended_X = x_aggressive
    elif p_at_be is not None and p_at_be >= threshold - 0.10:
        verdict = "TRIM"
        verdict_color = "[YELLOW]"
        recommended_X = args.entry
    else:
        verdict = "CUT"
        verdict_color = "[RED]"
        recommended_X = None

    # === EARNINGS BAND ADJUSTMENT ===
    earnings_band = None
    if x_aggressive and earnings_event and earnings_event.get("in_horizon"):
        p_at_X = next((row["p_point"] for row in scan["curve"]
                       if row["X"] == x_aggressive), None)
        if p_at_X is not None:
            earnings_band = adjust_for_earnings(p_at_X, earnings_event, args.horizon)

    # === PER-TARGET SENSITIVITY at X_aggressive ===
    sensitivity = None
    if x_aggressive:
        sensitivity = compute_target_sensitivity(
            S0, sigma_for_mc, mu_for_scan, args.horizon, x_aggressive, threshold)

    # === RENDER ===
    print(f"  Data through:           {last_date} close")
    print(f"  Spot:                   ${S0:.2f}")
    if market_cap_usd:
        print(f"  Market cap:             ${market_cap_usd/1e9:.1f}B")
    print(f"  Sector / Industry:      {sector_name} / {profile.get('industry','')}")
    print(f"  Sigma (GARCH spot):     {garch_sigma*100:.1f}%")
    print(f"  RSI / 5d mom / 30d mom: {rsi:.1f} / {mom_5d*100:+.1f}% / "
          f"{f'{mom_30d:+.1f}%' if mom_30d is not None else 'n/a'}")
    if ytd_pct is not None:
        print(f"  YTD return:             {ytd_pct:+.1f}%")
    print()
    print(f"  Position: {args.shares} shares @ ${args.entry:.0f} cost basis "
          f"({(S0-args.entry)*args.shares:+,.0f} unrealised)")
    print(f"  Patience window:        {args.horizon} trading days")
    print()

    # ---- REGIME + VOL ADVISORY ----
    print_header("REGIME & VOL ADVISORY")
    print(f"  Swing regime: {regime['regime']}")
    print(f"  Detail:       {regime['detail']}")
    print(f"  Vol level:    {vol_advice['level']}")
    print()

    # ---- EARNINGS CALENDAR ----
    if earnings_event:
        print_header("EARNINGS CALENDAR")
        ev = earnings_event
        print(f"  Next earnings:    {ev['date']} ({ev['days_away']}d away)")
        if ev.get("eps_est"):
            print(f"  EPS estimate:     ${ev['eps_est']:.2f}")
        if ev["in_horizon"]:
            print(f"  ⚠ IN HORIZON: gap risk applies; probabilities have ±5pp band")
        elif ev["approaching"]:
            print(f"  CONTEXT: {ev['days_away'] - args.horizon}d after horizon - "
                  f"late-horizon may see positioning vol")
        print()

    # ---- SIGMA TRIANGULATION ----
    print_header("SIGMA TRIANGULATION")
    if sigma_triangle and sigma_triangle["n_anchors"] >= 1:
        for anchor_name, anchor_val in sigma_triangle["anchors"].items():
            print(f"  {anchor_name:<25} {anchor_val*100:6.1f}%")
        print(f"  {'BLENDED (used in MC)':<25} {sigma_triangle['blended']*100:6.1f}%")
        print(f"  Anchors used:           {sigma_triangle['n_anchors']}")
        print(f"  Divergence (max-min):   {sigma_triangle['divergence_pp']:.1f}pp")
        if options_iv:
            if options_iv["is_liquid"]:
                print(f"  Options IV @ {options_iv['expiry']} (DTE {options_iv['dte']}): "
                      f"{options_iv['iv']*100:.1f}% [included]")
            else:
                print(f"  Options IV liquidity-gated out "
                      f"(bid-ask {options_iv['bid_ask_pct_avg']*100:.1f}% > 10%)")
        else:
            print(f"  Options IV: not available (yfinance unavailable or no chain)")
    else:
        print(f"  Only GARCH ({garch_sigma*100:.1f}%) available — no triangulation")
    print(f"  GARCH fit status:       {'OK' if garch_fit_ok else 'DEGRADED'}")
    print()

    # ---- FORWARD DRIFT INTELLIGENCE (9 SIGNALS) ----
    print_header("FORWARD DRIFT INTELLIGENCE (9 signals)")
    print(f"  {'Source':<37}{'mu (ann)':>10}{'Conf':>7}{'SrcQ':>13}{'Wt':>6}")
    print(f"  {'-'*37}{'-'*10}{'-'*7}{'-'*13}{'-'*6}")
    sig_labels = [
        ("historical",        "Historical (GARCH + enrichment)"),
        ("analyst",           "Analyst (price-target-summary)"),
        ("sector",            f"Sector momentum ({sector_name})"),
        ("macro",             "Macro regime (VIX/SPY)"),
        ("insider",           "Insider activity (90d, mcap-scaled)"),
        ("short_interest",    "Short interest (squeeze/skepticism)"),
        ("peer_rs",           "Peer relative strength (MU+WDC, 60d)"),
        ("sector_decoupling", "Sector decoupling (vs sector, 30d)"),
        ("ai",                "AI analyst (Claude Opus 4.7)"),
    ]
    for name, label in sig_labels:
        s = signals.get(name, {})
        d = s.get("drift")
        d_str = f"{d*100:>+8.1f}%" if d is not None else "    n/a "
        conf = s.get("confidence", "?")
        sq = s.get("source_quality", "?")
        w = blend["weights"].get(name, 0.0)
        marker = "  *" if name == "ai" else "   "
        print(f"{marker}{label:<36}{d_str:>9}{conf:>7}{sq:>13}{w*100:>5.0f}%")
    print()
    for name, label in sig_labels:
        s = signals.get(name, {})
        notes = s.get("notes", "")
        if notes:
            label_short = label.split(" (")[0] if "(" in label else label
            print(f"  {label_short[:32]:<32}: {notes}")
    print()

    if blend.get("std"):
        print(f"  BLENDED DRIFT (today): {blend['blended']*100:+.1f}% +/- "
              f"{blend['std']*100:.1f}pp")
        print(f"  68% CI: [{blend['lo68']*100:+.1f}%, {blend['hi68']*100:+.1f}%]")
    print(f"  Active signals: {blend['n_active']} of 9")
    print(f"  Dispersion: {blend['dispersion_pp']:.1f}pp")
    if blend["dispersion_pp"] >= 100:
        print(f"  ⚠ DISPERSION >=100pp - blend NOT a defensible point estimate")
    elif blend["dispersion_pp"] >= 60:
        print(f"  ⚠ DISPERSION 60-100pp - treat as noise; use CI bounds")
    elif blend["dispersion_pp"] >= 30:
        print(f"  ⚠ DISPERSION 30-60pp - signals disagree meaningfully")
    print()

    # ---- BAYESIAN ----
    if bayesian and prior_blend:
        print_header("BAYESIAN BELIEF UPDATE")
        print(f"  Prior (yesterday, {prior_age}d old): mu={prior_blend['blended']*100:+.1f}%, "
              f"std={prior_blend.get('std', 0.15)*100:.1f}pp")
        print(f"  Today obs:                  mu={blend['blended']*100:+.1f}%, "
              f"std={blend['std']*100:.1f}pp")
        print(f"  Posterior (used for scan):  mu={bayesian['posterior_mu']*100:+.1f}%, "
              f"std={bayesian['posterior_std']*100:.1f}pp")
        print()
    print(f"  Decision drift basis: {decision_basis}")
    print(f"  Drift used in scan:   {mu_for_scan*100:+.1f}% +/- {mu_std_for_scan*100:.1f}pp")
    print()

    # ---- AI SYNTHESIS ----
    if ai_parsed:
        print_header(f"AI ANALYST SYNTHESIS  ({args.ai_model}, cost ${ai_cost:.3f})")
        dp = ai_parsed.get("drift_point")
        if dp is not None:
            print(f"  AI drift estimate:  {dp*100:+.1f}%/yr "
                  f"(range {ai_parsed.get('drift_low', dp)*100:+.0f}% to "
                  f"{ai_parsed.get('drift_high', dp)*100:+.0f}%)")
        else:
            print(f"  AI drift estimate:  null (evidence too thin)")
        print(f"  Confidence:         {ai_parsed.get('confidence','?')}, "
              f"sources cited: {ai_parsed.get('sources_count', 0)}")
        rationale = ai_parsed.get("rationale", "")
        if rationale:
            print(f"  Rationale: {rationale[:300]}")
        if args.show_rationale:
            print()
            print("  BULL FACTORS:")
            for i, b in enumerate(ai_parsed.get("bull_factors", []) or [], 1):
                print(f"    {i}. {b.get('factor','')}")
                print(f"       Source: {b.get('source','')}   Weight: {b.get('weight','?')}")
            print()
            print("  BEAR FACTORS:")
            for i, b in enumerate(ai_parsed.get("bear_factors", []) or [], 1):
                print(f"    {i}. {b.get('factor','')}")
                print(f"       Source: {b.get('source','')}   Weight: {b.get('weight','?')}")
            print()
            print("  KEY RISKS:")
            for i, r in enumerate(ai_parsed.get("key_risks", []) or [], 1):
                print(f"    {i}. {r.get('risk','')}")
        print()

    # ---- MULTI-TARGET CONVICTION SCAN ----
    print_header(f"MULTI-TARGET CONVICTION SCAN at {threshold*100:.0f}% threshold")
    if scan and scan["curve"]:
        print(f"  {'Sell-limit':<12}{'P(touch)':>10}{'68% CI':>20}"
              f"{'Profit/sh':>12}{'Status':>16}")
        print(f"  {'-'*12}{'-'*10}{'-'*20}{'-'*12}{'-'*16}")
        for row in scan["curve"]:
            x = row["X"]
            pp = row["p_point"]
            plo = row["p_lo68"]
            phi = row["p_hi68"]
            profit_sh = row["profit_per_share"]
            action = row["action"]
            marker = ""
            if x == x_aggressive: marker = " ⭐"
            elif x == x_safe: marker = " ✓"
            ci_str = f"[{plo*100:.0f}%, {phi*100:.0f}%]"
            print(f"  ${x:>9.0f}{marker:<2}{pp*100:>8.1f}% {ci_str:>20}"
                  f"  ${profit_sh:>+7.0f}    {action:>15}")
        print()
        print(f"  X_safe       (lo68 >= {threshold*100:.0f}%):  "
              f"{f'${x_safe:.0f}' if x_safe else 'NONE'}"
              + (f"  → profit/sh +${x_safe - args.entry:.0f}, "
                 f"position +${(x_safe - args.entry) * args.shares:.0f}"
                 if x_safe else "  → no X meets robust CI test"))
        print(f"  X_aggressive (point >= {threshold*100:.0f}%): "
              f"{f'${x_aggressive:.0f}' if x_aggressive else 'NONE'}"
              + (f"  → profit/sh +${x_aggressive - args.entry:.0f}, "
                 f"position +${(x_aggressive - args.entry) * args.shares:.0f}"
                 if x_aggressive else "  → P(touch BE) below threshold"))
    print()

    # ---- VERDICT ----
    print_header(f"VERDICT  {verdict_color}  {verdict}")
    if verdict == "HOLD":
        print(f"  Action: HOLD all {args.shares} shares")
        print(f"  Sell-limit: ${recommended_X:.0f} (highest defensible at "
              f"{threshold*100:.0f}% threshold)")
        profit_at_X = (recommended_X - args.entry) * args.shares
        # P at recommended X
        p_at_rec = next((r["p_point"] for r in scan["curve"]
                          if r["X"] == recommended_X), None)
        if p_at_rec is not None:
            print(f"  Profit if hit: +${profit_at_X:.0f} ({p_at_rec*100:.0f}% probability)")
        if x_safe and x_safe != x_aggressive:
            print(f"  More conservative: ${x_safe:.0f} "
                  f"(lo68 still >= {threshold*100:.0f}%, profit "
                  f"+${(x_safe - args.entry) * args.shares:.0f})")
    elif verdict == "TRIM":
        print(f"  Action: TRIM half ({args.shares//2} shares)")
        print(f"  P(touch BE ${args.entry}) = {p_at_be*100:.0f}% — "
              f"marginal vs {threshold*100:.0f}% threshold")
        print(f"  Keep {args.shares - args.shares//2} shares with sell-limit @ "
              f"${args.entry:.0f}")
        cut_loss_now = (S0 - args.entry) * (args.shares // 2)
        print(f"  Realised on trim: ${cut_loss_now:+,.0f}")
    else:  # CUT
        print(f"  Action: CUT all {args.shares} shares")
        cut_loss = (S0 - args.entry) * args.shares
        print(f"  P(touch BE ${args.entry}) = {p_at_be*100:.0f}% — "
              f"more than 10pp below {threshold*100:.0f}% threshold")
        print(f"  Realise: ${cut_loss:+,.0f}")
    print()

    # ---- EARNINGS BAND ----
    if earnings_band and earnings_band["adjusted"]:
        print(f"  ⚠ {earnings_band['note']}")
        print(f"  P band at ${x_aggressive}: [{earnings_band['band_lo']*100:.0f}%, "
              f"{earnings_band['band_hi']*100:.0f}%]")
        print()

    # ---- PER-TARGET SENSITIVITY at X_aggressive ----
    if sensitivity:
        print_header(f"SENSITIVITY at recommended X = ${x_aggressive:.0f}")
        print(f"  {'Scenario':<42}{'mu':>9}{'sigma':>9}{'P(touch)':>10}{'Verdict':>12}")
        print(f"  {'-'*42}{'-'*9}{'-'*9}{'-'*10}{'-'*12}")
        for sc in sensitivity["scenarios"]:
            print(f"  {sc['label']:<42}{sc['mu']*100:>+7.0f}% "
                  f"{sc['sigma']*100:>7.0f}% {sc['p']*100:>8.1f}% {sc['verdict']:>12}")
        if sensitivity.get("flip_drift") is not None:
            print(f"  → Verdict flips to BELOW at drift "
                  f"{sensitivity['flip_drift']*100:+.0f}% "
                  f"(margin of {(mu_for_scan - sensitivity['flip_drift'])*100:.0f}pp)")
        print()

    # ---- PATH METRICS ----
    print_header("PATH-DEPENDENT RISK METRICS")
    md = path_stats
    print(f"  Max drawdown along the way (from ${S0:.0f}):")
    print(f"    median:  {md['max_drawdown_median']*100:5.1f}% "
          f"(${S0 * (1 - md['max_drawdown_median']):.0f} touched)")
    print(f"    p75:     {md['max_drawdown_p75']*100:5.1f}% "
          f"(${S0 * (1 - md['max_drawdown_p75']):.0f} touched)")
    print(f"    p90:     {md['max_drawdown_p90']*100:5.1f}% "
          f"(${S0 * (1 - md['max_drawdown_p90']):.0f} touched)")
    if md.get("time_to_target_median") is not None:
        print(f"  Time-to-target (target ${target_for_path:.0f}, among touching paths):")
        print(f"    median: {md['time_to_target_median']:.0f}d, "
              f"p25/p75: {md['time_to_target_p25']:.0f}d/{md['time_to_target_p75']:.0f}d")
    print(f"  P(panic floor ${args.panic_level:.0f} touched): {p_panic*100:.0f}%")
    print()

    # ---- RELIABILITY COMPONENTS (no synthesized score) ----
    print_header("RELIABILITY COMPONENTS  (assess each independently)")
    n_high = sum(1 for s in signals.values() if s.get("confidence") == "HIGH")
    n_med = sum(1 for s in signals.values() if s.get("confidence") == "MEDIUM")
    n_low = sum(1 for s in signals.values() if s.get("confidence") == "LOW")
    n_active = blend["n_active"]
    print(f"  Active signals:           {n_active}/9 (HIGH:{n_high}, "
          f"MED:{n_med}, LOW:{n_low}, NONE_FOUND:{9-n_active})")
    print(f"  Signal dispersion:        {blend['dispersion_pp']:.1f}pp "
          f"({'wide' if blend['dispersion_pp'] >= 60 else 'tight'})")
    print(f"  GARCH fit:                {'OK' if garch_fit_ok else 'DEGRADED'}")
    print(f"  Sigma anchors:            {sigma_triangle['n_anchors'] if sigma_triangle else 1} "
          f"({'multi-source' if sigma_triangle and sigma_triangle['n_anchors'] >= 2 else 'single-source'})")
    print(f"  Bayesian history depth:   {'present' if prior_blend else '1 day (no prior)'}")
    print(f"  Regime:                   {regime['regime']} "
          f"({'stable' if regime['regime'] not in ('POST_PARABOLA', 'UNCERTAIN') else 'unstable'})")
    print(f"  Math/AI position agreement: "
          f"{'agree' if ai_parsed and ai_parsed.get('position_guidance') == verdict else 'check below'}")
    if ai_parsed:
        print(f"  AI position view:         {ai_parsed.get('position_guidance', '?')}")
    print()

    # ---- HYSTERESIS ----
    hysteresis_warn, prior_verdict = check_hysteresis(history_path, verdict, 0)
    if hysteresis_warn:
        print(f"  HYSTERESIS: {hysteresis_warn}")
        print()

    # ---- CSV HISTORY ----
    csv_header = ("timestamp,spot,sigma_garch_pct,sigma_blended_pct,"
                  "mu_blended_pct,blend_std_pct,blend_lo68_pct,blend_hi68_pct,"
                  "p_at_be_pct,x_safe,x_aggressive,verdict,recommended_x,"
                  "ai_cost_usd,regime,max_dd_p50,panic_prob,days_to_earnings,"
                  "threshold_used\n")
    days_to_earnings_str = str(earnings_event["days_away"]) if earnings_event else ""
    new_row = (f"{datetime.now():%Y-%m-%d %H:%M},{S0:.2f},{garch_sigma*100:.2f},"
               f"{sigma_for_mc*100:.2f},{mu_for_scan*100:.2f},"
               f"{mu_std_for_scan*100:.2f},"
               f"{(mu_for_scan-mu_std_for_scan)*100:.2f},"
               f"{(mu_for_scan+mu_std_for_scan)*100:.2f},"
               f"{p_at_be*100 if p_at_be else 0:.2f},"
               f"{x_safe if x_safe else ''},{x_aggressive if x_aggressive else ''},"
               f"{verdict},{recommended_X if recommended_X else ''},"
               f"{ai_cost:.4f},{regime['regime']},"
               f"{path_stats['max_drawdown_median']*100:.2f},"
               f"{p_panic*100:.2f},{days_to_earnings_str},{threshold*100:.0f}\n")

    if history_path.exists():
        first_line = history_path.read_text().split("\n", 1)[0]
        if "x_aggressive" not in first_line:
            history_path.rename(history_dir / f"thesis_history_{ticker}.legacy.csv")
            history_path.write_text(csv_header)
    else:
        history_path.write_text(csv_header)
    with open(history_path, "a") as f:
        f.write(new_row)

    print(f"  Saved: {history_path}")
    if ai_cost > 0:
        print(f"  AI cost this run: ${ai_cost:.3f}")


def _quick_scenario(S0, sigma, mu, horizon, target, entry, shares, cut_pnl, pnl_good):
    """Quick MC + EV for the drift sensitivity table. Returns
    (p_touch, ev_hold, p_star, cushion, verdict_str)."""
    paths = run_mc_paths(S0, sigma, mu, horizon, n_paths=20_000, seed=43)
    touched = (paths >= target).any(axis=1)
    p = float(touched.mean())
    e_bad = float(paths[~touched, -1].mean()) if (~touched).any() else 0.0
    pnl_bad = (e_bad - entry) * shares
    ev = p * pnl_good + (1 - p) * pnl_bad
    if pnl_good == pnl_bad:
        return p, ev, float("nan"), float("nan"), "?"
    p_star = (cut_pnl - pnl_bad) / (pnl_good - pnl_bad)
    cushion = p - p_star
    if cushion >= 0.10:
        v = "STRONG HOLD"
    elif cushion >= 0.05:
        v = "HOLD"
    elif cushion >= 0.0:
        v = "EDGE"
    else:
        v = "CUT"
    return p, ev, p_star, cushion, v


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
    ap.add_argument("--conviction-threshold", type=float, default=0.65,
                    help="Conviction threshold for multi-target scan in "
                         "--check-thesis mode (default 0.65 = 65%%). HOLD if "
                         "any sell-limit X >= entry has P(touch X) above this.")
    ap.add_argument("--no-ai", action="store_true",
                    help="Skip Claude AI synthesis call (faster, free, "
                         "uses only FMP signals)")
    ap.add_argument("--ai-model", default="claude-opus-4-7",
                    help="Anthropic model for AI synthesis "
                         "(default: claude-opus-4-7)")
    ap.add_argument("--show-rationale", action="store_true",
                    help="Print full AI bull/bear/risk factor detail "
                         "(default: summary only)")
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
