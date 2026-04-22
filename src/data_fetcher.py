"""
Data Fetcher - Retrieves all stock data from yfinance
Includes rate limiting to avoid 429 errors on GitHub Actions
"""

import yfinance as yf
import pandas as pd
import time
from datetime import datetime, timedelta
from config import PORTFOLIO, LOOKBACK_DAYS, ANALYST_GRADE_MAX_AGE

# Delay between API calls to avoid rate limiting
REQUEST_DELAY = 2  # seconds

def fetch_historical_prices_batch(tickers):
    """
    Fetch 2yr daily OHLCV for ALL tickers in one batch call
    This avoids per-ticker rate limiting
    
    Returns: dict of ticker -> DataFrame
    """
    try:
        print(f"   Downloading historical data for {len(tickers)} tickers (batch)...")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=LOOKBACK_DAYS)
        
        # Single batch download - much less likely to be rate limited
        data = yf.download(
            tickers=list(tickers),
            start=start_date,
            end=end_date,
            group_by='ticker',
            auto_adjust=True,
            threads=False  # Sequential to avoid rate limits
        )
        
        result = {}
        
        for ticker in tickers:
            try:
                if len(tickers) == 1:
                    df = data.copy()
                else:
                    df = data[ticker].copy()
                
                df = df.dropna(subset=['Close'])
                
                if not df.empty:
                    df = df.reset_index()
                    df = df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']]
                    result[ticker] = df
                    print(f"   ✅ {ticker}: {len(df)} days")
                else:
                    print(f"   ⚠️  {ticker}: No data")
                    result[ticker] = None
            except Exception as e:
                print(f"   ⚠️  {ticker}: Error extracting data: {e}")
                result[ticker] = None
        
        return result
        
    except Exception as e:
        print(f"   ❌ Batch download failed: {e}")
        return {ticker: None for ticker in tickers}

def fetch_current_price(ticker, historical_df=None):
    """
    Get current price - use last close from historical data to avoid extra API calls
    Returns: float (current price)
    """
    if historical_df is not None and not historical_df.empty:
        return float(historical_df['Close'].iloc[-1])
    
    return None

def fetch_price_targets(ticker):
    """
    Get analyst price targets from yfinance
    Returns: dict with targetHigh, targetLow, targetMean
    """
    try:
        time.sleep(REQUEST_DELAY)
        stock = yf.Ticker(ticker)
        info = stock.info
        
        return {
            'targetHigh': info.get('targetHighPrice'),
            'targetLow': info.get('targetLowPrice'),
            'targetMean': info.get('targetMeanPrice'),
            'targetMedian': info.get('targetMedianPrice')
        }
        
    except Exception as e:
        print(f"   ⚠️  No price targets for {ticker}: {e}")
        return {}

def fetch_earnings_calendar(ticker):
    """
    Get next earnings date from yfinance
    Returns: date string (YYYY-MM-DD) or None
    """
    try:
        time.sleep(REQUEST_DELAY)
        stock = yf.Ticker(ticker)
        
        # Try calendar first (less likely to be rate limited)
        try:
            calendar = stock.calendar
            if calendar is not None:
                if isinstance(calendar, dict) and 'Earnings Date' in calendar:
                    dates = calendar['Earnings Date']
                    if dates:
                        next_date = dates[0] if isinstance(dates, list) else dates
                        if hasattr(next_date, 'strftime'):
                            return next_date.strftime('%Y-%m-%d')
                        return str(next_date)
        except:
            pass
        
        # Fallback to get_earnings_dates
        try:
            earnings = stock.get_earnings_dates(limit=5)
            if earnings is not None and not earnings.empty:
                today = datetime.now()
                future = earnings[earnings.index >= today]
                if not future.empty:
                    return future.index[0].strftime('%Y-%m-%d')
        except:
            pass
        
        return None
        
    except Exception as e:
        print(f"   ⚠️  No earnings for {ticker}: {e}")
        return None

def fetch_analyst_grades(ticker):
    """
    Get latest analyst grade action from yfinance
    Returns: dict with date, firm, action, toGrade, priceTargetAction
    """
    try:
        time.sleep(REQUEST_DELAY)
        stock = yf.Ticker(ticker)
        
        # Try both attribute names (changed between yfinance versions)
        grades = None
        try:
            grades = stock.upgrades_downgrades
        except:
            pass
        
        if grades is None:
            try:
                grades = stock.recommendations
            except:
                pass
        
        if grades is not None and not grades.empty:
            latest = grades.head(1).iloc[0]
            latest_date = grades.index[0]
            
            days_old = (datetime.now() - latest_date).days
            
            if days_old > ANALYST_GRADE_MAX_AGE:
                return {}
            
            return {
                'date': latest_date.strftime('%Y-%m-%d'),
                'firm': latest.get('Firm', latest.get('firm', 'N/A')),
                'action': latest.get('Action', latest.get('action', 'N/A')),
                'toGrade': latest.get('ToGrade', latest.get('toGrade', 'N/A')),
                'fromGrade': latest.get('FromGrade', latest.get('fromGrade', 'N/A')),
                'priceTargetAction': latest.get('priceTargetAction', 'N/A'),
                'currentPriceTarget': latest.get('currentPriceTarget', 0),
                'days_old': days_old
            }
        
        return {}
        
    except Exception as e:
        print(f"   ⚠️  No analyst grades for {ticker}: {e}")
        return {}

def fetch_portfolio_data():
    """
    Fetch data for all portfolio stocks
    Uses batch download for historical data to minimize API calls
    Returns: dict keyed by ticker
    """
    tickers = list(PORTFOLIO.keys())
    
    # Step 1: Batch download all historical data (single API call)
    print("   Batch downloading historical prices...")
    historical_data = fetch_historical_prices_batch(tickers)
    
    # Step 2: Fetch supplementary data per ticker (with delays)
    portfolio_data = {}
    
    for ticker in tickers:
        print(f"   📊 Fetching supplementary data for {ticker}...")
        
        hist = historical_data.get(ticker)
        current_price = fetch_current_price(ticker, hist)
        
        portfolio_data[ticker] = {
            'ticker': ticker,
            'historical': hist,
            'current_price': current_price,
            'price_targets': fetch_price_targets(ticker),
            'earnings_date': fetch_earnings_calendar(ticker),
            'analyst_grade': fetch_analyst_grades(ticker)
        }
    
    return portfolio_data
