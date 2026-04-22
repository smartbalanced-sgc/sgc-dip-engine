"""
Hidden Markov Model (HMM) for Regime Detection
Detects: Bull / Sideways / Drawdown regimes per stock
"""

import numpy as np
import pandas as pd

def detect_regime_simple(price_df, window=20):
    """
    Simplified regime detection using trend + volatility
    
    Returns: 'bull', 'sideways', or 'drawdown'
    
    Logic:
    - Bull: price > 20MA, low volatility
    - Drawdown: price << 20MA, high volatility
    - Sideways: else
    """
    
    current_price = price_df['Close'].iloc[-1]
    ma_20 = price_df['Close'].rolling(window).mean().iloc[-1]
    
    # Calculate recent volatility (20-day rolling std)
    volatility = price_df['Close'].pct_change().rolling(window).std().iloc[-1]
    
    # Price deviation from MA
    deviation = (current_price - ma_20) / ma_20
    
    # Regime classification
    if deviation > 0.02 and volatility < 0.02:  # 2% above MA, low vol
        return 'bull'
    elif deviation < -0.05:  # 5% below MA
        return 'drawdown'
    else:
        return 'sideways'

def get_regime_adjustments(regime):
    """
    Return drift and volatility multipliers based on regime
    
    Returns: dict with drift_mult, vol_mult
    """
    
    adjustments = {
        'bull': {'drift_mult': 1.1, 'vol_mult': 0.9},      # Higher drift, lower vol
        'sideways': {'drift_mult': 1.0, 'vol_mult': 1.0},  # Neutral
        'drawdown': {'drift_mult': 0.85, 'vol_mult': 1.3}  # Negative drift, higher vol
    }
    
    return adjustments.get(regime, {'drift_mult': 1.0, 'vol_mult': 1.0})
