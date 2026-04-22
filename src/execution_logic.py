"""
Execution Logic & Signal Generation
Determines BUY/WAIT signals and generates one-liner explanations.

Key addition: Materiality threshold.
If expected dip < MIN_ACTIONABLE_DIP_PCT, signal BUY regardless.
Rationale: waiting for a 1.5% dip on a low-vol stock is noise, not signal.
Ref: config.py MIN_ACTIONABLE_DIP_PCT
"""

from config import BUY_THRESHOLD, MIN_ACTIONABLE_DIP_PCT
from datetime import datetime, timedelta


def generate_signal(current_price, percentile_low, confidence):
    """
    Determine if signal is BUY or WAIT.

    Logic (in order):
    1. If dip target >= current price → BUY (no dip expected)
    2. If dip is < MIN_ACTIONABLE_DIP_PCT → BUY (dip too shallow to wait for)
    3. If current price within 1% of target → BUY (already at the bottom)
    4. If confidence < BUY_THRESHOLD → BUY (dip unlikely)
    5. Else → WAIT

    Returns: ('BUY' or 'WAIT', reason_code)
    """

    # No dip expected — model thinks price only goes up
    if percentile_low >= current_price:
        return 'BUY', 'no_dip'

    # Materiality check: is the expected dip worth waiting for?
    dip_pct = (current_price - percentile_low) / current_price
    if dip_pct < MIN_ACTIONABLE_DIP_PCT:
        return 'BUY', 'immaterial'

    # Already at or below the target
    if current_price <= percentile_low * 1.01:
        return 'BUY', 'at_target'

    # Probability-based decision
    if confidence < BUY_THRESHOLD:
        return 'BUY', 'low_confidence'
    else:
        return 'WAIT', 'dip_likely'


def generate_one_liner(signal, confidence, current_price, target_price, reason_code):
    """
    Generate contextual one-liner explanation.
    Ref: rationale.md §1.5 one-liner rules
    """
    confidence_pct = int(confidence * 100)

    # Special cases from materiality / no-dip checks
    if reason_code == 'no_dip':
        return "No dip expected in 60-day window. Buy today."

    if reason_code == 'immaterial':
        dip_pct = (current_price - target_price) / current_price * 100
        return f"Expected dip only {dip_pct:.1f}% — not worth waiting. Buy today."

    if reason_code == 'at_target':
        return f"Price already at target level. Buy today."

    # Standard one-liners per rationale.md §1.5
    if signal == 'BUY':
        if confidence < 0.40:
            return f"Dip unlikely ({confidence_pct}% chance). Buy today."
        elif confidence < 0.60:
            return f"Dip possible ({confidence_pct}% chance). Today defensible. Buy."
        else:
            return f"Dip likely ({confidence_pct}% chance) but shallow. Buy now."
    else:  # WAIT
        if confidence >= 0.70:
            return f"Very strong dip signal ({confidence_pct}% chance). Hold firm. Wait."
        elif confidence >= 0.65:
            return f"Strong dip signal ({confidence_pct}% chance). Be patient. Wait 2-3 weeks."
        elif confidence >= 0.50:
            return f"Dip likely ({confidence_pct}% chance). Wait 1-2 weeks."
        else:
            return f"Weak dip signal ({confidence_pct}% chance). Consider buying soon."


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
    Returns: dict per ticker with signal, one_liner, date_range, reason_code
    """
    execution_data = {}

    for ticker, result in simulation_results.items():
        # Skip excluded stocks (from Gate 3)
        if result.get('_exclude'):
            continue

        current = result['current_price']
        target = result['percentile_low']
        confidence = result['confidence']
        median_date_index = result['median_date_index']

        signal, reason_code = generate_signal(current, target, confidence)
        one_liner = generate_one_liner(signal, confidence, current, target, reason_code)
        date_range = format_date_range(median_date_index)

        execution_data[ticker] = {
            'signal': signal,
            'current_price': current,
            'target_price': target,
            'confidence': confidence,
            'date_range': date_range,
            'one_liner': one_liner,
            'reason_code': reason_code,
            # Pass through flags from validation
            '_extreme_dip': result.get('_extreme_dip', False),
            '_no_dip': result.get('_no_dip', False),
        }

    return execution_data
