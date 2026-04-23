"""
Correlated Monte Carlo Simulation Engine
10,000 correlated price paths × 60 days per stock

PHASE 2 ENRICHMENT (Apr 2026):
  Five data streams now feed into the MC simulation:
    1. RSI → drift modifier (overbought = dip more likely)
    2. Sentiment → drift modifier (Claude API score)
    3. Momentum → drift modifier (contrarian: strong up = pullback likely)
    4. Insider stats → drift modifier (heavy selling = bearish)
    5. Earnings date → vol multiplier (imminent earnings = vol spike)

  Each modifier is small individually (±0.01 to ±0.05).
  Combined, they differentiate stocks meaningfully.
  Total enrichment drift capped at ±0.10 to prevent extreme combined effects.

CONVICTION MODEL:
  Dip target = 60th percentile of path minimums.
  Signal driven by dip depth vs 3% materiality threshold.
"""

import numpy as np
import pandas as pd
from datetime import datetime
from config import NUM_PATHS, SIMULATION_DAYS, PERCENTILE_TARGET
from correlation import generate_correlated_random_numbers


# =============================================================
# PHASE 2: ENRICHMENT MODIFIERS
# =============================================================

def compute_enrichment_modifiers(stock_data):
    """
    Compute drift and vol modifiers from enrichment data.

    Args:
        stock_data: dict from portfolio_data for one stock

    Returns: dict with 'drift_adjustment' and 'vol_multiplier'
             plus individual modifier values for logging

    All modifiers default to 0 / 1.0 if data is missing.
    """

    drift_mods = {}
    vol_mult = 1.0

    # --- RSI modifier ---
    # RSI 70+ (overbought): drift down, dip more likely
    # RSI 30- (oversold): drift up, bounce likely
    # RSI 50 (neutral): no effect
    # Scale: (50 - RSI) / 500 → RSI 70: -0.04, RSI 30: +0.04
    rsi = stock_data.get('rsi')
    if rsi is not None:
        drift_mods['rsi'] = (50.0 - rsi) / 500.0
    else:
        drift_mods['rsi'] = 0.0

    # --- Sentiment modifier ---
    # Claude score -5 to +5 → drift modifier
    # Scale: score / 100 → +5: +0.05, -5: -0.05
    sentiment = stock_data.get('sentiment')
    if sentiment and isinstance(sentiment, dict):
        score = sentiment.get('sentiment_score', 0.0)
        drift_mods['sentiment'] = score / 100.0
    else:
        drift_mods['sentiment'] = 0.0

    # --- Momentum modifier (contrarian) ---
    # Strong positive 1M momentum → mild drag (what rips tends to pull back)
    # Strong negative momentum → mild boost (oversold bounce)
    # Scale: -momentum_1M / 1000 → +17%: -0.017, -10%: +0.01
    momentum = stock_data.get('momentum', {})
    mom_1m = momentum.get('1M')
    if mom_1m is not None:
        drift_mods['momentum'] = -mom_1m / 1000.0
    else:
        drift_mods['momentum'] = 0.0

    # --- Insider modifier ---
    # acquiredDisposedRatio: >1 = net buying, <1 = net selling
    # 0.5 = neutral midpoint for the modifier
    # Scale: (ratio - 0.5) / 25 → ratio 0.16 (heavy selling): -0.014
    # Capped at ±0.03
    insider = stock_data.get('insider_stats', {})
    ratio = insider.get('acquiredDisposedRatio')
    if ratio is not None:
        raw = (ratio - 0.5) / 25.0
        drift_mods['insider'] = max(-0.03, min(0.03, raw))
    else:
        drift_mods['insider'] = 0.0

    # --- Total drift adjustment (capped at ±0.10) ---
    total_drift = sum(drift_mods.values())
    total_drift = max(-0.10, min(0.10, total_drift))

    # --- Earnings vol multiplier ---
    # Imminent earnings → vol spike (earnings cause big moves)
    # Within 14 days: × 1.5
    # Within 14-30 days: × 1.3
    # Within 30-60 days: × 1.15
    # Outside window or no date: × 1.0
    earnings_date = stock_data.get('earnings_date')
    if earnings_date:
        try:
            ed = datetime.strptime(earnings_date, '%Y-%m-%d').date()
            days_to_earnings = (ed - datetime.now().date()).days
            if 0 <= days_to_earnings <= 14:
                vol_mult = 1.5
            elif 14 < days_to_earnings <= 30:
                vol_mult = 1.3
            elif 30 < days_to_earnings <= 60:
                vol_mult = 1.15
        except:
            pass

    return {
        'drift_adjustment': total_drift,
        'vol_multiplier': vol_mult,
        'modifiers': drift_mods,
    }


# =============================================================
# SIMULATION ENGINE
# =============================================================

def run_monte_carlo_stock(
    current_price,
    volatility,
    drift_mult,
    vol_mult,
    mean_reversion_anchor,
    enrichment_drift=0.0,
    enrichment_vol_mult=1.0,
    days=SIMULATION_DAYS,
    num_paths=NUM_PATHS,
    correlated_randoms=None
):
    """
    Run Monte Carlo simulation for one stock.

    Drift includes: regime + mean reversion + enrichment modifiers.
    Volatility includes: GARCH × regime × macro × earnings vol spike.
    """

    dt = 1/252

    # Vol: GARCH × regime × macro × earnings
    adj_volatility = volatility * vol_mult * enrichment_vol_mult

    # Mean reversion toward anchor
    deviation = (current_price - mean_reversion_anchor) / mean_reversion_anchor if mean_reversion_anchor > 0 else 0
    mean_reversion_pull = -0.1 * deviation

    # Drift: regime + mean reversion + enrichment (RSI + sentiment + momentum + insider)
    drift = (drift_mult - 1.0 + mean_reversion_pull + enrichment_drift) * dt

    # Diffusion
    diffusion = adj_volatility * np.sqrt(dt)

    # Simulate paths
    paths = np.zeros((num_paths, days + 1))
    paths[:, 0] = current_price

    if correlated_randoms is not None:
        randoms = correlated_randoms
    else:
        randoms = np.random.normal(0, 1, size=(num_paths, days))

    for t in range(1, days + 1):
        paths[:, t] = paths[:, t-1] * np.exp(drift + diffusion * randoms[:, t-1])

    return paths[:, 1:]


def extract_statistics(paths, current_price):
    """
    Dip target = 60th percentile of path minimums.
    Confidence = fraction of paths hitting that level (~60%, informational).
    """

    minimums = paths.min(axis=1)
    percentile_low = np.percentile(minimums, PERCENTILE_TARGET)
    confidence = float(np.mean(minimums <= percentile_low))

    min_dates = np.argmin(paths, axis=1)
    median_date_index = int(np.median(min_dates))

    return {
        'percentile_low': percentile_low,
        'confidence': confidence,
        'median_date_index': median_date_index
    }


def simulate_portfolio(portfolio_data, corr_matrix, ticker_order, regime_info):
    """
    Run correlated simulations for all stocks with Phase 2 enrichment.
    """

    results = {}
    n_stocks = len(ticker_order)

    # Generate correlated random numbers
    correlated_randoms_all = np.zeros((NUM_PATHS, SIMULATION_DAYS, n_stocks))
    for day in range(SIMULATION_DAYS):
        correlated_randoms_all[:, day, :] = generate_correlated_random_numbers(corr_matrix, NUM_PATHS)

    for i, ticker in enumerate(ticker_order):
        data = portfolio_data[ticker]

        if data['current_price'] is None or data['historical'] is None:
            print(f"⚠️  Skipping {ticker} - missing data")
            continue

        # Regime adjustments
        stock_regime = regime_info['stock_regimes'].get(ticker, {'drift_mult': 1.0, 'vol_mult': 1.0})
        macro_adj = regime_info['macro_adjustments']

        combined_drift = stock_regime['drift_mult']
        combined_vol = stock_regime['vol_mult'] * macro_adj['vol_mult']

        # Mean reversion anchor
        price_targets = data.get('price_targets', {})
        if price_targets.get('targetMean'):
            anchor = price_targets['targetMean']
        else:
            anchor = data['historical']['Close'].tail(50).mean()

        # Phase 2: Enrichment modifiers
        enrichment = compute_enrichment_modifiers(data)

        # Log enrichment for debugging
        mods = enrichment['modifiers']
        mod_parts = []
        for k, v in mods.items():
            if abs(v) > 0.001:
                mod_parts.append(f"{k}={v:+.3f}")
        if mod_parts or enrichment['vol_multiplier'] != 1.0:
            vol_label = f"vol×{enrichment['vol_multiplier']:.1f}" if enrichment['vol_multiplier'] != 1.0 else ""
            drift_label = f"drift={enrichment['drift_adjustment']:+.4f}" if abs(enrichment['drift_adjustment']) > 0.001 else ""
            parts = [p for p in [drift_label, vol_label] + mod_parts if p]
            print(f"   🔧 {ticker}: {' | '.join(parts)}")

        # Extract correlated randoms
        stock_randoms = correlated_randoms_all[:, :, i]

        # GARCH volatility
        from garch_model import calculate_forward_volatility
        volatility = calculate_forward_volatility(data['historical'])

        # Run simulation with enrichment
        paths = run_monte_carlo_stock(
            current_price=data['current_price'],
            volatility=volatility,
            drift_mult=combined_drift,
            vol_mult=combined_vol,
            mean_reversion_anchor=anchor,
            enrichment_drift=enrichment['drift_adjustment'],
            enrichment_vol_mult=enrichment['vol_multiplier'],
            correlated_randoms=stock_randoms
        )

        stats = extract_statistics(paths, data['current_price'])

        results[ticker] = {
            'current_price': data['current_price'],
            'percentile_low': stats['percentile_low'],
            'confidence': stats['confidence'],
            'median_date_index': stats['median_date_index'],
            'paths': paths
        }

    return results
