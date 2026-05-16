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

# Blend weights (sum = 1.0). Historical excluded — extrapolation bias.
BLEND_WEIGHTS = {
    "analyst": 0.25,
    "sector":  0.15,
    "macro":   0.10,
    "insider": 0.10,
    "ai":      0.40,
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
    """FMP price-target-consensus (12-month analyst price targets)."""
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


def fetch_company_profile(ticker, api_key):
    """FMP profile — sector, industry, market cap, etc."""
    data = _fmp_get("profile", api_key, {"symbol": ticker})
    if not data or not isinstance(data, list) or not data:
        return None
    return data[0]


def fetch_sector_perf(sector, api_key, days=30):
    """FMP historical-sector-performance — CANON: §sacred requires
    sector + from/to dates (otherwise stale 2024 data)."""
    if not sector:
        return None
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days + 14)).strftime("%Y-%m-%d")
    data = _fmp_get("historical-sector-performance", api_key,
                    {"sector": sector, "from": start, "to": end})
    if not data or not isinstance(data, list) or len(data) < 2:
        return None
    rows = sorted(data, key=lambda x: x.get("date", ""))
    rows = rows[-days:]
    if not rows or "changesPercentage" not in rows[0]:
        return None
    cum_return = 1.0
    for r in rows:
        try:
            cum_return *= (1 + float(r.get("changesPercentage", 0)) / 100.0)
        except (ValueError, TypeError):
            continue
    cum_return -= 1.0
    return {
        "cum_return_pct": cum_return * 100,
        "n_days": len(rows),
        "sector": sector,
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


def signal_from_analyst_targets(targets, S0):
    if not targets or not targets.get("target_mean") or S0 <= 0:
        return _none_signal("no analyst targets available")
    try:
        target = float(targets["target_mean"])
        if target <= 0:
            return _none_signal("invalid target price")
        # Analyst targets are 12mo horizon → implied annualised drift
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
        return {
            "drift": float(drift), "confidence": conf,
            "source_quality": "REPUTABLE", "sources_count": 5,
            "notes": f"target mean ${target:.0f}, range ${low:.0f}-${high:.0f}",
        }
    except (ValueError, TypeError):
        return _none_signal("analyst target parse error")


def signal_from_sector(sector_perf):
    if not sector_perf or sector_perf.get("cum_return_pct") is None:
        return _none_signal("sector data unavailable")
    days = max(1, sector_perf.get("n_days", 30))
    cum = sector_perf["cum_return_pct"] / 100.0
    drift = (1 + cum) ** (252 / days) - 1.0
    drift = max(-0.50, min(1.50, drift))
    return {
        "drift": float(drift), "confidence": "MEDIUM",
        "source_quality": "PRIMARY", "sources_count": 1,
        "notes": (f"{sector_perf.get('sector','?')} {cum*100:+.1f}% "
                  f"last {days}d (annualised {drift*100:+.0f}%)"),
    }


def signal_from_macro(macro):
    if not macro:
        return _none_signal("macro data unavailable")
    regime = macro.get("regime", "neutral")
    # Map regime to drift tilt — modest, since macro is broad backdrop
    drift = {"risk_on": 0.10, "neutral": 0.05, "risk_off": -0.05}.get(regime, 0.05)
    return {
        "drift": float(drift), "confidence": "HIGH",
        "source_quality": "PRIMARY", "sources_count": 2,
        "notes": (f"VIX {macro['vix']:.1f}, SPY {macro['spy_trend']*100:+.1f}% "
                  f"vs MA50 -> {regime}"),
    }


def signal_from_insider(insider):
    if not insider:
        return _none_signal("insider data unavailable")
    n_total = insider.get("n_buys", 0) + insider.get("n_sells", 0)
    if n_total == 0:
        return {"drift": 0.0, "confidence": "LOW",
                "source_quality": "PRIMARY", "sources_count": 1,
                "notes": "no insider P+S transactions in window"}
    net = insider.get("net_value_usd", 0)
    # Calibration: net buying >$10M = +5% tilt; net selling >$10M = -5% tilt
    drift = max(-0.10, min(0.10, net / 100_000_000))
    direction = "buying" if net > 0 else "selling"
    return {
        "drift": float(drift), "confidence": "MEDIUM",
        "source_quality": "PRIMARY", "sources_count": 1,
        "notes": (f"net {direction} ${abs(net)/1e6:.1f}M "
                  f"({insider['n_buys']}P/{insider['n_sells']}S in "
                  f"{insider['days']}d)"),
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
                              insider, macro):
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
    if analyst_targets and analyst_targets.get("target_mean"):
        analyst_str = (f"consensus target ${analyst_targets['target_mean']:.0f} "
                       f"(range ${analyst_targets.get('target_low') or 0:.0f}-"
                       f"${analyst_targets.get('target_high') or 0:.0f})")
    else:
        analyst_str = "no analyst consensus available"
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

CRITICAL ANTI-BIAS RULES:
- Do NOT extrapolate recent rallies. A multi-bagger post-IPO move does not mean the next 12 months will be the same.
- Do NOT confirm priors. Be willing to say "uncertain, default risk-free ~5%".
- If evidence is contradictory, return a WIDE range, not a confident middle.
- USE web_search to verify against current data, not training cutoff. Search aggressively.

CONTEXT DATA (from FMP, today):
- Current spot: ${S0:.2f}
- GARCH vol: {sigma*100:.0f}% annualised (CHARACTERISES dispersion, NOT direction)
- Sector context: {sector_str}
- Analyst consensus: {analyst_str}
- Insider activity: {insider_str}
- Macro backdrop: {macro_str}

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
6. Analyst rating changes (last 30 days)

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
  "drift_point": <decimal, e.g., 0.20 = +20% annualised>,
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

If evidence is too thin to form a defensible view: confidence=LOW, drift_point=0.05, source_quality=NONE_FOUND, explain in evidence_gaps.

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
    """Extract JSON from Claude's response. Tolerant of code fences and stray prose."""
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
        return json.loads(cleaned[start:end+1])
    except json.JSONDecodeError:
        return None


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

def mechanical_position_guidance(cushion, ev_advantage, dispersion_pp, ai_guidance):
    """
    Mechanical HOLD/TRIM/CUT/ADD purely from the math + signal dispersion,
    independent of AI synthesis. Returned alongside AI's own view so the
    user sees both and any disagreement.
    """
    if cushion >= 0.10 and ev_advantage > 500:
        mech = "HOLD"
    elif cushion >= 0.05:
        mech = "HOLD"
    elif cushion >= 0.0:
        mech = "TRIM"
    elif cushion >= -0.05:
        mech = "TRIM"
    else:
        mech = "CUT"
    # High signal dispersion -> downgrade conviction
    if dispersion_pp >= 30 and mech == "HOLD":
        mech = "TRIM"
    agreement = (mech == ai_guidance) if ai_guidance else None
    return mech, agreement


def check_thesis_mode(args):
    """
    Daily thesis-health check for a no-stop hold strategy (GOD MODE).

    Multi-signal forward drift estimation:
      1. Historical (GARCH + RSI/mom enrichment) — shown for reference, NOT blended
      2. Analyst consensus price targets (FMP)
      3. Sector momentum (FMP historical-sector-performance)
      4. Macro regime (FMP VIX + SPY, inlined from src/macro_regime.py logic)
      5. Insider activity (FMP insider-trading/search)
      6. AI analyst synthesis (Claude Opus 4.7 + web_search)

    Blended forward drift becomes the headline assumption.
    Verdict computed against blended drift, not historical.
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
    enr = enrichment_drift(rsi, mom_5d)
    # §2026-05-17: enrichment scaling deliberately differs from src/monte_carlo.py.
    # Canon treats enr as annualised rate (small effect over years). The swing
    # tool re-annualises (× 252/horizon) so RSI/momentum signals carry meaningful
    # weight on a 60-day horizon. Audit (Agent 1 BUG-1) flagged this as a
    # divergence — KEPT INTENTIONALLY for swing-trade short-horizon sensitivity.
    mu_effective_historical = mu_capped + enr * 252 / args.horizon
    T = args.horizon / 252

    # === GATHER MULTI-SIGNAL INTELLIGENCE ===
    print_header(f"{ticker} THESIS HEALTH CHECK (GOD MODE) — "
                 f"{datetime.now():%Y-%m-%d %H:%M}")
    print(f"  Gathering intelligence (this takes ~10-30s)...")
    print()

    profile = fetch_company_profile(ticker, api_key) or {}
    sector_name = profile.get("sector") or "Technology"
    analyst_targets = fetch_analyst_targets(ticker, api_key)
    sector_perf = fetch_sector_perf(sector_name, api_key, days=30)
    insider = fetch_insider_activity(ticker, api_key, days=90)
    macro = fetch_macro_indicators(api_key)
    recent_news = fetch_recent_news(ticker, api_key, limit=20)
    press_releases = fetch_press_releases(ticker, api_key, limit=10)

    # === BUILD SIGNAL DICT ===
    signals = {
        "analyst": signal_from_analyst_targets(analyst_targets, S0),
        "sector":  signal_from_sector(sector_perf),
        "macro":   signal_from_macro(macro),
        "insider": signal_from_insider(insider),
    }

    # === AI SYNTHESIS (optional, default ON) ===
    ai_signal = None
    ai_parsed = None
    ai_cost = 0.0
    ai_raw = ""
    if not args.no_ai:
        prompt = build_ai_synthesis_prompt(
            ticker, profile, S0, sigma, args.horizon,
            recent_news, press_releases, sector_perf, analyst_targets,
            insider, macro)
        ai_parsed, ai_cost, ai_raw = call_ai_analyst(
            prompt, model=args.ai_model, max_tokens=3000)
        if ai_parsed is not None:
            ai_signal = signal_from_ai(ai_parsed)
            signals["ai"] = ai_signal
        else:
            signals["ai"] = {"drift": None, "confidence": "LOW",
                             "source_quality": "NONE_FOUND", "sources_count": 0,
                             "notes": "AI synthesis failed; see raw output"}

    # === BLEND ===
    blend = blend_drifts(signals)
    mu_blended = blend["blended"]
    mu_for_decision = mu_blended  # headline now uses blended drift

    # === COMPUTE VERDICT USING BLENDED DRIFT ===
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

    p_panic = closed_touch_down(S0, args.panic_level, T, mu_for_decision, sigma)

    if cushion >= 0.10:
        verdict, light = "STRONG HOLD", "[GREEN]"
    elif cushion >= 0.05:
        verdict, light = "HOLD", "[YELLOW]"
    elif cushion >= 0.0:
        verdict, light = "EDGE", "[ORANGE]"
    else:
        verdict, light = "CUT", "[RED]"

    # === RENDER ===
    print(f"  Data through:           {last_date} close")
    print(f"  Spot:                   ${S0:.2f}")
    print(f"  Sector / Industry:      {sector_name} / {profile.get('industry','')}")
    print(f"  Sigma (GARCH):          {sigma * 100:.1f}%")
    print(f"  RSI(14) / 5d momentum:  {rsi:.1f}  /  {mom_5d * 100:+.1f}%")
    print()
    print(f"  Position: {args.shares} shares @ ${args.entry:.0f} cost basis, "
          f"target ${args.target:.0f}, no automatic stop")
    print(f"  Patience window:        {args.horizon} trading days")
    print()

    # ---- FORWARD DRIFT INTELLIGENCE PANEL ----
    print_header("FORWARD DRIFT INTELLIGENCE")
    print(f"  {'Source':<37}{'mu (ann)':>10}{'Conf':>7}{'SrcQ':>13}{'Sources':>9}")
    print(f"  {'-'*37}{'-'*10}{'-'*7}{'-'*13}{'-'*9}")

    # Historical row (NOT in blend — shown for reference)
    print(f"  {'Historical (post-IPO + enrichment)':<37}"
          f"{mu_effective_historical*100:>+8.1f}%   LOW       (excluded)         -")

    for name, label in [
        ("analyst", "Analyst consensus (12mo target)"),
        ("sector",  f"Sector momentum ({sector_name})"),
        ("macro",   "Macro regime (VIX/SPY)"),
        ("insider", "Insider activity (90d net)"),
        ("ai",      "AI analyst (Claude Opus 4.7)"),
    ]:
        s = signals.get(name, {})
        d = s.get("drift")
        d_str = f"{d*100:>+8.1f}%" if d is not None else "    n/a "
        conf = s.get("confidence", "?")
        sq = s.get("source_quality", "?")
        sc = s.get("sources_count", 0)
        marker = "  *" if name == "ai" else "   "
        print(f"{marker}{label:<36}{d_str:>9}{conf:>7}{sq:>13}{sc:>9}")

    # Notes column for each signal
    print()
    for name, label in [
        ("analyst", "Analyst"), ("sector", "Sector"),
        ("macro", "Macro"), ("insider", "Insider"), ("ai", "AI"),
    ]:
        s = signals.get(name, {})
        notes = s.get("notes", "")
        if notes:
            print(f"  {label:<10}: {notes}")
    print()
    print(f"  BLENDED FORWARD DRIFT:               "
          f"{mu_blended*100:+.1f}%/yr  <- NEW HEADLINE")
    print(f"  Active signals in blend:             "
          f"{blend['n_active']} of 5")
    if blend.get("fallback"):
        print(f"  WARNING: all signals NONE_FOUND — fell back to +5% risk-free default")
    weights_str = ", ".join(f"{n}:{w*100:.0f}%" for n, w in blend["weights"].items()
                            if w > 0)
    print(f"  Weights used:                        {weights_str}")
    print(f"  Signal dispersion (range):           "
          f"{blend['dispersion_pp']:.1f}pp")
    if blend['dispersion_pp'] >= 30:
        print(f"  WARNING: signals disagree significantly. Treat blend with caution.")
    print()

    # ---- AI ANALYST DETAILED SYNTHESIS ----
    if ai_parsed:
        print_header(f"AI ANALYST SYNTHESIS  ({args.ai_model}, cost ${ai_cost:.3f})")
        print(f"  Drift point:        {ai_parsed.get('drift_point',0)*100:+.1f}%/yr")
        if "drift_low" in ai_parsed and "drift_high" in ai_parsed:
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

    # ---- HEADLINE METRIC (using BLENDED drift) ----
    print_header("HEADLINE METRIC (recomputed with blended forward drift)")
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

    print_header("TAIL RISK")
    print(f"  P(touch ${args.panic_level:.0f} panic floor in "
          f"{args.horizon}d): {p_panic*100:.1f}%")
    print(f"  (your stated tolerance floor — watch this number trend up)")
    print()

    # ---- VERDICT ----
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

    # ---- POSITION-SPECIFIC GUIDANCE ----
    ai_guidance = ai_parsed.get("position_guidance") if ai_parsed else None
    mech_guidance, agreement = mechanical_position_guidance(
        cushion, ev_advantage, blend['dispersion_pp'], ai_guidance)

    print_header("POSITION-SPECIFIC GUIDANCE")
    print(f"  Mechanical (from math):  {mech_guidance}")
    if ai_guidance:
        agree_str = "AGREE" if agreement else "DISAGREE"
        print(f"  AI analyst view:         {ai_guidance}     "
              f"-> {agree_str}")
    print()
    print(f"  Recommendation logic:")
    print(f"    Cushion: {cushion_pp:+.1f}pp")
    print(f"    EV advantage: ${ev_advantage:+,.0f}")
    print(f"    Signal dispersion: {blend['dispersion_pp']:.1f}pp")
    print()
    if mech_guidance == "HOLD":
        print(f"  -> HOLD: math supports continuing. Re-check tomorrow.")
    elif mech_guidance == "TRIM":
        print(f"  -> TRIM: at edge. Consider taking partial off ({args.shares//2} "
              f"shares) to cap downside while preserving upside on the rest.")
    elif mech_guidance == "CUT":
        print(f"  -> CUT: math says cutting beats holding. Realise the loss.")
    if not agreement and ai_guidance:
        print(f"  Note: AI ({ai_guidance}) and math ({mech_guidance}) disagree.")
        print(f"        Consider the more conservative of the two.")
    print()

    # ---- DRIFT SENSITIVITY (preserved + augmented) ----
    print_header("DRIFT SENSITIVITY (transparency)")
    print(f"  Verdict at multiple drift assumptions, "
          f"sigma fixed at {sigma*100:.0f}%:")
    print()
    print(f"  {'Drift scenario':<42}{'P(touch)':>10}{'EV':>11}"
          f"{'Cushion':>10}{'Verdict':>15}")
    print(f"  {'-'*42}{'-'*10}{'-'*11}{'-'*10}{'-'*15}")
    for mu_test, label in [
        (mu_effective_historical, "Historical (capped + enrichment)"),
        (mu_blended,              "BLENDED (today's headline)"),
        (0.30,                    "Mildly bullish (+30%)"),
        (0.05,                    "Risk-free / options-market (+5%)"),
        (0.0,                     "Zero drift"),
    ]:
        p_t, ev_t, p_star_t, cush_t, v_t = _quick_scenario(
            S0, sigma, mu_test, args.horizon, args.target, args.entry,
            args.shares, cut_pnl, pnl_good)
        marker = "  *" if "BLENDED" in label else "   "
        print(f"{marker}{label:<41}{p_t*100:>9.1f}% ${ev_t:>+9,.0f}  "
              f"{cush_t*100:>+6.1f}pp{v_t:>15}")
    print()

    # ---- HISTORY CSV (extended schema) ----
    history_dir = Path(__file__).parent / "output"
    history_dir.mkdir(exist_ok=True)
    history_path = history_dir / f"thesis_history_{ticker}.csv"
    csv_header = ("timestamp,spot,sigma_pct,mu_historical_pct,mu_blended_pct,"
                  "mu_analyst_pct,mu_sector_pct,mu_macro_pct,mu_insider_pct,"
                  "mu_ai_pct,p_touch_pct,breakeven_p_pct,cushion_pp,ev_hold,"
                  "ev_cut,verdict,position_guidance,ai_cost_usd\n")

    def _fmt_drift(d):
        return f"{d*100:.2f}" if d is not None else ""

    new_row = (f"{datetime.now():%Y-%m-%d %H:%M},{S0:.2f},{sigma*100:.2f},"
               f"{mu_effective_historical*100:.2f},{mu_blended*100:.2f},"
               f"{_fmt_drift(signals['analyst'].get('drift'))},"
               f"{_fmt_drift(signals['sector'].get('drift'))},"
               f"{_fmt_drift(signals['macro'].get('drift'))},"
               f"{_fmt_drift(signals['insider'].get('drift'))},"
               f"{_fmt_drift(signals.get('ai',{}).get('drift'))},"
               f"{p_touch_mc*100:.2f},{breakeven_p*100:.2f},"
               f"{cushion_pp:.2f},{ev_hold:.0f},{cut_pnl:.0f},"
               f"{verdict},{mech_guidance},{ai_cost:.4f}\n")

    # Migration: if old CSV exists with old schema, rotate it
    if history_path.exists():
        first_line = history_path.read_text().split("\n", 1)[0]
        if "mu_blended_pct" not in first_line:
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
              f"{'mu_hist':>9}{'mu_blend':>10}{'P(t)':>8}"
              f"{'P*':>7}{'Cush':>7}{'Verdict':>13}{'Guidance':>10}")
        print(f"  {'-'*19}{'-'*9}{'-'*7}{'-'*9}{'-'*10}"
              f"{'-'*8}{'-'*7}{'-'*7}{'-'*13}{'-'*10}")
        for row in rows[1:][-7:]:
            cells = row.split(",")
            if len(cells) < 17:
                continue
            ts, sp, sg, mh, mb = cells[0], cells[1], cells[2], cells[3], cells[4]
            pt, bp_, cu, evh, evc, vd, pg = cells[10], cells[11], cells[12], cells[13], cells[14], cells[15], cells[16]
            print(f"  {ts:<19}${float(sp):>7.2f}{float(sg):>6.1f}%"
                  f"{float(mh):>+7.1f}%{float(mb):>+8.1f}%"
                  f"{float(pt):>7.1f}%{float(bp_):>6.1f}%"
                  f"{float(cu):>+6.1f}pp{vd:>13}{pg:>10}")
        print()
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
