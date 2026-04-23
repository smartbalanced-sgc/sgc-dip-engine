"""
Backtest Framework — SGC Dip Engine v6

Compares historical WAIT signals against actual price outcomes.
Did stocks that were predicted to dip actually dip to their targets?

Requires:
  - data/signal_history.csv (accumulated from daily runs)
  - FMP historical prices (to check actual outcomes)

Output:
  - Hit rate (% of WAIT signals where target was reached)
  - Average error (predicted dip vs actual dip)
  - ROI vs naive (did waiting beat buying on signal date?)
"""

import csv
import os
from datetime import datetime, timedelta
from pathlib import Path
from config_loader import get_config

MIN_HISTORY_DAYS = get_config('backtest', 'min_history_days', default=14)
TARGET_HIT_RATE_MIN = get_config('backtest', 'target_hit_rate_min', default=0.55)
TARGET_HIT_RATE_MAX = get_config('backtest', 'target_hit_rate_max', default=0.65)
ROLLING_WINDOW = get_config('backtest', 'rolling_window_days', default=30)
SIMULATION_DAYS = get_config('monte_carlo', 'simulation_days', default=60)


def load_signal_history():
    """Load signal_history.csv and return list of dicts."""
    repo_root = Path(__file__).parent.parent
    csv_path = repo_root / 'data' / 'signal_history.csv'

    if not csv_path.exists():
        return []

    signals = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            signals.append(row)

    return signals


def get_actual_price(ticker, target_date, portfolio_data=None):
    """
    Get the actual lowest price for ticker between signal date and target_date.

    Uses portfolio_data historical if available (no extra API calls).
    Returns: (actual_low, actual_close_on_signal_date) or (None, None)
    """
    if portfolio_data is None:
        return None, None

    stock_data = portfolio_data.get(ticker, {})
    historical = stock_data.get('historical')

    if historical is None or len(historical) == 0:
        return None, None

    try:
        # Filter to date range
        hist = historical.copy()
        hist['Date'] = hist['Date'].apply(
            lambda x: x.date() if hasattr(x, 'date') else x
        )

        signal_date = datetime.strptime(target_date, '%Y-%m-%d').date() if isinstance(target_date, str) else target_date

        # Look for prices after signal date, within simulation window
        window_end = signal_date + timedelta(days=SIMULATION_DAYS)
        mask = (hist['Date'] >= signal_date) & (hist['Date'] <= window_end)
        window_data = hist[mask]

        if len(window_data) == 0:
            return None, None

        actual_low = float(window_data['Low'].min())
        actual_close_on_signal = float(window_data['Close'].iloc[0])

        return actual_low, actual_close_on_signal

    except Exception:
        return None, None


def run_backtest(portfolio_data=None):
    """
    Run backtest on accumulated signal history.

    Returns dict with:
      - has_data: bool (enough history to backtest?)
      - days_of_data: int
      - total_signals: int
      - wait_signals: int
      - targets_hit: int
      - hit_rate: float
      - avg_error: float (predicted dip% - actual dip%)
      - roi_vs_naive: float (% improvement from waiting)
      - calibration: str ('well_calibrated', 'overconfident', 'underconfident')
      - by_ticker: dict of per-ticker stats
    """
    signals = load_signal_history()

    if len(signals) == 0:
        return {'has_data': False, 'reason': 'No signal history found'}

    # Filter to WAIT signals only (BUY signals aren't testable)
    wait_signals = [s for s in signals if s.get('signal') == 'WAIT']

    if len(wait_signals) == 0:
        return {'has_data': False, 'reason': 'No WAIT signals in history'}

    # Check if we have enough days of data
    dates = sorted(set(s.get('date', '') for s in signals))
    days_of_data = len(dates)

    if days_of_data < MIN_HISTORY_DAYS:
        return {
            'has_data': False,
            'reason': f'Need {MIN_HISTORY_DAYS} days of history, have {days_of_data}',
            'days_of_data': days_of_data,
            'days_needed': MIN_HISTORY_DAYS
        }

    # Only backtest signals old enough to have outcomes
    # (signal must be at least 14 days old to check if target was hit)
    today = datetime.now().date()
    testable_cutoff = today - timedelta(days=14)

    testable = []
    for s in wait_signals:
        try:
            signal_date = datetime.strptime(s['date'], '%Y-%m-%d').date()
            if signal_date <= testable_cutoff:
                testable.append(s)
        except:
            continue

    if len(testable) == 0:
        return {
            'has_data': False,
            'reason': f'No signals old enough to test (need 14+ days). Have {days_of_data} days of data.',
            'days_of_data': days_of_data
        }

    # Run backtest on testable signals
    hits = 0
    misses = 0
    errors = []
    roi_improvements = []
    by_ticker = {}

    for s in testable:
        ticker = s.get('ticker', '')
        try:
            signal_date = s['date']
            predicted_dip_pct = float(s.get('dip_pct', 0))
            dip_target = float(s.get('dip_target', 0))
            signal_price = float(s.get('current_price', 0))
        except (ValueError, TypeError):
            continue

        if signal_price <= 0 or dip_target <= 0:
            continue

        # Get actual price data
        actual_low, actual_close = get_actual_price(ticker, signal_date, portfolio_data)

        if actual_low is None:
            continue

        # Did the actual low reach the target?
        actual_dip_pct = (signal_price - actual_low) / signal_price
        target_hit = actual_low <= dip_target * 1.02  # Within 2% counts as hit

        if target_hit:
            hits += 1
        else:
            misses += 1

        # Error: predicted dip vs actual dip
        error = predicted_dip_pct - actual_dip_pct
        errors.append(error)

        # ROI: did waiting improve entry?
        if actual_low < signal_price:
            roi_improvement = (signal_price - actual_low) / signal_price
            roi_improvements.append(roi_improvement)
        else:
            roi_improvements.append(0.0)

        # Per-ticker tracking
        if ticker not in by_ticker:
            by_ticker[ticker] = {'hits': 0, 'misses': 0, 'signals': 0}
        by_ticker[ticker]['signals'] += 1
        if target_hit:
            by_ticker[ticker]['hits'] += 1
        else:
            by_ticker[ticker]['misses'] += 1

    total_tested = hits + misses
    if total_tested == 0:
        return {
            'has_data': False,
            'reason': 'Could not match signals to price data',
            'days_of_data': days_of_data
        }

    hit_rate = hits / total_tested
    avg_error = sum(errors) / len(errors) if errors else 0
    avg_roi = sum(roi_improvements) / len(roi_improvements) if roi_improvements else 0

    # Calibration assessment
    if hit_rate < TARGET_HIT_RATE_MIN:
        calibration = 'overconfident'
    elif hit_rate > TARGET_HIT_RATE_MAX:
        calibration = 'underconfident'
    else:
        calibration = 'well_calibrated'

    # Per-ticker hit rates
    for ticker, stats in by_ticker.items():
        if stats['signals'] > 0:
            stats['hit_rate'] = stats['hits'] / stats['signals']

    return {
        'has_data': True,
        'days_of_data': days_of_data,
        'total_signals': len(wait_signals),
        'testable_signals': total_tested,
        'targets_hit': hits,
        'targets_missed': misses,
        'hit_rate': hit_rate,
        'avg_error': avg_error,
        'avg_roi_vs_naive': avg_roi,
        'calibration': calibration,
        'by_ticker': by_ticker,
    }
