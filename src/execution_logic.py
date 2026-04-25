"""
Execution Logic & Signal Generation

Signal is driven by DIP DEPTH vs materiality threshold, NOT by confidence.
Confidence is ~60% for all stocks by construction (conviction dial setting).
Differentiation comes from how DEEP each stock's dip is at that conviction.

Decision flow:
  1. Model outputs a dip target per stock (60th percentile of path minimums)
  2. Calculate dip % = (current - target) / current
  3. If dip < 3% → BUY (dip too small to matter at position sizes)
  4. If dip >= 3% → WAIT (meaningful dip worth waiting for)

SESSION 2 ENHANCEMENTS:
  - Post-earnings anchor suppression: suppress_stale_anchor()
  - Catalyst-aware date formatting: format_date_range() shows earnings/macro dates
  - process_execution_signals() accepts portfolio_data and macro_events

One-liners describe the dip depth, not a probability number.
"""

from config import MIN_ACTIONABLE_DIP_PCT, PERCENTILE_TARGET
from config_loader import get_config
from datetime import datetime, timedelta


# =============================================================
# POST-EARNINGS ANCHOR SUPPRESSION (Session 2)
# Ref: Build Spec §Session 2 — prevents false signals when
# analyst targets are stale after earnings gaps
# =============================================================

def suppress_stale_anchor(stock_data):
    """
    Check if stock recently had earnings + large gap.
    If so, analyst targets are stale — suppress mean reversion.

    Returns: (suppressed: bool, reason: str)
    """
    # §Anchor Suppression: check if enabled — config.yaml
    if not get_config('anchor_suppression', 'enabled', default=False):
        return False, ''

    earnings_date = stock_data.get('earnings_date')
    if not earnings_date:
        return False, ''

    try:
        if isinstance(earnings_date, str):
            ed = datetime.strptime(earnings_date, '%Y-%m-%d').date()
        else:
            ed = earnings_date

        days_since = (datetime.now().date() - ed).days

        # §Anchor Suppression: lookback_days threshold — config.yaml
        lookback = get_config('anchor_suppression', 'lookback_days', default=5)
        if days_since < 0 or days_since > lookback:
            return False, ''

        # §Anchor Suppression: check for large single-day return
        hist = stock_data.get('historical')
        if hist is not None and len(hist) >= 5:
            recent_returns = hist['Close'].pct_change().tail(lookback).abs()
            threshold = get_config('anchor_suppression', 'return_threshold', default=0.05)
            if recent_returns.max() > threshold:
                return True, f"Post-earnings gap detected ({recent_returns.max():.1%} move within {days_since}d of earnings)"

    except (ValueError, TypeError):
        pass

    return False, ''


def generate_signal(current_price, percentile_low):
    """
    Determine BUY or WAIT based on dip depth vs materiality threshold.

    Returns: ('BUY' or 'WAIT', reason_code, dip_pct)
    """

    # No dip expected — target at or above current price
    if percentile_low >= current_price:
        return 'BUY', 'no_dip', 0.0

    dip_pct = (current_price - percentile_low) / current_price

    # Already at or below the target
    if current_price <= percentile_low * 1.01:
        return 'BUY', 'at_target', dip_pct

    # Materiality check: is the dip worth waiting for?
    if dip_pct < MIN_ACTIONABLE_DIP_PCT:
        return 'BUY', 'immaterial', dip_pct

    # Dip is meaningful — WAIT
    return 'WAIT', 'dip_likely', dip_pct


def generate_one_liner(signal, dip_pct, reason_code):
    """
    Generate contextual one-liner based on dip depth.
    Conviction is fixed at PERCENTILE_TARGET%, so one-liners
    describe the dip size rather than a probability.
    """
    dip_display = f"{dip_pct*100:.1f}%"
    conviction = PERCENTILE_TARGET

    if reason_code == 'no_dip':
        return "No meaningful dip expected in 60-day window. Buy today."

    if reason_code == 'at_target':
        return "Price already at dip target. Buy today."

    if reason_code == 'immaterial':
        return f"Expected dip only {dip_display} — not worth waiting. Buy today."

    # WAIT signals — vary by dip depth
    if dip_pct >= 0.10:
        return f"Deep {dip_display} dip expected ({conviction}% conviction). Hold firm. Wait."
    elif dip_pct >= 0.05:
        return f"Strong {dip_display} dip expected ({conviction}% conviction). Be patient."
    else:
        return f"Moderate {dip_display} dip expected ({conviction}% conviction). Worth waiting."


def format_date_range(median_date_index, days_window=7, earnings_date=None, macro_events=None):
    """
    Convert simulation day index to calendar date range.
    If dip timing aligns with a catalyst, show that instead of generic range.
    Ref: Build Spec §Session 2 — Honest date buckets with catalyst awareness
    """
    today = datetime.now()

    # §Session 2: Check if dip aligns with earnings date
    if earnings_date:
        try:
            if isinstance(earnings_date, str):
                ed = datetime.strptime(earnings_date, '%Y-%m-%d').date()
            else:
                ed = earnings_date
            earnings_day_index = (ed - today.date()).days
            if abs(median_date_index - earnings_day_index) <= 3:
                return f"likely around earnings ({ed.strftime('%b %d')})"
        except (ValueError, TypeError):
            pass

    # §Session 2: Check if dip aligns with macro event
    if macro_events:
        for event in macro_events:
            try:
                event_date = datetime.strptime(event.get('date', '')[:10], '%Y-%m-%d').date()
                event_day_index = (event_date - today.date()).days
                if abs(median_date_index - event_day_index) <= 2:
                    event_name = event.get('event', 'macro event')
                    return f"likely around {event_name} ({event_date.strftime('%b %d')})"
            except (ValueError, TypeError):
                pass

    # Fallback: show actual calendar date ranges
    # Calculate start and end dates for the time window
    dip_date = today + timedelta(days=median_date_index)
    window_start = dip_date - timedelta(days=days_window)
    window_end = dip_date + timedelta(days=days_window)
    
    # Format: "May 9-23" (single month) or "May 30-Jun 6" (spans months)
    if window_start.month == window_end.month:
        return f"{window_start.strftime('%b %d')}-{window_end.strftime('%d')}"
    else:
        return f"{window_start.strftime('%b %d')}-{window_end.strftime('%b %d')}"


def process_execution_signals(simulation_results, portfolio_data=None, macro_events=None):
    """
    Generate all signals and one-liners for portfolio.
    Returns: dict per ticker with signal, one_liner, date_range, dip_pct

    Session 2: accepts portfolio_data for anchor suppression,
    macro_events for catalyst-aware date formatting.
    """
    execution_data = {}

    for ticker, result in simulation_results.items():
        if result.get('_exclude'):
            continue

        current = result['current_price']
        target = result['percentile_low']
        confidence = result['confidence']
        median_date_index = result['median_date_index']

        # §Session 2: Check for stale anchor (post-earnings suppression)
        stock_data = portfolio_data.get(ticker, {}) if portfolio_data else {}
        anchor_suppressed, suppress_reason = suppress_stale_anchor(stock_data)

        signal, reason_code, dip_pct = generate_signal(current, target)
        one_liner = generate_one_liner(signal, dip_pct, reason_code)

      # Session 3: Generate fallback signal when primary is WAIT
        fallback = None
        if signal == 'WAIT':
            fb_signal, fb_reason, fb_dip = generate_signal(current, result['fallback_low'])
            
            # Only show fallback if actionable (BUY or meaningful WAIT)
            if fb_signal == 'BUY' or fb_dip >= MIN_ACTIONABLE_DIP_PCT:
                fallback = {
                    'price': result['fallback_low'],
                    'dip_pct': fb_dip,
                    'confidence': result['fallback_confidence'],
                    'signal': fb_signal,
                    'date_range': format_date_range(
                        result['fallback_date_index'],
                        earnings_date=stock_data.get('earnings_date'),
                        macro_events=macro_events
                    )
                }

        # §Session 2: Pass earnings/macro for catalyst-aware date formatting
        earnings_date = stock_data.get('earnings_date')
        date_range = format_date_range(
            median_date_index,
            earnings_date=earnings_date,
            macro_events=macro_events
        )

        execution_data[ticker] = {
            'signal': signal,
            'current_price': current,
            'target_price': target,
            'confidence': confidence,
            'dip_pct': dip_pct,
            'date_range': date_range,
            'one_liner': one_liner,
            'reason_code': reason_code,
            '_extreme_dip': result.get('_extreme_dip', False),
            '_no_dip': result.get('_no_dip', False),
            '_anchor_suppressed': anchor_suppressed,
            '_suppress_reason': suppress_reason,
            'fallback': fallback,  # Session 3: NEW FIELD
        }

    return execution_data
