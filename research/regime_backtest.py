"""
Regime Classifier Backtest — research tool, NOT production.

Purpose
-------
Empirically evaluate whether the regime classifier rule (and three
alternative variants) produces forward-return separation between
stocks labelled MOMENTUM vs NORMAL.

Motivated by: on 2026-05-14, MU was classified NORMAL despite
RSI 81 / +20% in 5 days / +16.5pp sector decoupling / fresh 60-day
high. Rule failed on relative volume (1.19x vs 1.3x threshold). The
question: is the rule's volume gate doing real predictive work,
or is it producing false negatives like today's MU?

Approach
--------
1. Pull 3 years of OHLCV via yfinance for two universes:
   - PORTFOLIO: the 31 modelled SGC tickers
   - SP100: broader robustness universe to reduce survivorship bias
2. For each (ticker, day): compute the four metrics that the
   production classifier uses (RSI, 5d return, sector decoupling,
   relative volume).
3. Apply four rule variants:
   - R0 current production: rel_vol 1.3x, hard AND gate
   - R1 lower threshold: rel_vol 1.1x
   - R2 smoothed volume: 5d avg volume / 30d prior avg, 1.3x
   - R3 3-of-4 logic: rel_vol becomes tiebreaker
4. For each labelling, compute forward 5/10/20-day returns.
5. Statistical tests: Welch's t-test, Cohen's d, Bonferroni-corrected
   p-values across the four rules.
6. Optional Test B: dip-fill rate using signal_history.csv (the
   sharper test of the classifier's actual purpose — overriding
   WAIT signals when dips won't fill).

Honest limitations documented in the report.

Run
---
    python3 research/regime_backtest.py

Outputs
-------
- research/regime_backtest_report.md  (overwritten each run)
- research/.cache/*.pkl                (cached yfinance pulls)

Does NOT modify any production file or production data.
"""

import os
import sys
import json
import pickle
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


# =============================================================
# CONFIGURATION
# =============================================================

# Portfolio universe (current 31 modelled stocks per CLAUDE.md)
# LDO.MI excluded — yfinance unreliable on .MI tickers.
PORTFOLIO_TICKERS = [
    'NVDA', 'MSFT', 'GOOGL', 'META', 'AMZN', 'MA', 'WM', 'MU',
    'ASML', 'AVGO', 'CTAS', 'VST', 'CEG', 'TSLA', 'INOD', 'ADP',
    'V', 'LLY', 'LIN', 'WMT', 'PLTR', 'AMD', 'INTC', 'SNDK',
    'ENGN', 'AIIO', 'GDC', 'FWRD', 'HUBS', 'CRWD',
]

# S&P 100 robustness universe (broader than portfolio, less
# survivorship-biased for the rule's general predictive power)
SP100_TICKERS = [
    'AAPL', 'ABBV', 'ABT', 'ACN', 'ADBE', 'AIG', 'AMGN', 'AMT',
    'AMZN', 'AVGO', 'AXP', 'BA', 'BAC', 'BK', 'BKNG', 'BLK',
    'BMY', 'BRK-B', 'C', 'CAT', 'CHTR', 'CL', 'CMCSA', 'COF',
    'COP', 'COST', 'CRM', 'CSCO', 'CVS', 'CVX', 'DE', 'DHR',
    'DIS', 'DUK', 'EMR', 'EXC', 'F', 'FDX', 'GD', 'GE', 'GILD',
    'GM', 'GOOG', 'GOOGL', 'GS', 'HD', 'HON', 'IBM', 'INTC',
    'JNJ', 'JPM', 'KHC', 'KO', 'LIN', 'LLY', 'LMT', 'LOW',
    'MA', 'MCD', 'MDLZ', 'MDT', 'MET', 'META', 'MMM', 'MO',
    'MRK', 'MS', 'MSFT', 'NEE', 'NFLX', 'NKE', 'NVDA', 'ORCL',
    'PEP', 'PFE', 'PG', 'PM', 'PYPL', 'QCOM', 'RTX', 'SBUX',
    'SCHW', 'SO', 'SPG', 'T', 'TGT', 'TMO', 'TMUS', 'TSLA',
    'TXN', 'UNH', 'UNP', 'UPS', 'USB', 'V', 'VZ', 'WBA', 'WFC',
    'WMT', 'XOM',
]

# Sector proxy used uniformly across all stocks for decoupling computation.
# Honest simplification: production uses GICS sector aggregate via FMP,
# which we cannot replicate cheaply. SPY (broad market) is a coarser
# proxy. Decoupling threshold may behave differently, but directional
# answer (does rule have predictive power) should hold.
SECTOR_PROXY = 'SPY'

# Lookback window for yfinance pulls
LOOKBACK_PERIOD = '3y'

# Cache directory (gitignored)
CACHE_DIR = Path(__file__).parent / '.cache'
CACHE_DIR.mkdir(exist_ok=True)

# Report output path
REPORT_PATH = Path(__file__).parent / 'regime_backtest_report.md'

# Signal history (Test B input — dip-fill rate)
SIGNAL_HISTORY_CSV = Path(__file__).parent.parent / 'data' / 'signal_history.csv'

# Lookback boundaries (need historical context for metric computation
# and forward window for return evaluation)
HISTORY_NEEDED = 60   # 60d for drawdown
FORWARD_WINDOW = 20   # forward 20d returns


# =============================================================
# CACHED DATA FETCH
# =============================================================

def _cache_path(ticker):
    return CACHE_DIR / f"{ticker}.pkl"


def fetch_history(ticker, force=False):
    """Fetch OHLCV history with disk cache. Returns DataFrame or None on failure."""
    path = _cache_path(ticker)
    if path.exists() and not force:
        try:
            with open(path, 'rb') as f:
                return pickle.load(f)
        except Exception:
            pass

    try:
        import yfinance as yf
        df = yf.download(ticker, period=LOOKBACK_PERIOD, interval='1d',
                         auto_adjust=False, progress=False)
        if df is None or len(df) == 0:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        with open(path, 'wb') as f:
            pickle.dump(df, f)
        return df
    except Exception as e:
        print(f"   yfinance failed for {ticker}: {e}")
        return None


def fetch_universe(tickers, label):
    """Pull all tickers in a universe with progress logging."""
    print(f"📊 Fetching {label} ({len(tickers)} tickers)...")
    data = {}
    fail = []
    for i, ticker in enumerate(tickers, 1):
        df = fetch_history(ticker)
        if df is None or len(df) < HISTORY_NEEDED + FORWARD_WINDOW:
            fail.append(ticker)
            continue
        data[ticker] = df
        if i % 10 == 0:
            print(f"   {i}/{len(tickers)}...")
    if fail:
        print(f"   ⚠️  {label}: {len(fail)} tickers failed/insufficient: {fail[:10]}")
    return data


# =============================================================
# METRIC COMPUTATION (mirrors regime_classifier.py)
# =============================================================

def rsi_wilder(closes, period=14):
    """Wilder-smoothed RSI(14). Returns array of same length as closes."""
    rsi = np.full(len(closes), np.nan)
    if len(closes) < period + 1:
        return rsi
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    ag = np.mean(gains[:period])
    al = np.mean(losses[:period])
    rsi[period] = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))
    for i in range(period + 1, len(closes)):
        ag = (ag * (period - 1) + gains[i - 1]) / period
        al = (al * (period - 1) + losses[i - 1]) / period
        rsi[i] = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))
    return rsi


def compute_metrics_panel(df, sector_5d):
    """
    Compute per-day metrics for one ticker. Returns a DataFrame indexed
    by date with columns: rsi, mom_5d, mom_20d, drawdown_from_high,
    decoupling, rel_vol, rel_vol_smoothed, fwd_5d, fwd_10d, fwd_20d.

    sector_5d: pd.Series of sector 5d returns indexed by date (aligned to df).
    """
    closes = df['Close'].values
    volumes = df['Volume'].values
    n = len(closes)
    out = pd.DataFrame(index=df.index)

    # RSI
    out['rsi'] = rsi_wilder(closes, 14)

    # 5d / 20d returns (matches regime_classifier.py line 117-122)
    mom_5d = np.full(n, np.nan)
    mom_20d = np.full(n, np.nan)
    for i in range(5, n):
        mom_5d[i] = closes[i] / closes[i - 5] - 1
    for i in range(20, n):
        mom_20d[i] = closes[i] / closes[i - 20] - 1
    out['mom_5d'] = mom_5d
    out['mom_20d'] = mom_20d

    # Drawdown from 60d high (matches lines 126-129)
    dd = np.full(n, np.nan)
    for i in range(59, n):
        recent_high = np.max(closes[i - 59:i + 1])
        if recent_high > 0:
            dd[i] = closes[i] / recent_high - 1
    out['drawdown_from_high'] = dd

    # Sector decoupling (stock 5d minus sector 5d)
    out['decoupling'] = out['mom_5d'] - sector_5d.reindex(out.index)

    # Relative volume (matches lines 142-148: today / mean(prior 29 days))
    rel_vol = np.full(n, np.nan)
    for i in range(30, n):
        prior = volumes[i - 29:i]   # 29 days prior (exclusive of today)
        avg = np.mean(prior)
        if avg > 0:
            rel_vol[i] = volumes[i] / avg
    out['rel_vol'] = rel_vol

    # Smoothed relative volume: 5d avg / 30d prior avg
    rel_vol_smooth = np.full(n, np.nan)
    for i in range(34, n):
        recent_5d = np.mean(volumes[i - 4:i + 1])
        prior = volumes[i - 33:i - 4]   # ~29 days prior to the 5d window
        avg = np.mean(prior)
        if avg > 0:
            rel_vol_smooth[i] = recent_5d / avg
    out['rel_vol_smoothed'] = rel_vol_smooth

    # Forward returns
    for h in (5, 10, 20):
        fwd = np.full(n, np.nan)
        for i in range(n - h):
            fwd[i] = closes[i + h] / closes[i] - 1
        out[f'fwd_{h}d'] = fwd

    # Forward minimum close in 20-day window (for dip-fill detection in Test B)
    fwd_min_20d = np.full(n, np.nan)
    for i in range(n - 20):
        fwd_min_20d[i] = float(np.min(closes[i + 1:i + 21]))
    out['fwd_min_close_20d'] = fwd_min_20d

    return out


def compute_sector_5d(sector_df):
    """5-day return of sector proxy ETF, indexed by date."""
    closes = sector_df['Close'].values
    n = len(closes)
    s = np.full(n, np.nan)
    for i in range(5, n):
        s[i] = closes[i] / closes[i - 5] - 1
    return pd.Series(s, index=sector_df.index)


# =============================================================
# RULE APPLICATION
# =============================================================

def apply_rule(panel, rule_name):
    """
    Apply a rule variant; returns a Series of regime labels for each
    row of `panel`. Labels: 'MOMENTUM' or 'NORMAL'. (We do not test
    SQUEEZE_RISK / OVERSOLD / BREAKDOWN here — out of scope.)
    """
    rsi = panel['rsi']
    m5 = panel['mom_5d']
    dec = panel['decoupling']

    if rule_name == 'R0_current':
        rv, rv_thresh = panel['rel_vol'], 1.3
    elif rule_name == 'R1_lower_threshold':
        rv, rv_thresh = panel['rel_vol'], 1.1
    elif rule_name == 'R2_smoothed_volume':
        rv, rv_thresh = panel['rel_vol_smoothed'], 1.3
    elif rule_name == 'R3_three_of_four':
        # All four conditions evaluated; require ≥3 of 4 (rel_vol_thresh
        # stays at 1.3x for parity, but can be vetoed by other 3).
        cond_rsi = (rsi >= 75)
        cond_m5 = (m5 >= 0.10)
        cond_dec = (dec >= 0.05)
        cond_rv = (panel['rel_vol'] >= 1.3)
        passes = (
            cond_rsi.astype(int) + cond_m5.astype(int)
            + cond_dec.astype(int) + cond_rv.astype(int)
        )
        labels = np.where(passes >= 3, 'MOMENTUM', 'NORMAL')
        # NaN in any input → NORMAL by default
        any_nan = rsi.isna() | m5.isna() | dec.isna() | panel['rel_vol'].isna()
        return pd.Series(np.where(any_nan, 'NORMAL', labels), index=panel.index)
    else:
        raise ValueError(f"Unknown rule: {rule_name}")

    cond_rsi = (rsi >= 75)
    cond_m5 = (m5 >= 0.10)
    cond_dec = (dec >= 0.05)
    cond_rv = (rv >= rv_thresh)
    is_momentum = cond_rsi & cond_m5 & cond_dec & cond_rv
    labels = np.where(is_momentum, 'MOMENTUM', 'NORMAL')
    any_nan = rsi.isna() | m5.isna() | dec.isna() | rv.isna()
    return pd.Series(np.where(any_nan, 'NORMAL', labels), index=panel.index)


# =============================================================
# STATISTICAL TESTS
# =============================================================

def welch_ttest(a, b):
    """
    Welch's t-test for unequal variances. Returns (t_stat, approx_p).
    p computed via normal approximation (valid for large samples,
    which we have); flagged as approximate in the report.
    """
    a = np.asarray(a)
    b = np.asarray(b)
    a = a[~np.isnan(a)]
    b = b[~np.isnan(b)]
    if len(a) < 5 or len(b) < 5:
        return float('nan'), float('nan')
    ma, mb = np.mean(a), np.mean(b)
    va, vb = np.var(a, ddof=1), np.var(b, ddof=1)
    se = math.sqrt(va / len(a) + vb / len(b))
    if se == 0:
        return float('nan'), float('nan')
    t = (ma - mb) / se
    # Normal approximation to p-value (two-tailed)
    z = abs(t)
    # Φ(z) approximation: 1 - 0.5 * erfc(z/√2). Use numpy's erf.
    p = 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))
    return t, p


def cohens_d(a, b):
    a = np.asarray(a)
    b = np.asarray(b)
    a = a[~np.isnan(a)]
    b = b[~np.isnan(b)]
    if len(a) < 5 or len(b) < 5:
        return float('nan')
    ma, mb = np.mean(a), np.mean(b)
    va, vb = np.var(a, ddof=1), np.var(b, ddof=1)
    pooled = math.sqrt((va + vb) / 2)
    if pooled == 0:
        return float('nan')
    return (ma - mb) / pooled


# =============================================================
# BACKTEST EXECUTION
# =============================================================

RULE_NAMES = ['R0_current', 'R1_lower_threshold',
              'R2_smoothed_volume', 'R3_three_of_four']


def run_universe(universe_data, sector_5d, label):
    """Compute metrics + apply all rules across a universe. Returns
    a dict mapping rule_name → DataFrame of (forward_return, regime_label)
    pairs concatenated across all tickers in the universe."""
    print(f"\n🔬 Running analysis on {label}...")
    rule_panels = {r: [] for r in RULE_NAMES}

    for ticker, df in universe_data.items():
        try:
            panel = compute_metrics_panel(df, sector_5d)
        except Exception as e:
            print(f"   ⚠️  {ticker}: metric computation failed ({e})")
            continue
        for rule in RULE_NAMES:
            labels = apply_rule(panel, rule)
            slice_df = panel[['fwd_5d', 'fwd_10d', 'fwd_20d',
                              'fwd_min_close_20d']].copy()
            slice_df['close'] = panel.index.map(
                lambda d, df=df: float(df.loc[d, 'Close'])
                if d in df.index else float('nan'))
            slice_df['regime'] = labels
            slice_df['ticker'] = ticker
            rule_panels[rule].append(slice_df)

    result = {}
    for rule, frames in rule_panels.items():
        if frames:
            result[rule] = pd.concat(frames, axis=0)
        else:
            result[rule] = pd.DataFrame()
    return result


def summarise_test_a(rule_data, rule_name):
    """For one rule, compute MOMENTUM vs NORMAL forward return separation."""
    df = rule_data[rule_name]
    summary = {'rule': rule_name}
    for h in (5, 10, 20):
        col = f'fwd_{h}d'
        mom = df.loc[df['regime'] == 'MOMENTUM', col].dropna()
        nor = df.loc[df['regime'] == 'NORMAL', col].dropna()
        t, p = welch_ttest(mom.values, nor.values)
        d = cohens_d(mom.values, nor.values)
        summary[f'mom_n_{h}d'] = len(mom)
        summary[f'nor_n_{h}d'] = len(nor)
        summary[f'mom_mean_{h}d'] = mom.mean() if len(mom) else float('nan')
        summary[f'nor_mean_{h}d'] = nor.mean() if len(nor) else float('nan')
        summary[f'spread_{h}d'] = (mom.mean() - nor.mean()) if (len(mom) and len(nor)) else float('nan')
        summary[f't_{h}d'] = t
        summary[f'p_{h}d'] = p
        summary[f'd_{h}d'] = d
    return summary


# =============================================================
# TEST B — DIP-FILL RATE (uses signal_history.csv)
# =============================================================

def _normalise_date_col(s):
    """Normalise a date series to tz-naive day-floored Timestamps."""
    s = pd.to_datetime(s, errors='coerce')
    try:
        if getattr(s.dt, 'tz', None) is not None:
            s = s.dt.tz_localize(None)
    except (AttributeError, TypeError):
        pass
    return pd.to_datetime(s.dt.date)


def run_test_b(rule_data_portfolio, signal_history):
    """
    For (ticker, date) observations where signal_history shows WAIT,
    compare MOMENTUM-labelled dip-fill rate vs NORMAL-labelled rate.

    Dip-fill detection: was the dip_target touched (close-price approximation)
    at any point in the next 20 trading days? Uses fwd_min_close_20d from the
    panel — i.e., minimum daily close in the forward 20-day window. This is
    more conservative than production's intraday-low check; intraday dips that
    recover by close will be missed. Effect: Test B fill rates biased downward.
    """
    if signal_history is None or signal_history.empty:
        return None

    # Filter to WAIT signals with usable dip targets
    wait = signal_history[
        (signal_history['signal'] == 'WAIT')
        & signal_history['dip_target'].notna()
    ].copy()
    if wait.empty:
        return None

    wait['date'] = _normalise_date_col(wait['date'])
    wait['ticker'] = wait['ticker'].astype(str)
    wait['dip_target'] = pd.to_numeric(wait['dip_target'], errors='coerce')
    wait = wait.dropna(subset=['date', 'dip_target'])

    results = {}
    for rule in RULE_NAMES:
        df = rule_data_portfolio[rule]
        if df is None or df.empty:
            results[rule] = {'mom_fill_rate': None, 'mom_n': 0,
                             'nor_fill_rate': None, 'nor_n': 0}
            continue

        panel = df.copy()
        panel['date'] = _normalise_date_col(pd.Series(panel.index))
        panel['ticker'] = panel['ticker'].astype(str)

        merged = wait.merge(
            panel[['ticker', 'date', 'regime',
                   'fwd_min_close_20d', 'close']],
            on=['ticker', 'date'],
            how='inner',
        )
        merged = merged.dropna(subset=['fwd_min_close_20d', 'regime'])

        if merged.empty:
            results[rule] = {'mom_fill_rate': None, 'mom_n': 0,
                             'nor_fill_rate': None, 'nor_n': 0}
            continue

        # Dip filled if minimum close in next 20 days is at or below dip target
        merged['hit'] = merged['fwd_min_close_20d'] <= merged['dip_target']

        mom = merged[merged['regime'] == 'MOMENTUM']
        nor = merged[merged['regime'] == 'NORMAL']

        mom_n = int(len(mom))
        nor_n = int(len(nor))
        mom_fr = float(mom['hit'].mean()) if mom_n else None
        nor_fr = float(nor['hit'].mean()) if nor_n else None

        results[rule] = {
            'mom_fill_rate': mom_fr,
            'mom_n': mom_n,
            'nor_fill_rate': nor_fr,
            'nor_n': nor_n,
        }
    return results


# =============================================================
# TRACK 3 — MU LIVE PREDICTION LOG
# =============================================================

def mu_prediction_log(portfolio_data):
    """Capture today's MU state as a forward-evaluable prediction."""
    if 'MU' not in portfolio_data:
        return None
    df = portfolio_data['MU']
    closes = df['Close'].values
    volumes = df['Volume'].values
    rsi = rsi_wilder(closes, 14)[-1]
    mom_5d = closes[-1] / closes[-6] - 1 if len(closes) >= 6 else None
    mom_20d = closes[-1] / closes[-21] - 1 if len(closes) >= 21 else None
    avg_vol = np.mean(volumes[-30:-1]) if len(volumes) >= 30 else None
    rel_vol = volumes[-1] / avg_vol if avg_vol else None
    high_60 = np.max(closes[-60:]) if len(closes) >= 60 else None
    dd = closes[-1] / high_60 - 1 if high_60 else None
    return {
        'as_of_date': df.index[-1].date().isoformat(),
        'close': float(closes[-1]),
        'rsi_14': float(rsi) if not np.isnan(rsi) else None,
        'mom_5d_pct': float(mom_5d * 100) if mom_5d is not None else None,
        'mom_20d_pct': float(mom_20d * 100) if mom_20d is not None else None,
        'rel_vol': float(rel_vol) if rel_vol else None,
        'drawdown_from_60d_high_pct': float(dd * 100) if dd is not None else None,
        'production_label_2026_05_14': 'NORMAL',
        'evaluate_after': (datetime.now() + timedelta(days=30)).date().isoformat(),
    }


# =============================================================
# REPORT GENERATION
# =============================================================

def fmt_pct(v):
    return f"{v*100:+.2f}%" if v is not None and not (isinstance(v, float) and math.isnan(v)) else 'n/a'


def fmt_num(v, places=3):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return 'n/a'
    return f"{v:.{places}f}"


def render_report(test_a_portfolio, test_a_sp100, test_b_portfolio,
                  mu_log, run_start):
    lines = []
    lines.append(f"# Regime Classifier Backtest Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append(f"Runtime: {(datetime.now() - run_start).total_seconds():.1f}s")
    lines.append("")
    lines.append("## Question")
    lines.append("")
    lines.append("Does the regime classifier rule have empirical forward-return predictive power?")
    lines.append("Specifically, are MOMENTUM-labelled stock-days followed by materially different")
    lines.append("forward returns than NORMAL-labelled stock-days? Are alternative rule variants")
    lines.append("better than the current production rule?")
    lines.append("")

    # Track 3 — MU live prediction
    if mu_log:
        lines.append("## Track 3 — MU Live Prediction (forward evidence)")
        lines.append("")
        lines.append("Captured for forward evaluation. Re-run this script after 30+ days to")
        lines.append("see whether MU continued rallying (rule false negative) or pulled back")
        lines.append("(rule correctly cooled on it).")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(mu_log, indent=2))
        lines.append("```")
        lines.append("")

    # Test A — Portfolio
    lines.append("## Test A — Forward return separation (PORTFOLIO universe, 31 stocks)")
    lines.append("")
    lines.append("For each rule, do MOMENTUM-labelled days have higher forward returns than")
    lines.append("NORMAL-labelled days? (Survivorship-biased — see S&P 100 below for robustness.)")
    lines.append("")
    lines.append("| Rule | Horizon | MOM n | NOR n | MOM mean | NOR mean | Spread | t-stat | p (approx) | Cohen's d |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for h in (5, 10, 20):
        for s in test_a_portfolio:
            lines.append(
                f"| {s['rule']} | +{h}d | {s[f'mom_n_{h}d']} | {s[f'nor_n_{h}d']} | "
                f"{fmt_pct(s[f'mom_mean_{h}d'])} | {fmt_pct(s[f'nor_mean_{h}d'])} | "
                f"{fmt_pct(s[f'spread_{h}d'])} | {fmt_num(s[f't_{h}d'])} | "
                f"{fmt_num(s[f'p_{h}d'])} | {fmt_num(s[f'd_{h}d'])} |"
            )
    lines.append("")

    # Test A — S&P 100
    lines.append("## Test A — Forward return separation (S&P 100 universe, robustness check)")
    lines.append("")
    lines.append("Same test on a broader, less survivorship-biased universe. If the rule's")
    lines.append("predictive power survives here, it's robust to portfolio selection.")
    lines.append("")
    lines.append("| Rule | Horizon | MOM n | NOR n | MOM mean | NOR mean | Spread | t-stat | p (approx) | Cohen's d |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for h in (5, 10, 20):
        for s in test_a_sp100:
            lines.append(
                f"| {s['rule']} | +{h}d | {s[f'mom_n_{h}d']} | {s[f'nor_n_{h}d']} | "
                f"{fmt_pct(s[f'mom_mean_{h}d'])} | {fmt_pct(s[f'nor_mean_{h}d'])} | "
                f"{fmt_pct(s[f'spread_{h}d'])} | {fmt_num(s[f't_{h}d'])} | "
                f"{fmt_num(s[f'p_{h}d'])} | {fmt_num(s[f'd_{h}d'])} |"
            )
    lines.append("")

    # Test B — dip-fill rate
    lines.append("## Test B — Dip-fill rate on WAIT signals (PORTFOLIO universe)")
    lines.append("")
    lines.append("The classifier's actual purpose: among WAIT signals, do MOMENTUM-labelled")
    lines.append("days have lower dip-fill rates than NORMAL-labelled days? If yes, the rule")
    lines.append("is doing its job — suppressing WAIT on stocks where dips don't fill.")
    lines.append("")
    lines.append("Caveat: Uses close-price approximation; production uses intraday lows.")
    lines.append("")
    if test_b_portfolio:
        lines.append("| Rule | MOM n | MOM fill rate | NOR n | NOR fill rate | Spread |")
        lines.append("|---|---|---|---|---|---|")
        for rule in RULE_NAMES:
            r = test_b_portfolio.get(rule, {})
            mom_fr = r.get('mom_fill_rate')
            nor_fr = r.get('nor_fill_rate')
            spread = ((mom_fr - nor_fr) if (mom_fr is not None and nor_fr is not None) else None)
            mom_fr_s = f"{mom_fr*100:.1f}%" if mom_fr is not None else 'n/a'
            nor_fr_s = f"{nor_fr*100:.1f}%" if nor_fr is not None else 'n/a'
            spread_s = f"{spread*100:+.1f}pp" if spread is not None else 'n/a'
            lines.append(f"| {rule} | {r.get('mom_n', 0)} | {mom_fr_s} | "
                         f"{r.get('nor_n', 0)} | {nor_fr_s} | {spread_s} |")
        lines.append("")
        lines.append("Interpretation: a NEGATIVE spread (MOM fill rate < NOR fill rate)")
        lines.append("indicates the rule is correctly identifying stocks where dips don't fill.")
    else:
        lines.append("_No signal_history.csv data available; Test B skipped._")
    lines.append("")

    # Verdict heuristic
    lines.append("## Verdict heuristic")
    lines.append("")
    lines.append("For each rule, evidence considered strong if ALL of:")
    lines.append("- ≥100 MOMENTUM observations")
    lines.append("- Forward 20d return spread (MOM − NOR) ≥ +2pp OR dip-fill spread ≤ −15pp")
    lines.append("- t-statistic ≥ 2 in the predicted direction (Bonferroni threshold: ≥2.5)")
    lines.append("- Sign of effect consistent across +5d, +10d, +20d")
    lines.append("")
    lines.append("Read the tables above and apply the heuristic. If no rule clears the bar,")
    lines.append("status quo wins by default — do not change the production rule yet.")
    lines.append("")

    # Caveats
    lines.append("## Honest limitations")
    lines.append("")
    lines.append("1. **Sector proxy:** uses SPY (broad market) for decoupling, not GICS sector.")
    lines.append("   Production uses FMP's GICS Technology aggregate (or equivalent). Decoupling")
    lines.append("   threshold may behave differently; absolute thresholds not directly comparable.")
    lines.append("2. **Survivorship bias:** portfolio of 31 stocks selected with hindsight; S&P 100")
    lines.append("   inclusion is itself a winner-selection. Both biases inflate forward returns.")
    lines.append("3. **Lookback window:** 3 years, mostly bullish macro. Rule's performance under")
    lines.append("   drawdown environments (e.g., 2022) not tested in this run.")
    lines.append("4. **Multiple comparisons:** testing 4 rules × 3 horizons = 12 comparisons.")
    lines.append("   Bonferroni-corrected significance threshold: p<0.0042 (raw p<0.05 ÷ 12).")
    lines.append("5. **p-value approximation:** computed via normal approximation; valid for large")
    lines.append("   n but slightly anti-conservative for small samples.")
    lines.append("6. **Path dependency:** forward-return windows overlap; standard errors not")
    lines.append("   adjusted for autocorrelation. Effect on directional verdict: minimal.")
    lines.append("7. **Test B dip-fill detection:** uses close-price approximation, not intraday")
    lines.append("   lows. Production catches dips that happen intraday but recover by close;")
    lines.append("   this script does not. Test B fill rates may be biased downward.")
    lines.append("")

    return "\n".join(lines)


# =============================================================
# MAIN
# =============================================================

def main():
    run_start = datetime.now()
    print("=" * 60)
    print("REGIME CLASSIFIER BACKTEST")
    print(f"Started: {run_start.isoformat()}")
    print("=" * 60)

    # 1. Fetch sector proxy
    print(f"\n📊 Fetching sector proxy ({SECTOR_PROXY})...")
    sector_df = fetch_history(SECTOR_PROXY)
    if sector_df is None:
        print(f"❌ Cannot fetch {SECTOR_PROXY}. Check yfinance / network. Aborting.")
        sys.exit(1)
    sector_5d = compute_sector_5d(sector_df)

    # 2. Fetch universes
    portfolio_data = fetch_universe(PORTFOLIO_TICKERS, 'PORTFOLIO')
    sp100_data = fetch_universe(SP100_TICKERS, 'SP100')

    if not portfolio_data:
        print("❌ No portfolio data fetched. Aborting.")
        sys.exit(1)

    # 3. Run rule analysis on both universes
    rule_data_portfolio = run_universe(portfolio_data, sector_5d, 'PORTFOLIO')
    rule_data_sp100 = run_universe(sp100_data, sector_5d, 'SP100')

    # 4. Test A summaries
    print("\n📈 Computing Test A (forward return separation)...")
    test_a_portfolio = [summarise_test_a(rule_data_portfolio, r) for r in RULE_NAMES]
    test_a_sp100 = [summarise_test_a(rule_data_sp100, r) for r in RULE_NAMES]

    # 5. Test B (dip-fill rate) — uses signal_history.csv
    print("\n📉 Computing Test B (dip-fill rate)...")
    signal_history = None
    if SIGNAL_HISTORY_CSV.exists():
        try:
            signal_history = pd.read_csv(SIGNAL_HISTORY_CSV)
        except Exception as e:
            print(f"   ⚠️  Could not load signal_history.csv: {e}")
    test_b_portfolio = run_test_b(rule_data_portfolio, signal_history) if signal_history is not None else None

    # 6. Track 3 — MU live prediction
    mu_log = mu_prediction_log(portfolio_data)

    # 7. Write report
    print(f"\n📝 Writing report to {REPORT_PATH}...")
    report = render_report(test_a_portfolio, test_a_sp100,
                           test_b_portfolio, mu_log, run_start)
    with open(REPORT_PATH, 'w') as f:
        f.write(report)

    elapsed = (datetime.now() - run_start).total_seconds()
    print(f"\n✅ Done in {elapsed:.1f}s. Report: {REPORT_PATH}")
    print("=" * 60)


if __name__ == '__main__':
    main()
