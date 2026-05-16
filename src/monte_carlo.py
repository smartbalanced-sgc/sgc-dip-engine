"""
Correlated Monte Carlo Simulation Engine
10,000 correlated price paths × 60 days per stock

PHASE 2 ENRICHMENT (23 Apr 2026):
  Five data streams now feed into the MC simulation:
    1. RSI → drift modifier (overbought = dip more likely)
    2. Sentiment → drift modifier (Claude API score)
    3. Momentum → drift modifier (contrarian: strong up = pullback likely)
    4. Insider stats → drift modifier (heavy selling = bearish)
    5. Earnings date → vol multiplier (imminent earnings = vol spike)

  Each modifier is small individually (±0.01 to ±0.05).
  Combined, they differentiate stocks meaningfully.
  Total enrichment drift capped at ±0.10 to prevent extreme combined effects.

SESSION 2 ENHANCEMENTS:
  - Time-varying volatility: vol spikes on earnings/macro days instead of uniform
  - build_volatility_schedule() creates per-day vol array
  - MC loop uses daily vol when schedule is enabled
  - CRITICAL: When schedule is enabled, enrichment vol multiplier is excluded
    from base_vol because the schedule REPLACES uniform earnings vol, not stacks.

CONVICTION MODEL:
  Dip target = 60th percentile of path minimums.
  Signal driven by dip depth vs 3% materiality threshold.
"""

import numpy as np
import pandas as pd
from datetime import datetime
from config import NUM_PATHS, SIMULATION_DAYS, PERCENTILE_TARGET, RALLY_CONVICTION_PERCENTILE
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
    # Session 5: Sentiment no longer modifies drift (negligible impact ±0.82%)
    # AI now modifies vol_regime instead (see below)
    # Drift slot kept at 0 for backwards compatibility
    drift_mods['sentiment'] = 0.0

    # --- AI vol_regime modifier (Session 5) ---
    # Post-earnings AI output: HIGH/MEDIUM/LOW → vol multiplier
    # This ACTUALLY changes dip depth (5-15%) unlike drift adjustment
    ai_result = stock_data.get('ai_result', {})
    if isinstance(ai_result, dict):
        ai_vol_regime = ai_result.get('vol_regime', '').upper()
        if ai_vol_regime == 'LOW':
            vol_mult *= 0.75  # Post-earnings beat: vol collapses, shallower dips
        elif ai_vol_regime == 'HIGH':
            vol_mult *= 1.30  # Post-earnings miss: vol elevated, deeper dips
        # MEDIUM = no change (default)

    # --- Analyst spread → uncertainty proxy (Session 5, free) ---
    # Wide analyst disagreement = high uncertainty = wider distribution
    from sentiment import compute_analyst_spread
    price_targets = stock_data.get('price_targets', {})
    analyst_spread = compute_analyst_spread(price_targets)
    if analyst_spread is not None and analyst_spread > 0.30:
        vol_mult *= 1.10  # High disagreement: widen distribution

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
    # Used ONLY when time-varying vol schedule is DISABLED (Phase 2 fallback).
    # When schedule is enabled, this multiplier is ignored — schedule handles it.
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
# TIME-VARYING VOLATILITY SCHEDULE (Session 2)
# Ref: Build Spec §Session 2, rationale.md §Earnings Vol Spike
# Concentrates vol spike on actual catalyst day instead of uniform
# =============================================================

def build_volatility_schedule(base_vol, earnings_date=None, macro_events=None, days=SIMULATION_DAYS):
    """
    Build per-day volatility array. Spikes on earnings/macro days,
    normal elsewhere.

    Returns: np.array of shape (days,) with daily vol values
    """
    from config_loader import get_config

    schedule = np.ones(days)  # §Vol Schedule: default 1.0x multiplier
    today = datetime.now().date()

    # §Vol Schedule: Earnings spike — Build Spec §Session 2
    if earnings_date:
        try:
            if isinstance(earnings_date, str):
                ed = datetime.strptime(earnings_date, '%Y-%m-%d').date()
            else:
                ed = earnings_date
            day_index = (ed - today).days
            window = get_config('volatility_schedule', 'earnings_pre_post_window', default=2)
            day_mult = get_config('volatility_schedule', 'earnings_day_multiplier', default=3.0)
            pre_post_mult = get_config('volatility_schedule', 'earnings_pre_post_multiplier', default=1.5)

            if 0 <= day_index < days:
                schedule[day_index] = day_mult
                for offset in range(1, window + 1):
                    if day_index - offset >= 0:
                        schedule[day_index - offset] = max(schedule[day_index - offset], pre_post_mult)
                    if day_index + offset < days:
                        schedule[day_index + offset] = max(schedule[day_index + offset], pre_post_mult)
        except (ValueError, TypeError):
            pass

    # §Vol Schedule: Macro event spikes — Build Spec §Session 2
    if macro_events:
        macro_mult = get_config('volatility_schedule', 'macro_event_multiplier', default=2.0)
        for event in macro_events:
            event_date_str = event.get('date', '')
            try:
                event_date = datetime.strptime(event_date_str[:10], '%Y-%m-%d').date()
                day_index = (event_date - today).days
                if 0 <= day_index < days:
                    schedule[day_index] = max(schedule[day_index], macro_mult)
            except (ValueError, TypeError):
                pass

    return base_vol * schedule


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
    vol_schedule=None,
    days=SIMULATION_DAYS,
    num_paths=NUM_PATHS,
    correlated_randoms=None
):
    """
    Run Monte Carlo simulation for one stock.

    Drift includes: regime + mean reversion + enrichment modifiers.
    Volatility: if vol_schedule provided, uses per-day vol array (Session 2).
    Otherwise falls back to uniform vol (Phase 2 behavior).
    """

    dt = 1/252

    # Mean reversion toward anchor
    deviation = (current_price - mean_reversion_anchor) / mean_reversion_anchor if mean_reversion_anchor > 0 else 0
    mean_reversion_pull = -0.1 * deviation

    # Drift: regime + mean reversion + enrichment (RSI + sentiment + momentum + insider)
    drift = (drift_mult - 1.0 + mean_reversion_pull + enrichment_drift) * dt

    # Simulate paths
    paths = np.zeros((num_paths, days + 1))
    paths[:, 0] = current_price

    if correlated_randoms is not None:
        randoms = correlated_randoms
    else:
        randoms = np.random.normal(0, 1, size=(num_paths, days))

    if vol_schedule is not None:
        # §Session 2: Time-varying vol — per-day diffusion from schedule
        for t in range(1, days + 1):
            diffusion_t = vol_schedule[t - 1] * np.sqrt(dt)
            paths[:, t] = paths[:, t-1] * np.exp(drift + diffusion_t * randoms[:, t-1])
    else:
        # Phase 2 fallback: uniform vol
        adj_volatility = volatility * vol_mult * enrichment_vol_mult
        diffusion = adj_volatility * np.sqrt(dt)
        for t in range(1, days + 1):
            paths[:, t] = paths[:, t-1] * np.exp(drift + diffusion * randoms[:, t-1])

    return paths[:, 1:]


def extract_statistics(paths, current_price):
    """
    Extract primary and fallback dip targets + rally targets from MC paths.
    
    Primary: PERCENTILE_TARGET percentile (deeper dip, moderate conviction)
    Fallback: 80th percentile — shallower dip, higher conviction
    Rally: 40th percentile of maximums (60% conviction rally target)
    """
    from config import PERCENTILE_TARGET
    from config_loader import get_config
    
    minimums = paths.min(axis=1)
    min_dates = np.argmin(paths, axis=1)
    
    # Primary target (PERCENTILE_TARGET percentile)
    percentile_low = np.percentile(minimums, PERCENTILE_TARGET)
    confidence = float(np.mean(minimums <= percentile_low))
    median_date_index = int(np.median(min_dates))
    
    # Fallback target (80th percentile)
    fallback_pct = get_config('signal', 'fallback_percentile', default=80)
    fallback_low = np.percentile(minimums, fallback_pct)
    fallback_confidence = float(np.mean(minimums <= fallback_low))
    
    # Fallback timing: median day when paths hit fallback price
    fallback_hits = np.argmax(paths <= fallback_low, axis=1)
    valid_hits = fallback_hits[fallback_hits > 0]
    fallback_date_index = int(np.median(valid_hits)) if len(valid_hits) > 0 else median_date_index
    
    # --- Rally statistics (Session 5, config-driven in Session 6) ---
    # rally_conviction_percentile from config.yaml (default 60)
    # 60% conviction = 40th percentile of maximums (60% of paths reach this high or higher)
    # Formula: percentile_input = 100 - rally_conviction_percentile
    maximums = paths.max(axis=1)
    max_dates = np.argmax(paths, axis=1)
    
    rally_pct_input = 100 - RALLY_CONVICTION_PERCENTILE
    rally_target = np.percentile(maximums, rally_pct_input)
    
    # Conservative rally (rally_conviction_percentile + 10 for conservative view)
    rally_conservative_pct_input = max(0, 100 - (RALLY_CONVICTION_PERCENTILE + 10))
    rally_conservative = np.percentile(maximums, rally_conservative_pct_input)
    
    # Rally timing: median day when paths first hit rally target
    rally_hits = np.argmax(paths >= rally_target, axis=1)
    valid_rally_hits = rally_hits[rally_hits > 0]
    rally_date_index = int(np.median(valid_rally_hits)) if len(valid_rally_hits) > 0 else int(np.median(max_dates))
    
    # Terminal price (where stock ends at Day 60)
    terminal_prices = paths[:, -1]
    terminal_median = float(np.median(terminal_prices))

    # §May 14 (rebuilt 2026-05-15) — daily bands as MEDIAN PATH + ZONES
    # Previous interpretations (per-day percentile, cumulative running min/max)
    # both produced confusing values for users. This rebuild shows the simplest,
    # most intuitive view:
    #   - "median_price" = where the typical Monte Carlo path is each day
    #     (np.median across paths at each day). Naturally fluctuates.
    #   - "zone" = 'rally' / 'dip' / '' based on whether the day falls within
    #     ±7 days of the headline rally/dip date indices. Renders as visually
    #     highlighted rows in the dashboard, marking when the predicted dip
    #     and rally are most likely to occur.
    # Headline dip/rally targets and date indices are computed above and are
    # unchanged. This block only adds presentation data for the dashboard.
    median_per_day = np.median(paths, axis=0)
    ZONE_HALF_WIDTH = 7  # ±7 trading days around each median event date
    n_days = len(median_per_day)
    dip_zone_start = max(0, int(median_date_index) - ZONE_HALF_WIDTH)
    dip_zone_end = min(n_days - 1, int(median_date_index) + ZONE_HALF_WIDTH)
    rally_zone_start = max(0, int(rally_date_index) - ZONE_HALF_WIDTH)
    rally_zone_end = min(n_days - 1, int(rally_date_index) + ZONE_HALF_WIDTH)

    daily_bands = []
    for i in range(n_days):
        zone = ''
        if rally_zone_start <= i <= rally_zone_end:
            zone = 'rally'
        if dip_zone_start <= i <= dip_zone_end:
            zone = 'dip'  # dip takes precedence if overlap (rare)
        daily_bands.append({
            'day': i + 1,
            'median_price': float(median_per_day[i]),
            'zone': zone,
        })

    return {
        'percentile_low': percentile_low,
        'confidence': confidence,
        'median_date_index': median_date_index,
        'fallback_low': fallback_low,
        'fallback_confidence': fallback_confidence,
        'fallback_date_index': fallback_date_index,
        # Rally stats (Session 5, config-driven Session 6)
        'rally_60': rally_target,           # Primary rally target (conviction from config)
        'rally_70': rally_conservative,     # Conservative rally target
        'rally_date_index': rally_date_index,
        'terminal_median': terminal_median,
        'daily_bands': daily_bands,  # §May 14 daily probability bands feature
    }


def simulate_portfolio(portfolio_data, corr_matrix, ticker_order, regime_info, macro_events=None):
    """
    Run correlated simulations for all stocks with Phase 2 enrichment
    and Session 2 time-varying volatility.
    """
    from config_loader import get_config

    results = {}
    n_stocks = len(ticker_order)
    use_vol_schedule = get_config('volatility_schedule', 'enabled', default=False)

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

        # §Session 2: Build time-varying vol schedule if enabled
        # CRITICAL: When schedule is enabled, do NOT include enrichment vol_multiplier
        # in base_vol. The schedule REPLACES the uniform earnings multiplier.
        # Including both would double-count: base×1.5 (uniform) × 3.0 (schedule) = 4.5×
        # Correct: base × 3.0 (schedule only) on earnings day, base × 1.0 elsewhere
        vol_schedule = None
        if use_vol_schedule:
            base_vol = volatility * combined_vol  # No enrichment vol — schedule handles it
            vol_schedule = build_volatility_schedule(
                base_vol=base_vol,
                earnings_date=data.get('earnings_date'),
                macro_events=macro_events,
                days=SIMULATION_DAYS
            )

        # Run simulation
        paths = run_monte_carlo_stock(
            current_price=data['current_price'],
            volatility=volatility,
            drift_mult=combined_drift,
            vol_mult=combined_vol,
            mean_reversion_anchor=anchor,
            enrichment_drift=enrichment['drift_adjustment'],
            enrichment_vol_mult=enrichment['vol_multiplier'],
            vol_schedule=vol_schedule,
            correlated_randoms=stock_randoms
        )

        stats = extract_statistics(paths, data['current_price'])

        results[ticker] = {
            'current_price': data['current_price'],
            'percentile_low': stats['percentile_low'],
            'confidence': stats['confidence'],
            'median_date_index': stats['median_date_index'],
            'fallback_low': stats['fallback_low'],
            'fallback_confidence': stats['fallback_confidence'],
            'fallback_date_index': stats['fallback_date_index'],
            # Rally stats (Session 5)
            'rally_60': stats['rally_60'],
            'rally_70': stats['rally_70'],
            'rally_date_index': stats['rally_date_index'],
            'terminal_median': stats['terminal_median'],
            'daily_bands': stats['daily_bands'],  # §May 14 daily probability bands feature — propagate to dashboard consumer
            'paths': paths
        }

    return results
