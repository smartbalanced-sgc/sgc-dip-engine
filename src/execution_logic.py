"""
Execution Logic & Signal Generation
Determines BUY/WAIT signals and generates one-liner explanations
"""

from config import BUY_THRESHOLD
from datetime import datetime, timedelta

def generate_signal(current_price, percentile_low, confidence):
    """
    Determine if signal is BUY or WAIT
    
    Logic: If current price is likely the bottom (based on confidence), BUY. Else WAIT.
    
    Returns: 'BUY' or 'WAIT'
    """
    
    # If current price is at or below the likely low → BUY
    if current_price <= percentile_low * 1.01:  # Within 1% of target
        return 'BUY'
    
    # If confidence in dip is low → BUY (dip unlikely)
    # If confidence in dip is high → WAIT (dip likely)
    if confidence < BUY_THRESHOLD:
        return 'BUY'
    else:
        return 'WAIT'

def generate_one_liner(signal, confidence, current_price, target_price):
    """
    Generate contextual one-liner explanation
    
    Returns: string (one-liner)
    """
    
    confidence_pct = int(confidence * 100)
    
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
    """
    Convert simulation day index to calendar date range
    
    Args:
        median_date_index: day index (0-59)
        days_window: +/- days around median
    
    Returns: string like "May 6-12"
    """
    
    start_date = datetime.now() + timedelta(days=median_date_index - days_window//2)
    end_date = datetime.now() + timedelta(days=median_date_index + days_window//2)
    
    # Format as "May 6-12" or "Apr 28-May 2" if crosses month
    if start_date.month == end_date.month:
        return f"{start_date.strftime('%b')} {start_date.day}-{end_date.day}"
    else:
        return f"{start_date.strftime('%b %d')}-{end_date.strftime('%b %d')}"

def process_execution_signals(simulation_results):
    """
    Generate all signals and one-liners for portfolio
    
    Returns: dict per ticker with signal, one_liner, date_range
    """
    
    execution_data = {}
    
    for ticker, result in simulation_results.items():
        current = result['current_price']
        target = result['percentile_low']
        confidence = result['confidence']
        median_date_index = result['median_date_index']
        
        signal = generate_signal(current, target, confidence)
        one_liner = generate_one_liner(signal, confidence, current, target)
        date_range = format_date_range(median_date_index)
        
        execution_data[ticker] = {
            'signal': signal,
            'current_price': current,
            'target_price': target,
            'confidence': confidence,
            'date_range': date_range,
            'one_liner': one_liner
        }
    
    return execution_data
