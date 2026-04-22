"""
Macro Regime Detection — FMP API for VIX and SPY
Ref: rationale.md §1.2 Layer 0
"""

from data_fetcher import fmp_get


def fetch_macro_indicators():
    """Fetch VIX and SPY trend from FMP quote endpoint"""
    vix_val = None
    spy_trend = 0.0

    # VIX quote
    data = fmp_get("quote", "^VIX")
    if data and isinstance(data, list) and data[0].get('price'):
        vix_val = float(data[0]['price'])

    # SPY quote — use priceAvg50 for trend
    data = fmp_get("quote", "SPY")
    if data and isinstance(data, list):
        price = data[0].get('price')
        ma50 = data[0].get('priceAvg50')
        if price and ma50 and ma50 > 0:
            spy_trend = (price - ma50) / ma50

    return {'vix': vix_val or 18.0, 'spy_trend': spy_trend}


def classify_macro_regime(indicators):
    """Classify: risk_on / neutral / risk_off"""
    vix = indicators['vix']
    spy_trend = indicators['spy_trend']
    if vix > 25 or spy_trend < -0.03:
        return 'risk_off'
    elif vix < 15 and spy_trend > 0.02:
        return 'risk_on'
    return 'neutral'


def get_macro_adjustments(regime):
    """Volatility and correlation multipliers by regime"""
    return {
        'risk_on':  {'corr_mult': 0.8, 'vol_mult': 0.9},
        'neutral':  {'corr_mult': 1.0, 'vol_mult': 1.0},
        'risk_off': {'corr_mult': 1.3, 'vol_mult': 1.4}
    }.get(regime, {'corr_mult': 1.0, 'vol_mult': 1.0})
