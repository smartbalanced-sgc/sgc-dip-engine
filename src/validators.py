"""
SGC Dip Engine v6 — Validation & Guardrail Layer

Four-gate validation pipeline that prevents garbage signals from
reaching the dashboard. Each gate validates at a pipeline stage.

Design principles:
  - NEVER clamp data and pretend it's real. Flag or degrade instead.
  - NaN is the silent killer. Check at every gate.
  - Log every warning so Jesse can see what the model distrusts.

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

    Actions:
      - Critical fail (no price, <50 rows) → mark stock as skip
      - Soft fail (stale, low volume) → warn but proceed
      - Data corruption (NaN, negative) → warn and flag
    """
    warnings = []
    today = datetime.now().date()

    for ticker, data in portfolio_data.items():
        prefix = f"[GATE1] {ticker}"

        # --- Critical: must have current price ---
        if data.get('current_price') is None:
            warnings.append(f"{prefix}: No current price — SKIPPED")
            data['_skip'] = True
            continue

        price = data['current_price']

        # --- Critical: price must be positive ---
        if price <= 0 or np.isnan(price):
            warnings.append(f"{prefix}: Invalid price {price} — SKIPPED")
            data['_skip'] = True
            continue

        # --- Historical data checks ---
        hist = data.get('historical')
        if hist is None or (hasattr(hist, 'empty') and hist.empty):
            warnings.append(f"{prefix}: No historical data — SKIPPED")
            data['_skip'] = True
            continue

        row_count = len(hist)
        if row_count < HIST_MIN_ROWS_SKIP:
            warnings.append(f"{prefix}: Only {row_count} rows (need {HIST_MIN_ROWS_SKIP}) — SKIPPED")
            data['_skip'] = True
            continue

        if row_count < HIST_MIN_ROWS_WARN:
            warnings.append(f"{prefix}: Only {row_count} rows (want {HIST_MIN_ROWS_WARN}) — reduced GARCH accuracy")

        # --- Freshness check ---
        last_date = pd.to_datetime(hist['Date'].iloc[-1]).date()
        days_stale = (today - last_date).days
        if days_stale > HIST_MAX_STALE_DAYS:
            warnings.append(f"{prefix}: Data is {days_stale} days stale (last: {last_date})")

        # --- NaN check in historical ---
        nan_count = hist[['Open', 'High', 'Low', 'Close', 'Volume']].isna().sum().sum()
        if nan_count > 0:
            warnings.append(f"{prefix}: {nan_count} NaN values in historical data")

        # --- Negative/zero price check ---
        if (hist['Close'] <= 0).any():
            bad_count = (hist['Close'] <= 0).sum()
            warnings.append(f"{prefix}: {bad_count} non-positive Close prices in history")

        # --- Returns outlier scan ---
        returns = hist['Close'].pct_change().dropna()
        outliers = returns[returns.abs() > RETURN_OUTLIER_PCT]
        if len(outliers) > 0:
            worst = outliers.abs().max()
            warnings.append(f"{prefix}: {len(outliers)} daily returns >{RETURN_OUTLIER_PCT*100:.0f}% (max {worst*100:.1f}%) — possible split/data error")
            data['_has_return_outliers'] = True

        # --- Price cross-check: quote vs last historical close ---
        last_close = float(hist['Close'].iloc[-1])
        if last_close > 0:
            divergence = abs(price - last_close) / last_close
            if divergence > PRICE_CROSSCHECK_MAX_PCT:
                warnings.append(f"{prefix}: Quote ${price:.2f} vs last close ${last_close:.2f} ({divergence*100:.1f}% divergence)")

        # --- Volume check ---
        mean_vol = hist['Volume'].mean()
        if mean_vol < VOLUME_MIN_DAILY:
            warnings.append(f"{prefix}: Mean daily volume {mean_vol:,.0f} below {VOLUME_MIN_DAILY:,}")

        # --- Analyst target bounds ---
        targets = data.get('price_targets', {})
        target_mean = targets.get('targetMean')
        if target_mean and target_mean > 0:
            ratio = target_mean / price
            if ratio < ANCHOR_MIN_RATIO or ratio > ANCHOR_MAX_RATIO:
                warnings.append(f"{prefix}: Analyst target ${target_mean:.2f} is {ratio:.1f}x price — suspect, will use fallback anchor")
                data['_anchor_suspect'] = True

        # --- DCF bounds ---
        dcf = data.get('dcf_value')
        if dcf and dcf > 0:
            dcf_ratio = dcf / price
            if dcf_ratio < ANCHOR_MIN_RATIO or dcf_ratio > ANCHOR_MAX_RATIO:
                warnings.append(f"{prefix}: DCF ${dcf:.2f} is {dcf_ratio:.1f}x price — suspect")
                data['_dcf_suspect'] = True

        # Mark as valid
        data['_skip'] = False

    skipped = sum(1 for d in portfolio_data.values() if d.get('_skip'))
    valid = len(portfolio_data) - skipped
    if skipped > 0:
        warnings.append(f"[GATE1] {skipped} stocks skipped, {valid} valid")

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
    prefix = f"[GATE2] {ticker}"

    # NaN check
    if volatility is None or np.isnan(volatility):
        warnings.append(f"{prefix}: GARCH returned NaN — UNMODELABLE")
        return None, False, warnings

    # Negative vol (should be impossible but defensive)
    if volatility <= 0:
        warnings.append(f"{prefix}: GARCH vol {volatility:.4f} <= 0 — UNMODELABLE")
        return None, False, warnings

    # Unmodelable threshold
    if volatility > VOL_UNMODELABLE_PCT:
        warnings.append(f"{prefix}: Vol {volatility*100:.1f}% exceeds {VOL_UNMODELABLE_PCT*100:.0f}% — UNMODELABLE")
        return volatility, False, warnings

    # Stationarity check
    if garch_params:
        alpha = garch_params.get('alpha', 0)
        beta = garch_params.get('beta', 0)
        persistence = alpha + beta
        if persistence > GARCH_STATIONARITY_WARN:
            warnings.append(f"{prefix}: GARCH persistence {persistence:.3f} near unit root — vol forecast unreliable")

    return volatility, True, warnings


def validate_anchor(ticker, anchor, current_price, source_name="unknown"):
    """
    Validate mean reversion anchor.
    Returns: (validated_anchor, warnings_list)
    """
    warnings = []
    prefix = f"[GATE2] {ticker}"

    if anchor is None or anchor <= 0 or np.isnan(anchor):
        warnings.append(f"{prefix}: Anchor ({source_name}) invalid — using current price (no mean reversion)")
        return current_price, warnings

    ratio = anchor / current_price
    if ratio < ANCHOR_MIN_RATIO or ratio > ANCHOR_MAX_RATIO:
        warnings.append(f"{prefix}: Anchor ${anchor:.2f} ({source_name}) is {ratio:.1f}x price — falling back to current price")
        return current_price, warnings

    return anchor, warnings


def validate_correlation_matrix(corr_matrix, ticker_order):
    """
    Validate correlation matrix before Cholesky decomposition.
    Returns: (matrix, warnings_list)
    """
    warnings = []

    # Check for NaN
    if np.any(np.isnan(corr_matrix)):
        warnings.append("[GATE2] Correlation matrix contains NaN — using identity matrix")
        return np.eye(len(ticker_order)), warnings

    # Check diagonal
    diag = np.diag(corr_matrix)
    if not np.allclose(diag, 1.0):
        warnings.append("[GATE2] Correlation diagonal != 1.0 — fixing")
        np.fill_diagonal(corr_matrix, 1.0)

    # Check bounds
    if np.any(corr_matrix > 1.0) or np.any(corr_matrix < -1.0):
        warnings.append("[GATE2] Correlation values outside [-1, 1] — clipping")
        corr_matrix = np.clip(corr_matrix, -1.0, 1.0)

    # Check for near-perfect correlation (possible data error)
    offdiag = corr_matrix[~np.eye(len(ticker_order), dtype=bool)]
    max_corr = np.max(np.abs(offdiag))
    if max_corr > CORR_MAX_OFFDIAG:
        warnings.append(f"[GATE2] Max off-diagonal correlation {max_corr:.3f} > {CORR_MAX_OFFDIAG} — possible data issue")

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
        prefix = f"[GATE3] {ticker}"
        current = result.get('current_price')
        target = result.get('percentile_low')
        confidence = result.get('confidence')

        # NaN checks
        if any(v is None or (isinstance(v, float) and np.isnan(v))
               for v in [current, target, confidence]):
            warnings.append(f"{prefix}: NaN in simulation output — EXCLUDED")
            result['_exclude'] = True
            continue

        # Confidence bounds
        if confidence < 0.0 or confidence > 1.0:
            warnings.append(f"{prefix}: Confidence {confidence:.3f} outside [0,1] — clamping")
            result['confidence'] = max(0.0, min(1.0, confidence))

        # Dip target above current price = "no dip expected"
        if target >= current:
            result['_no_dip'] = True

        # Extreme dip flag (don't clamp — just flag)
        if current > 0 and target > 0:
            dip_pct = (current - target) / current
            if dip_pct > DIP_EXTREME_FLAG_PCT:
                warnings.append(f"{prefix}: Predicted {dip_pct*100:.1f}% dip — flagged as extreme")
                result['_extreme_dip'] = True

        # Median date sanity
        date_idx = result.get('median_date_index', 0)
        if date_idx < 0 or date_idx > SIMULATION_DAYS:
            warnings.append(f"{prefix}: Median date index {date_idx} outside [0, {SIMULATION_DAYS}]")
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
        warnings.append("[GATE4] No valid signals — dashboard will show error state")
        return execution_data, warnings

    valid_count = len(execution_data)

    # Minimum stock count
    if valid_count < MIN_VALID_STOCKS:
        warnings.append(f"[GATE4] Only {valid_count}/{len(execution_data)} stocks with signals — degraded dashboard")

    # VIX sanity
    vix = macro_indicators.get('vix', 0)
    if vix < VIX_FLOOR:
        warnings.append(f"[GATE4] VIX {vix:.1f} below {VIX_FLOOR} — data may be stale or erroneous")
    elif vix > VIX_CEILING:
        warnings.append(f"[GATE4] VIX {vix:.1f} above {VIX_CEILING} — extreme market stress")

    # All-same-signal check
    signals = [d['signal'] for d in execution_data.values()]
    buy_count = signals.count('BUY')
    wait_count = signals.count('WAIT')

    if buy_count == valid_count:
        warnings.append(f"[GATE4] All {valid_count} stocks show BUY — verify macro data")
    elif wait_count == valid_count:
        warnings.append(f"[GATE4] All {valid_count} stocks show WAIT — verify macro data")

    # Average confidence check (informational, not actionable)
    confidences = [d['confidence'] for d in execution_data.values()]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0
    if avg_conf > 0.80:
        warnings.append(f"[GATE4] Average confidence {avg_conf*100:.0f}% — model is very confident, verify inputs")

    return execution_data, warnings
