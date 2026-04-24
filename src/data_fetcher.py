"""
Data Fetcher — SGC Dip Engine v7 (Session 3 Production)
- FMP API for 13 US stocks (15 endpoints per stock + 2 macro)
- Eulerpool API for LDO.MI (complete primary source — all 14/16 fields)
- ASML EUR conversion for display (user buys on Trading212 in EUR)

FMP stable API pattern: symbol in query params, NOT path
Endpoint: historical-price-eod/full (NOT historical-price-full)
Ref: rationale.md §2.1, §2.2, §3.1-§3.6

Session 3 Production:
- LDO.MI: Eulerpool complete (OHLC 230 days, beta, targets, estimates, grades, insider, AAQS)
- Corrected endpoint paths: /research/recommendations, /sentiment/price-metrics, /sentiment/insider-sentiment
- ASML: Fetch USD ADR, convert to EUR for display (1:1 ratio)
- FX rates: exchangerate-api.com (FMP doesn't support on Starter plan)
- yfinance REMOVED (unreliable on GitHub Actions, Eulerpool provides all needed fields)
"""

import requests
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from config import (
    PORTFOLIO, YFINANCE_TICKERS,
    FMP_API_KEY, FMP_BASE_URL, API_DELAY, API_TIMEOUT,
    LOOKBACK_DAYS, ANALYST_GRADE_MAX_AGE
)

try:
    from config import EULERPOOL_TOKEN
except ImportError:
    EULERPOOL_TOKEN = None


# =============================================================
# FX RATE FETCHING (Session 3)
# =============================================================

def fetch_fx_rate(base='EUR', target='USD'):
    """
    Fetch current FX rate from exchangerate-api.com
    Free tier: 1,500 requests/month (sufficient for daily runs)
    
    Returns: float (e.g., 1.17 for EUR/USD)
    """
    try:
        url = f"https://api.exchangerate-api.com/v4/latest/{base}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            rate = data['rates'].get(target, 1.0)
            return float(rate)
    except Exception as e:
        print(f"   ⚠️  FX rate fetch failed ({base}/{target}): {e}")
    
    return 1.0  # Fallback to 1:1 if fetch fails


# =============================================================
# FMP API WRAPPER
# Ref: rationale.md §2.2 — symbol in query params, NOT path
# =============================================================

def fmp_get(endpoint, symbol, extra_params=None):
    """FMP stable API call. Returns parsed JSON or None."""
    url = f"{FMP_BASE_URL}/{endpoint}"
    params = {"symbol": symbol, "apikey": FMP_API_KEY}
    if extra_params:
        params.update(extra_params)
    time.sleep(API_DELAY)
    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) == 0:
                return None
            if isinstance(data, dict) and data.get('Error Message'):
                return None
            return data
        else:
            print(f"      FMP {resp.status_code} for {endpoint}?symbol={symbol}")
            return None
    except Exception as e:
        print(f"      FMP error {endpoint}/{symbol}: {e}")
        return None


# =============================================================
# ORIGINAL 8 FMP ENDPOINTS (per stock)
# Ref: rationale.md §3.1 confirmed working endpoints
# =============================================================

def fetch_historical_fmp(ticker):
    """FMP historical-price-eod/full — 2yr daily OHLCV"""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    data = fmp_get("historical-price-eod/full", ticker, {"from": start_date, "to": end_date})
    if not data or not isinstance(data, list):
        print(f"   ⚠️  No historical data for {ticker}")
        return None
    df = pd.DataFrame(data)
    rename = {'date': 'Date', 'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}
    df = df.rename(columns=rename)
    required = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
    for col in required:
        if col not in df.columns:
            print(f"   ⚠️  Missing column {col} for {ticker}")
            return None
    df = df[required]
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.sort_values('Date').reset_index(drop=True)
    print(f"   ✅ {ticker}: {len(df)} days (FMP)")
    return df


def fetch_current_price_fmp(ticker, hist_df=None):
    """FMP quote — current price + MA50/MA200"""
    data = fmp_get("quote", ticker)
    if data and isinstance(data, list) and data[0].get('price'):
        return float(data[0]['price']), data[0]
    if hist_df is not None and not hist_df.empty:
        return float(hist_df['Close'].iloc[-1]), {}
    return None, {}


def fetch_price_targets_fmp(ticker):
    """FMP price-target-consensus"""
    data = fmp_get("price-target-consensus", ticker)
    if data and isinstance(data, list):
        t = data[0]
        return {
            'targetHigh': t.get('targetHigh'),
            'targetLow': t.get('targetLow'),
            'targetMean': t.get('targetConsensus'),
            'targetMedian': t.get('targetMedian')
        }
    return {}


def fetch_earnings_fmp(ticker):
    """FMP earnings — next earnings date"""
    data = fmp_get("earnings", ticker, {"limit": "5"})
    if data and isinstance(data, list):
        today = datetime.now().date()
        for rec in data:
            date_str = rec.get('date')
            if date_str:
                try:
                    d = datetime.strptime(date_str, '%Y-%m-%d').date()
                    if d >= today or rec.get('epsActual') is None:
                        return date_str
                except:
                    continue
    return None


def fetch_grades_fmp(ticker):
    """FMP grades — latest analyst action"""
    data = fmp_get("grades", ticker, {"limit": "3"})
    if data and isinstance(data, list):
        latest = data[0]
        date_str = latest.get('date', '')
        days_old = 999
        try:
            d = datetime.strptime(date_str, '%Y-%m-%d').date()
            days_old = (datetime.now().date() - d).days
        except:
            pass
        if days_old > ANALYST_GRADE_MAX_AGE:
            return {}
        return {
            'date': date_str,
            'gradingCompany': latest.get('gradingCompany'),
            'action': latest.get('action'),
            'newGrade': latest.get('newGrade'),
            'previousGrade': latest.get('previousGrade'),
            'toGrade': latest.get('newGrade'),  # Alias for consistency
            'priceTargetAction': latest.get('priceWhenPosted', ''),
            'days_old': days_old
        }
    return {}


def fetch_momentum_fmp(ticker):
    """FMP stock-price-change — 1M/3M/6M momentum"""
    data = fmp_get("stock-price-change", ticker)
    if data and isinstance(data, list):
        return {
            '1M': data[0].get('1M'),
            '3M': data[0].get('3M'),
            '6M': data[0].get('6M')
        }
    return {}


def fetch_forward_estimates_fmp(ticker):
    """FMP analyst-estimates — forward EPS"""
    data = fmp_get("analyst-estimates", ticker, {"period": "annual", "limit": "2"})
    if data and isinstance(data, list):
        return {
            'epsAvg': data[0].get('epsAvg'),
            'numAnalysts': data[0].get('numAnalystsEps')
        }
    return {}


def fetch_target_trend_fmp(ticker):
    """FMP price-target-summary — are analysts raising or lowering targets?"""
    data = fmp_get("price-target-summary", ticker)
    if data and isinstance(data, list):
        t = data[0]
        return {
            'lastMonthAvg': t.get('lastMonthAvgPriceTarget'),
            'lastQuarterAvg': t.get('lastQuarterAvgPriceTarget'),
            'lastYearAvg': t.get('lastYearAvgPriceTarget')
        }
    return {}


# =============================================================
# NEW 7 FMP ENDPOINTS (per stock) — confirmed on Starter plan
# =============================================================

def fetch_rsi_fmp(ticker):
    """FMP RSI(14) — overbought/oversold signal"""
    data = fmp_get("technical-indicators/rsi", ticker,
                   {"periodLength": "14", "timeframe": "1day"})
    if data and isinstance(data, list) and len(data) > 0:
        return float(data[0].get('rsi', 0))
    return None


def fetch_profile_fmp(ticker):
    """FMP profile — beta, sector, company name"""
    data = fmp_get("profile", ticker)
    if data and isinstance(data, list):
        return {
            'beta': data[0].get('beta'),
            'sector': data[0].get('sector'),
            'companyName': data[0].get('companyName')
        }
    return {}


def fetch_financial_scores_fmp(ticker):
    """FMP financial-scores — Altman Z-Score, Piotroski Score"""
    data = fmp_get("financial-scores", ticker)
    if data and isinstance(data, list):
        return {
            'altmanZScore': data[0].get('altmanZScore'),
            'piotroskiScore': data[0].get('piotroskiScore')
        }
    return {}


def fetch_grades_consensus_fmp(ticker):
    """FMP upgrades-downgrades-consensus — analyst sentiment distribution"""
    data = fmp_get("upgrades-downgrades-consensus", ticker)
    if data and isinstance(data, list):
        return {
            'strongBuy': data[0].get('strongBuy'),
            'buy': data[0].get('buy'),
            'hold': data[0].get('hold'),
            'sell': data[0].get('sell'),
            'strongSell': data[0].get('strongSell')
        }
    return {}


def fetch_dcf_fmp(ticker):
    """FMP discounted-cash-flow — DCF fair value estimate"""
    data = fmp_get("discounted-cash-flow", ticker)
    if data and isinstance(data, list):
        return data[0].get('dcf')
    return None


def fetch_insider_stats_fmp(ticker):
    """FMP insider-roaster-statistics — insider buying/selling pressure"""
    data = fmp_get("insider-roaster-statistics", ticker, {"page": "0"})
    if data and isinstance(data, list):
        return {
            'year': data[0].get('year'),
            'quarter': data[0].get('quarter'),
            'purchases': data[0].get('purchases'),
            'sales': data[0].get('sales')
        }
    return {}


# =============================================================
# MACRO CALENDAR (2 calls total, not per-stock)
# =============================================================

def fetch_economic_calendar():
    """FMP economic_calendar — FOMC, CPI, NFP, etc."""
    today = datetime.now().strftime("%Y-%m-%d")
    end_date = (datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d")
    url = f"{FMP_BASE_URL}/economic_calendar"
    params = {"from": today, "to": end_date, "apikey": FMP_API_KEY}
    time.sleep(API_DELAY)
    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                important = [e for e in data if e.get('impact') in ['High', 'Medium']]
                return important
    except Exception as e:
        print(f"   ⚠️  Economic calendar fetch failed: {e}")
    return []


def fetch_vix():
    """FMP ^VIX — market fear gauge"""
    data = fmp_get("quote", "^VIX")
    if data and isinstance(data, list):
        return float(data[0].get('price', 0))
    return None


# =============================================================
# EULERPOOL API WRAPPER (LDO.MI enrichment only)
# =============================================================

def eulerpool_get(endpoint, ticker):
    """Eulerpool API call for LDO.MI enrichment fields"""
    if not EULERPOOL_TOKEN:
        return None
    
    url = f"https://api.eulerpool.com/api/{endpoint}"
    headers = {"Authorization": f"Bearer {EULERPOOL_TOKEN}"}
    time.sleep(0.5)
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"      Eulerpool {resp.status_code} for {endpoint}")
            return None
    except Exception as e:
        print(f"      Eulerpool error {endpoint}: {e}")
        return None


def fetch_historical_eulerpool(ticker):
    """
    Eulerpool /equity/candles endpoint — 230 days of OHLC (no volume).
    Returns DataFrame compatible with FMP format.
    Validated endpoint: https://api.eulerpool.com/api/1/equity/candles/{ticker}?range=1y
    """
    if not EULERPOOL_TOKEN:
        return None
    
    url = f"https://api.eulerpool.com/api/1/equity/candles/{ticker}"
    params = {"range": "1y", "token": EULERPOOL_TOKEN}
    time.sleep(API_DELAY)
    
    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        if resp.status_code != 200:
            print(f"      Eulerpool candles {resp.status_code}")
            return None
        
        data = resp.json()
        if not isinstance(data, list) or len(data) < 50:
            print(f"      Eulerpool candles insufficient data: {len(data) if isinstance(data, list) else 0}")
            return None
        
        # Convert Eulerpool format to FMP-compatible DataFrame
        # Eulerpool returns: [{"timestamp": ms, "open": float, "high": float, "low": float, "close": float}, ...]
        rows = []
        for candle in data:
            timestamp_ms = candle.get('timestamp')
            if not timestamp_ms:
                continue
            
            date = datetime.fromtimestamp(timestamp_ms / 1000).strftime('%Y-%m-%d')
            rows.append({
                'Date': date,
                'Open': float(candle.get('open', 0)),
                'High': float(candle.get('high', 0)),
                'Low': float(candle.get('low', 0)),
                'Close': float(candle.get('close', 0)),
                'Volume': 0  # Eulerpool doesn't provide volume
            })
        
        if len(rows) < 50:
            print(f"      Eulerpool candles insufficient rows after parsing: {len(rows)}")
            return None
        
        df = pd.DataFrame(rows)
        df = df.sort_values('Date').reset_index(drop=True)
        
        print(f"   ✅ {ticker}: {len(df)} days (Eulerpool candles, no volume)")
        return df
        
    except Exception as e:
        print(f"      Eulerpool candles error: {e}")
        return None


def fetch_stock_data_eulerpool(ticker):
    """
    Fetch LDO.MI enrichment from Eulerpool with CORRECTED endpoint paths.
    All paths validated via terminal testing on 2026-04-24.
    
    Returns dict with enrichment fields or None if critical fields fail.
    """
    if not EULERPOOL_TOKEN:
        return None
    
    # 1. PRICE TARGETS — /equity/price-target/ (CORRECTED: singular, not plural!)
    targets_data = eulerpool_get(f"equity/price-target/{ticker}", ticker) or {}
    price_targets = {}
    if isinstance(targets_data, dict):
        price_targets = {
            'targetMean': targets_data.get('target_mean'),
            'targetHigh': targets_data.get('target_high'),
            'targetLow': targets_data.get('target_low'),
            'targetMedian': targets_data.get('target_median')
        }
    
    # 2. EARNINGS DATE PROXY — /equity/fundamentals-quarterly/ (CORRECTED endpoint)
    financials_data = eulerpool_get(f"equity/fundamentals-quarterly/{ticker}", ticker) or []
    earnings_date = None
    if isinstance(financials_data, list) and len(financials_data) > 0:
        # Find first future quarter
        for quarter in financials_data:
            period = quarter.get('period', '')
            if period.endswith('e'):  # 'e' suffix indicates estimate/future
                try:
                    # Parse quarter period (e.g., "2026-Q2e" → estimate Q2 end date)
                    year = quarter.get('year')
                    quarter_num = int(period.split('-Q')[1][0])
                    # Approximate quarter end dates
                    quarter_end_month = quarter_num * 3
                    earnings_date = f"{year}-{quarter_end_month:02d}-01"
                    break
                except:
                    pass
    
    # 3. FORWARD ESTIMATES — /equity/estimates/ (CORRECTED: removed /equity-extended/)
    estimates_data = eulerpool_get(f"equity/estimates/{ticker}", ticker) or []
    forward_estimates = {}
    if isinstance(estimates_data, list) and len(estimates_data) > 0:
        # Get latest annual estimate
        annual_est = [e for e in estimates_data if e.get('period') == 'FY']
        if annual_est:
            latest = annual_est[0]
            forward_estimates = {
                'epsAvg': latest.get('epsEstimate'),
                'numAnalysts': latest.get('epsAnalysts')
            }
    
    # 4. PROFILE — /equity/profile/ (sector, company name)
    profile_data = eulerpool_get(f"equity/profile/{ticker}", ticker) or {}
    
    # 4b. BETA — /sentiment/price-metrics/ (CORRECTED: beta is HERE, not in profile!)
    price_metrics = eulerpool_get(f"sentiment/price-metrics/{ticker}", ticker) or {}
    beta = price_metrics.get('beta')
    
    profile = {
        'beta': beta,  # From price-metrics, not profile
        'sector': profile_data.get('sector', 'Industrials'),
        'companyName': profile_data.get('name')
    }
    
    # 5. GRADES CONSENSUS — /research/recommendations/ (CORRECTED: /research/, not /equity-extended/)
    recs_data = eulerpool_get(f"research/recommendations/{ticker}", ticker) or []
    grades_consensus = {}
    target_trend = {}
    if isinstance(recs_data, list) and len(recs_data) > 0:
        # Latest month's ratings
        latest = recs_data[0]
        grades_consensus = {
            'strongBuy': latest.get('strongBuy', 0),
            'buy': latest.get('buy', 0),
            'hold': latest.get('hold', 0),
            'sell': latest.get('sell', 0),
            'strongSell': latest.get('strongSell', 0)
        }
        # Target trend: compare recent months
        if len(recs_data) >= 3:
            target_trend = {
                'lastMonthAvg': recs_data[0].get('targetMean'),
                'lastQuarterAvg': recs_data[2].get('targetMean')
            }
    
    # 6. FINANCIAL SCORES — /equity-extended/aaqs/ (path already correct)
    aaqs_data = eulerpool_get(f"equity-extended/aaqs/{ticker}", ticker) or {}
    financial_scores = {
        'aaqs': aaqs_data.get('score'),
        'altmanZScore': None,
        'piotroskiScore': None
    }
    
    # 7. INSIDER STATS — /sentiment/insider-sentiment/ (CORRECTED: /sentiment/, not /equity/)
    insider_data = eulerpool_get(f"sentiment/insider-sentiment/{ticker}", ticker) or []
    insider_stats = {}
    if isinstance(insider_data, list) and len(insider_data) > 0:
        recent = insider_data[0]
        insider_stats = {
            'year_month': f"{recent.get('year')}-{recent.get('month'):02d}",
            'change': recent.get('change'),
            'mspr': recent.get('mspr')
        }
    
    return {
        'price_targets': price_targets,
        'earnings_date': earnings_date,
        'forward_estimates': forward_estimates,
        'profile': profile,
        'grades_consensus': grades_consensus,
        'target_trend': target_trend,
        'financial_scores': financial_scores,
        'insider_stats': insider_stats
    }


# =============================================================
# HELPER FUNCTIONS (Momentum & RSI computed from historical data)
# =============================================================

def compute_momentum(hist_df):
    """Compute 1M/3M/6M momentum from historical data"""
    if hist_df is None or len(hist_df) < 127:
        return {}
    
    closes = hist_df['Close'].values
    latest = closes[-1]
    
    momentum = {}
    if len(closes) >= 22:
        momentum['1M'] = ((latest / closes[-22] - 1) * 100)
    if len(closes) >= 64:
        momentum['3M'] = ((latest / closes[-64] - 1) * 100)
    if len(closes) >= 127:
        momentum['6M'] = ((latest / closes[-127] - 1) * 100)
    
    return momentum


def compute_rsi(hist_df, period=14):
    """Compute RSI from historical data"""
    if hist_df is None or len(hist_df) < period + 5:
        return None
    
    closes = hist_df['Close']
    delta = closes.diff().dropna()
    gain = delta.where(delta > 0, 0)
    loss = (-delta).where(delta < 0, 0)
    
    avg_gain = gain.rolling(period).mean().iloc[-1]
    avg_loss = loss.rolling(period).mean().iloc[-1]
    
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    return None


# =============================================================
# STOCK ORCHESTRATORS
# =============================================================

def fetch_stock_data_fmp(ticker):
    """Fetch all data for one US stock from FMP (15 endpoints)"""
    print(f"   📊 {ticker} (FMP)...")
    hist = fetch_historical_fmp(ticker)
    price, quote_data = fetch_current_price_fmp(ticker, hist)

    # Session 3: ASML EUR conversion
    price_usd = price
    price_eur = None
    fx_rate = None
    
    if ticker == 'ASML' and price:
        fx_rate = fetch_fx_rate('EUR', 'USD')
        price_eur = price_usd / fx_rate
        print(f"   💱 {ticker}: ${price_usd:.2f} → €{price_eur:.2f} (EUR/USD {fx_rate:.4f})")

    return {
        'ticker': ticker,
        'historical': hist,
        'current_price': price,
        'quote_data': quote_data,
        # Original endpoints
        'price_targets': fetch_price_targets_fmp(ticker),
        'earnings_date': fetch_earnings_fmp(ticker),
        'analyst_grade': fetch_grades_fmp(ticker),
        'momentum': fetch_momentum_fmp(ticker),
        'forward_estimates': fetch_forward_estimates_fmp(ticker),
        'target_trend': fetch_target_trend_fmp(ticker),
        # New endpoints
        'rsi': fetch_rsi_fmp(ticker),
        'profile': fetch_profile_fmp(ticker),
        'financial_scores': fetch_financial_scores_fmp(ticker),
        'grades_consensus': fetch_grades_consensus_fmp(ticker),
        'dcf_value': fetch_dcf_fmp(ticker),
        'insider_stats': fetch_insider_stats_fmp(ticker),
        # Session 3: EUR conversion metadata
        '_price_usd': price_usd if ticker == 'ASML' else None,
        '_price_eur': price_eur if ticker == 'ASML' else None,
        '_fx_rate': fx_rate if ticker == 'ASML' else None
    }


def fetch_stock_data_ldomi(ticker):
    """
    Session 3 Production: Fetch LDO.MI using ONLY Eulerpool (complete data source).
    
    Eulerpool provides all 14/16 fields via corrected endpoints:
    - OHLC: 230 days from /equity/candles (no volume)
    - Enrichment: targets, estimates, grades, beta, insider, AAQS
    
    yfinance REMOVED (unreliable on GitHub Actions, Eulerpool is sufficient).
    """
    print(f"   📊 {ticker} (Eulerpool complete)...")
    
    # Get historical OHLC from Eulerpool candles
    hist = fetch_historical_eulerpool(ticker)
    if hist is None:
        print(f"   ❌ {ticker}: Eulerpool candles failed, skipping")
        return None
    
    current_price = float(hist['Close'].iloc[-1])
    
    # Get enrichment fields from Eulerpool
    eulerpool_bundle = fetch_stock_data_eulerpool(ticker)
    
    if eulerpool_bundle:
        price_targets = eulerpool_bundle.get('price_targets', {})
        earnings_date = eulerpool_bundle.get('earnings_date')
        forward_estimates = eulerpool_bundle.get('forward_estimates', {})
        profile = eulerpool_bundle.get('profile', {})
        grades_consensus = eulerpool_bundle.get('grades_consensus', {})
        target_trend = eulerpool_bundle.get('target_trend', {})
        financial_scores = eulerpool_bundle.get('financial_scores', {})
        insider_stats = eulerpool_bundle.get('insider_stats', {})
    else:
        # Eulerpool enrichment failed — use minimal defaults
        print(f"   ⚠️  {ticker}: Eulerpool enrichment unavailable, using candles only")
        price_targets = {}
        earnings_date = None
        forward_estimates = {}
        profile = {'beta': None, 'sector': 'Industrials', 'companyName': 'Leonardo S.p.A.'}
        grades_consensus = {}
        target_trend = {}
        financial_scores = {}
        insider_stats = {}
    
    # Compute momentum and RSI from Eulerpool historical
    momentum = compute_momentum(hist)
    rsi = compute_rsi(hist)
    
    return {
        'ticker': ticker,
        'historical': hist,
        'current_price': current_price,
        'quote_data': {},
        'price_targets': price_targets,
        'earnings_date': earnings_date,
        'forward_estimates': forward_estimates,
        'profile': profile,
        'grades_consensus': grades_consensus,
        'target_trend': target_trend,
        'momentum': momentum,
        'rsi': rsi,
        'financial_scores': financial_scores,
        'insider_stats': insider_stats,
        'analyst_grade': {},  # Eulerpool /equity/upgrades returns empty for LDO.MI
        'dcf_value': None,    # Eulerpool doesn't provide DCF
        '_no_volume': True    # Flag for validators (Eulerpool candles have Volume=0)
    }


# =============================================================
# PORTFOLIO ORCHESTRATOR
# =============================================================

def fetch_portfolio_data():
    """Fetch data for all 14 portfolio stocks + macro calendar"""
    portfolio_data = {}

    for ticker in PORTFOLIO.keys():
        if ticker in YFINANCE_TICKERS:
            # LDO.MI: Eulerpool complete (OHLC + enrichment)
            portfolio_data[ticker] = fetch_stock_data_ldomi(ticker)
        else:
            # US stocks: FMP (with ASML EUR conversion)
            portfolio_data[ticker] = fetch_stock_data_fmp(ticker)
    
    # Convert ASML analyst targets to EUR if available
    if 'ASML' in portfolio_data and portfolio_data['ASML']:
        asml = portfolio_data['ASML']
        if asml.get('_price_eur') and asml.get('price_targets'):
            fx_rate = asml['_fx_rate']
            targets = asml['price_targets']
            for key in ['targetMean', 'targetHigh', 'targetLow', 'targetMedian']:
                if targets.get(key):
                    targets[f'{key}_eur'] = targets[key] / fx_rate

    ok = sum(1 for d in portfolio_data.values() if d and d.get('current_price') is not None)
    print(f"\n   Data fetched: {ok}/{len(PORTFOLIO)} stocks with price data")

    # Fetch macro calendar (1 call, not per-stock)
    macro_events = fetch_economic_calendar()
    print(f"   📅 {len(macro_events)} macro events in next 60 days")

    return portfolio_data, macro_events
