"""
SGC Dip Engine v7 — Validation & Guardrail Layer

Four-gate validation pipeline that prevents garbage signals from
reaching the dashboard. Each gate validates at a pipeline stage.

Design principles:
  - NEVER clamp data and pretend it's real. Flag or degrade instead.
  - NaN is the silent killer. Check at every gate.
  - Log every warning so Jesse can see what the model distrusts.
  - Warnings use plain "caveman" language: what happened, why, what we did.

Gate 1: Input data quality (after fetch, before models)
Gate 2: Model output sanity (after GARCH/HMM, before MC)
Gate 3: Simulation output sanity (after MC, before signals)
Gate 4: Portfolio-level coherence (after signals, before dashboard)
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from config import (
    HIST_MIN_ROWS_WARN, HIST_MIN_ROWS_SKIP, HIST_MAX_STALE_DAYS,
    PRICE_CROSSCHECK_MAX_PCT, RETURN_OUTLIER_PCT,
    ANCHOR_MIN_RATIO, ANCHOR_MAX_RATIO, VOLUME_MIN_DAILY,
    VOL_UNMODELABLE_PCT, GARCH_STATIONARITY_WARN, CORR_MAX_OFFDIAG,
    DIP_EXTREME_FLAG_PCT, VIX_FLOOR, VIX_CEILING,
    MIN_VALID_STOCKS, SIMULATION_DAYS
)


# =============================================================
# GATE 1: INPUT DATA VALIDATION
# Runs after data_fetcher, before any model processing.
# =============================================================

def validate_input_data(portfolio_data):
    """
    Validate all fetched data for quality issues.
    Returns: (cleaned_data, warnings_list)
    """
    warnings = []
    today = datetime.now().date()

    for ticker, data in portfolio_data.items():

        # --- Critical: must have current price ---
        if data.get('current_price') is None:
            warnings.append(f"{ticker}: No current price — SKIPPED")
            data['_skip'] = True
            continue

        price = data['current_price']

        # --- Critical: price must be positive ---
        if price <= 0 or np.isnan(price):
            warnings.append(f"{ticker}: Invalid price {price} — SKIPPED")
            data['_skip'] = True
            continue

        # --- Historical data checks ---
        hist = data.get('historical')
        if hist is None or (hasattr(hist, 'empty') and hist.empty):
            warnings.append(f"{ticker}: No historical data — SKIPPED")
            data['_skip'] = True
            continue

        row_count = len(hist)
        if row_count < HIST_MIN_ROWS_SKIP:
            warnings.append(f"{ticker}: Only {row_count} rows (need {HIST_MIN_ROWS_SKIP}) — SKIPPED")
            data['_skip'] = True
            continue

        if row_count < HIST_MIN_ROWS_WARN:
            warnings.append(f"{ticker}: {row_count} days of history (want {HIST_MIN_ROWS_WARN}). GARCH less precise but usable.")

        # --- Freshness check ---
        last_date = pd.to_datetime(hist['Date'].iloc[-1]).date()
        days_stale = (today - last_date).days
        if days_stale > HIST_MAX_STALE_DAYS:
            warnings.append(f"{ticker}: Data {days_stale} days old (last: {last_date}). Signal may lag recent moves.")

        # --- NaN check in historical ---
        nan_count = hist[['Open', 'High', 'Low', 'Close', 'Volume']].isna().sum().sum()
        if nan_count > 0:
            warnings.append(f"{ticker}: {nan_count} missing values in price data. May reduce accuracy.")

        # --- Negative/zero price check ---
        if (hist['Close'] <= 0).any():
            bad_count = (hist['Close'] <= 0).sum()
            warnings.append(f"{ticker}: {bad_count} zero/negative prices. Possible data corruption.")

        # --- Returns outlier scan ---
        returns = hist['Close'].pct_change().dropna()
        outliers = returns[returns.abs() > RETURN_OUTLIER_PCT]
        if len(outliers) > 0:
            worst = outliers.abs().max()
            if len(outliers) == 1:
                warnings.append(f"{ticker}: {worst*100:.0f}% single-day move. Real event OR data corrupted. Included in volatility.")
            else:
                warnings.append(f"{ticker}: {len(outliers)} moves over {RETURN_OUTLIER_PCT*100:.0f}% (max {worst*100:.0f}%). Real volatility OR data corrupted. Check quality.")
            data['_has_return_outliers'] = True

      # Add context for Power sector stocks
      if ticker in ['CEG', 'VST'] and data.get('_has_return_outliers'):
          warnings.append(f"{ticker}: Power sector has binary catalysts (PPA wins, restarts, regulation). >20% moves normal for role.")

        # --- Price cross-check: quote vs last historical close ---
        last_close = float(hist['Close'].iloc[-1])
        if last_close > 0:
            divergence = abs(price - last_close) / last_close
            if divergence > PRICE_CROSSCHECK_MAX_PCT:
                warnings.append(f"{ticker}: Quote ${price:.2f} vs last close ${last_close:.2f} ({divergence*100:.1f}% gap). Possible stale data.")

        # --- Volume check ---
        mean_vol = hist['Volume'].mean()
        if mean_vol < VOLUME_MIN_DAILY:
            warnings.append(f"{ticker}: Low volume ({mean_vol:,.0f}/day vs {VOLUME_MIN_DAILY:,} min). Prices may be less reliable.")

        # --- Analyst target bounds ---
        targets = data.get('price_targets', {})
        target_mean = targets.get('targetMean')
        if target_mean and target_mean > 0:
            ratio = target_mean / price
            if ratio < ANCHOR_MIN_RATIO or ratio > ANCHOR_MAX_RATIO:
                warnings.append(f"{ticker}: Analyst target ${target_mean:.2f} is {ratio:.1f}x price. Suspect — using fallback anchor.")
                data['_anchor_suspect'] = True

        # --- DCF bounds (caveman version) ---
        dcf = data.get('dcf_value')
        if dcf and dcf > 0:
            dcf_ratio = dcf / price
            if dcf_ratio < ANCHOR_MIN_RATIO or dcf_ratio > ANCHOR_MAX_RATIO:
          warnings.append(f"{ticker}: Model ${dcf:.0f} vs market ${price:.0f}. Model broken OR stock overvalued. Using analyst targets.")
          data['_dcf_suspect'] = True
          # Add context note once
          if not any("Growth stocks" in w for w in warnings):
            warnings.append("Growth stocks often show DCF warnings (AI premium vs traditional model). System uses analyst targets instead.")

        # Mark as valid
        data['_skip'] = False

    skipped = sum(1 for d in portfolio_data.values() if d.get('_skip'))
    valid = len(portfolio_data) - skipped
    if skipped > 0:
        warnings.append(f"{skipped} stock(s) skipped, {valid} valid")

    return portfolio_data, warnings


# =============================================================
# GATE 2: MODEL OUTPUT VALIDATION
# Runs after GARCH/HMM, before Monte Carlo.
# =============================================================

def validate_volatility(ticker, volatility, garch_params=None):
    """
    Validate GARCH volatility output for a single stock.
    Returns: (validated_vol, is_modelable, warnings_list)
    """
    warnings = []

    if volatility is None or np.isnan(volatility):
        warnings.append(f"{ticker}: GARCH failed. No vol estimate — excluded.")
        return None, False, warnings

    if volatility <= 0:
        warnings.append(f"{ticker}: GARCH returned {volatility:.4f}. Invalid — excluded.")
        return None, False, warnings

    if volatility > VOL_UNMODELABLE_PCT:
        warnings.append(f"{ticker}: Vol {volatility*100:.0f}% (>{VOL_UNMODELABLE_PCT*100:.0f}% limit). Too wild to model — excluded.")
        return volatility, False, warnings

    if garch_params:
        alpha = garch_params.get('alpha', 0)
        beta = garch_params.get('beta', 0)
        persistence = alpha + beta
        if persistence > GARCH_STATIONARITY_WARN:
            warnings.append(f"{ticker}: GARCH unstable (persistence {persistence:.3f}). Dip forecast may overshoot.")

    return volatility, True, warnings


def validate_anchor(ticker, anchor, current_price, source_name="unknown"):
    """
    Validate mean reversion anchor.
    Returns: (validated_anchor, warnings_list)
    """
    warnings = []

    if anchor is None or anchor <= 0 or np.isnan(anchor):
        warnings.append(f"{ticker}: No anchor ({source_name}). Mean reversion disabled.")
        return current_price, warnings

    ratio = anchor / current_price
    if ratio < ANCHOR_MIN_RATIO or ratio > ANCHOR_MAX_RATIO:
        warnings.append(f"{ticker}: Anchor ${anchor:.2f} ({source_name}) is {ratio:.1f}x price. Too extreme — using current price instead.")
        return current_price, warnings

    return anchor, warnings


def validate_correlation_matrix(corr_matrix, ticker_order):
    """
    Validate correlation matrix before Cholesky decomposition.
    Returns: (matrix, warnings_list)
    """
    warnings = []

    if np.any(np.isnan(corr_matrix)):
        warnings.append("Correlation matrix has NaN. Stocks simulated independently (no cross-correlation).")
        return np.eye(len(ticker_order)), warnings

    diag = np.diag(corr_matrix)
    if not np.allclose(diag, 1.0):
        np.fill_diagonal(corr_matrix, 1.0)

    if np.any(corr_matrix > 1.0) or np.any(corr_matrix < -1.0):
        corr_matrix = np.clip(corr_matrix, -1.0, 1.0)

    offdiag = corr_matrix[~np.eye(len(ticker_order), dtype=bool)]
    max_corr = np.max(np.abs(offdiag))
    if max_corr > CORR_MAX_OFFDIAG:
        warnings.append(f"Near-perfect correlation ({max_corr:.2f}) between two stocks. Possible data issue.")

    return corr_matrix, warnings


# =============================================================
# GATE 3: SIMULATION OUTPUT VALIDATION
# Runs after Monte Carlo, before signal generation.
# =============================================================

def validate_simulation_results(simulation_results):
    """
    Validate MC simulation outputs per stock.
    Returns: (cleaned_results, warnings_list)
    """
    warnings = []

    for ticker, result in simulation_results.items():
        current = result.get('current_price')
        target = result.get('percentile_low')
        confidence = result.get('confidence')

        if any(v is None or (isinstance(v, float) and np.isnan(v))
               for v in [current, target, confidence]):
            warnings.append(f"{ticker}: Simulation produced garbage. Excluded.")
            result['_exclude'] = True
            continue

        if confidence < 0.0 or confidence > 1.0:
            result['confidence'] = max(0.0, min(1.0, confidence))

        if target >= current:
            result['_no_dip'] = True

        if current > 0 and target > 0:
            dip_pct = (current - target) / current
            if dip_pct > DIP_EXTREME_FLAG_PCT:
                warnings.append(f"{ticker}: {dip_pct*100:.1f}% dip predicted. Unusually deep — treat with caution.")
                result['_extreme_dip'] = True

        date_idx = result.get('median_date_index', 0)
        if date_idx < 0 or date_idx > SIMULATION_DAYS:
            result['median_date_index'] = max(0, min(SIMULATION_DAYS, date_idx))

        result['_exclude'] = False

    return simulation_results, warnings


# =============================================================
# GATE 4: PORTFOLIO-LEVEL SIGNAL VALIDATION
# Runs after signal generation, before dashboard.
# =============================================================

def validate_signals_portfolio(execution_data, macro_indicators):
    """
    Cross-stock and macro-level sanity checks.
    Returns: (execution_data, warnings_list)
    """
    warnings = []

    if not execution_data:
        warnings.append("No valid signals. Dashboard will show error state.")
        return execution_data, warnings

    valid_count = len(execution_data)

    if valid_count < MIN_VALID_STOCKS:
        warnings.append(f"Only {valid_count} stocks have signals (need {MIN_VALID_STOCKS}). Dashboard may be incomplete.")

    vix = macro_indicators.get('vix', 0)
    if vix < VIX_FLOOR:
        warnings.append(f"VIX {vix:.1f} below {VIX_FLOOR}. Data may be stale or erroneous.")
    elif vix > VIX_CEILING:
        warnings.append(f"VIX {vix:.1f} above {VIX_CEILING}. Extreme stress — model may underestimate crash risk.")

    signals = [d['signal'] for d in execution_data.values()]
    buy_count = signals.count('BUY')
    wait_count = signals.count('WAIT')

    if buy_count == valid_count:
        warnings.append(f"All {valid_count} stocks show BUY. Unusual — verify macro data is current.")
    elif wait_count == valid_count:
        warnings.append(f"All {valid_count} stocks show WAIT. Pre-earnings cluster OR model miscalibrated. May normalize after earnings pass.")

    confidences = [d['confidence'] for d in execution_data.values()]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0
    if avg_conf > 0.80:
        warnings.append(f"{avg_conf*100:.0f}% confidence across all stocks. Too high. Check inputs.")

    return execution_data, warnings
