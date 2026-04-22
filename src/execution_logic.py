"""
Execution Logic & Signal Generation

Signal is driven by DIP DEPTH vs materiality threshold, NOT by confidence.
Confidence is ~60% for all stocks by construction (conviction dial setting).
Differentiation comes from how DEEP each stock's dip is at that conviction.

Decision flow:
  1. Model outputs a dip target per stock (60th percentile of path minimums)
  2. Calculate dip % = (current - target) / current
  3. If dip < 3% → BUY (dip too small to matter at Jesse's position sizes)
  4. If dip >= 3% → WAIT (meaningful dip worth waiting for)

One-liners describe the dip depth, not a probability number.
"""

from config import MIN_ACTIONABLE_DIP_PCT, PERCENTILE_TARGET
from datetime import datetime, timedelta


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


def format_date_range(median_date_index, days_window=7):
    """Convert simulation day index to calendar date range."""
    start_date = datetime.now() + timedelta(days=median_date_index - days_window // 2)
    end_date = datetime.now() + timedelta(days=median_date_index + days_window // 2)
    if start_date.month == end_date.month:
        return f"{start_date.strftime('%b')} {start_date.day}-{end_date.day}"
    else:
        return f"{start_date.strftime('%b %d')}-{end_date.strftime('%b %d')}"


def process_execution_signals(simulation_results):
    """
    Generate all signals and one-liners for portfolio.
    Returns: dict per ticker with signal, one_liner, date_range, dip_pct
    """
    execution_data = {}

    for ticker, result in simulation_results.items():
        if result.get('_exclude'):
            continue

        current = result['current_price']
        target = result['percentile_low']
        confidence = result['confidence']
        median_date_index = result['median_date_index']

        signal, reason_code, dip_pct = generate_signal(current, target)
        one_liner = generate_one_liner(signal, dip_pct, reason_code)
        date_range = format_date_range(median_date_index)

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
        }

    return execution_data
