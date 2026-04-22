"""
Macro Regime Detection
Uses VIX, 10Y yield, SPY trend to classify market environment
"""

import yfinance as yf
import numpy as np

def fetch_macro_indicators():
    """
    Fetch current VIX, 10Y yield, SPY trend
    Returns: dict with indicators
    """
    
    try:
        # VIX
        vix = yf.Ticker("^VIX")
        vix_current = vix.history(period="1d")['Close'].iloc[-1]
        
        # 10Y Treasury Yield
        tnx = yf.Ticker("^TNX")
        yield_10y = tnx.history(period="1d")['Close'].iloc[-1]
        
        # SPY 20-day trend
        spy = yf.Ticker("SPY")
        spy_hist = spy.history(period="1mo")
        spy_current = spy_hist['Close'].iloc[-1]
        spy_ma20 = spy_hist['Close'].rolling(20).mean().iloc[-1]
        spy_trend = (spy_current - spy_ma20) / spy_ma20
        
        return {
            'vix': vix_current,
            'yield_10y': yield_10y,
            'spy_trend': spy_trend
        }
        
    except Exception as e:
        print(f"⚠️  Error fetching macro indicators: {e}")
        # Return neutral defaults
        return {
            'vix': 18.0,
            'yield_10y': 4.3,
            'spy_trend': 0.0
        }

def classify_macro_regime(indicators):
    """
    Classify market regime based on indicators
    
    Returns: 'risk_on', 'neutral', or 'risk_off'
    """
    
    vix = indicators['vix']
    spy_trend = indicators['spy_trend']
    
    # Risk-off: VIX > 25 or SPY trend < -3%
    if vix > 25 or spy_trend < -0.03:
        return 'risk_off'
    
    # Risk-on: VIX < 15 and SPY trend > 2%
    elif vix < 15 and spy_trend > 0.02:
        return 'risk_on'
    
    else:
        return 'neutral'

def get_macro_adjustments(regime):
    """
    Return correlation and volatility adjustments based on macro regime
    
    Returns: dict with corr_mult, vol_mult
    """
    
    adjustments = {
        'risk_on': {'corr_mult': 0.8, 'vol_mult': 0.9},    # Lower correlation, lower vol
        'neutral': {'corr_mult': 1.0, 'vol_mult': 1.0},    # No adjustment
        'risk_off': {'corr_mult': 1.3, 'vol_mult': 1.4}    # Higher correlation (stocks move together), higher vol
    }
    
    return adjustments.get(regime, {'corr_mult': 1.0, 'vol_mult': 1.0})
