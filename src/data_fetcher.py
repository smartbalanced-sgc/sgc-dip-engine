"""
Data Fetcher — SGC Dip Engine v6
- FMP API for 13 US stocks (15 endpoints per stock + 2 macro)
- yfinance for LDO.MI only (FMP returns 402 for European tickers)

FMP stable API pattern: symbol in query params, NOT path
Endpoint: historical-price-eod/full (NOT historical-price-full)
Ref: rationale.md §2.1, §2.2, §3.1-§3.6
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
    """FMP profile — beta, sector"""
    data = fmp_get("profile", ticker)
    if data and isinstance(data, list):
        return {
            'beta': data[0].get('beta'),
            'sector': data[0].get('sector')
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
    """FMP grades-consensus — buy/hold/sell counts"""
    data = fmp_get("grades-consensus", ticker)
    if data and isinstance(data, list):
        d = data[0]
        return {
            'strongBuy': d.get('strongBuy', 0),
            'buy': d.get('buy', 0),
            'hold': d.get('hold', 0),
            'sell': d.get('sell', 0),
            'strongSell': d.get('strongSell', 0),
            'consensus': d.get('consensus')
        }
    return {}


def fetch_dcf_fmp(ticker):
    """FMP discounted-cash-flow — intrinsic value"""
    data = fmp_get("discounted-cash-flow", ticker)
    if data and isinstance(data, list):
        return float(data[0].get('dcf', 0))
    return None


def fetch_insider_stats_fmp(ticker):
    """FMP insider-trading/statistics — net buy/sell activity"""
    data = fmp_get("insider-trading/statistics", ticker)
    if data and isinstance(data, list) and len(data) > 0:
        d = data[0]
        return {
            'totalPurchases': d.get('totalPurchases', 0),
            'totalSales': d.get('totalSales', 0),
            'acquiredDisposedRatio': d.get('acquiredDisposedRatio', 0)
        }
    return {}


# =============================================================
# FMP: MACRO ENDPOINT (1 call total, not per-stock)
# =============================================================

def fetch_economic_calendar():
    """FMP economic-calendar — Fed/CPI/jobs dates in next 60 days"""
    today = datetime.now().strftime("%Y-%m-%d")
    future = (datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d")
    url = f"{FMP_BASE_URL}/economic-calendar"
    params = {"from": today, "to": future, "apikey": FMP_API_KEY}
    time.sleep(API_DELAY)
    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        if resp.status_code == 200:
            events = resp.json()
            keywords = ['interest rate', 'fomc', 'fed', 'cpi', 'nonfarm',
                        'unemployment', 'gdp', 'pce']
            macro_events = []
            for e in events:
                event_name = (e.get('event', '') or '').lower()
                if any(kw in event_name for kw in keywords):
                    macro_events.append({
                        'date': e.get('date', ''),
                        'event': e.get('event', '')
                    })
            return macro_events
        return []
    except:
        return []


# =============================================================
# STOCK ORCHESTRATORS
# =============================================================

def fetch_stock_data_fmp(ticker):
    """Fetch all data for one US stock from FMP (15 endpoints)"""
    print(f"   📊 {ticker} (FMP)...")
    hist = fetch_historical_fmp(ticker)
    price, quote_data = fetch_current_price_fmp(ticker, hist)

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
    }


def fetch_stock_data_yfinance(ticker):
    """Fetch all data for LDO.MI from yfinance (2 API calls only)"""
    import yfinance as yf

    print(f"   📊 {ticker} (yfinance)...")
    stock = yf.Ticker(ticker)

    # Call 1: Historical OHLCV
    hist_raw = stock.history(period="2y")
    hist = None
    if not hist_raw.empty:
        hist = hist_raw.reset_index()[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']]
        print(f"   ✅ {ticker}: {len(hist)} days (yfinance)")

    # Call 2: Info (current price, targets)
    # yfinance gets 429 on GitHub Actions shared IPs — must not crash pipeline
    info = {}
    try:
        info = stock.info
    except Exception as e:
        print(f"   ⚠️  {ticker} yfinance info failed: {e}")

    current_price = info.get('currentPrice', info.get('regularMarketPrice'))
    if current_price is None and hist is not None and not hist.empty:
        current_price = float(hist['Close'].iloc[-1])

    # Earnings dates (separate call, also may 429)
    earnings_date = None
    try:
        earnings = stock.get_earnings_dates(limit=5)
        if earnings is not None and not earnings.empty:
            today = datetime.now()
            future = earnings[earnings.index >= today]
            if not future.empty:
                earnings_date = future.index[0].strftime('%Y-%m-%d')
    except:
        pass

    # Compute RSI locally for LDO.MI (no FMP access)
    rsi = None
    if hist is not None and len(hist) >= 20:
        closes = hist['Close']
        delta = closes.diff().dropna()
        gain = delta.where(delta > 0, 0)
        loss = (-delta).where(delta < 0, 0)
        avg_gain = gain.rolling(14).mean().iloc[-1]
        avg_loss = loss.rolling(14).mean().iloc[-1]
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

    return {
        'ticker': ticker,
        'historical': hist,
        'current_price': float(current_price) if current_price else None,
        'quote_data': {},
        'price_targets': {
            'targetHigh': info.get('targetHighPrice'),
            'targetLow': info.get('targetLowPrice'),
            'targetMean': info.get('targetMeanPrice'),
            'targetMedian': info.get('targetMedianPrice')
        },
        'earnings_date': earnings_date,
        'analyst_grade': {},
        'momentum': {},
        'forward_estimates': {},
        'target_trend': {},
        'rsi': rsi,
        'profile': {'beta': info.get('beta'), 'sector': info.get('sector')},
        'financial_scores': {},
        'grades_consensus': {},
        'dcf_value': None,
        'insider_stats': {},
    }


# =============================================================
# PORTFOLIO ORCHESTRATOR
# =============================================================

def fetch_portfolio_data():
    """Fetch data for all 14 portfolio stocks + macro calendar"""
    portfolio_data = {}

    for ticker in PORTFOLIO.keys():
        if ticker in YFINANCE_TICKERS:
            portfolio_data[ticker] = fetch_stock_data_yfinance(ticker)
        else:
            portfolio_data[ticker] = fetch_stock_data_fmp(ticker)

    ok = sum(1 for d in portfolio_data.values() if d['current_price'] is not None)
    print(f"\n   Data fetched: {ok}/{len(PORTFOLIO)} stocks with price data")

    # Fetch macro calendar (1 call, not per-stock)
    macro_events = fetch_economic_calendar()
    print(f"   📅 {len(macro_events)} macro events in next 60 days")

    return portfolio_data, macro_events
