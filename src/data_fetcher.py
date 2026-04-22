"""
Data Fetcher - Retrieves all stock data from yfinance
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from config import PORTFOLIO, LOOKBACK_DAYS, ANALYST_GRADE_MAX_AGE

def fetch_historical_prices(ticker, days=LOOKBACK_DAYS):
    """
    Fetch 2yr daily OHLCV from yfinance
    Returns: DataFrame with columns [Date, Open, High, Low, Close, Volume]
    """
    try:
        stock = yf.Ticker(ticker)
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        df = stock.history(start=start_date, end=end_date)
        
        if df.empty:
            print(f"⚠️  No historical data for {ticker}")
            return None
            
        df = df.reset_index()
        df = df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']]
        
        return df
        
    except Exception as e:
        print(f"❌ Error fetching historical data for {ticker}: {e}")
        return None

def fetch_current_price(ticker):
    """
    Get current price from yfinance
    Returns: float (current price)
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        current_price = info.get('currentPrice', info.get('regularMarketPrice'))
        
        if current_price is None:
            # Fallback: use latest close from history
            hist = stock.history(period="1d")
            if not hist.empty:
                current_price = hist['Close'].iloc[-1]
        
        return current_price
            
    except Exception as e:
        print(f"❌ Error fetching current price for {ticker}: {e}")
        return None

def fetch_price_targets(ticker):
    """
    Get analyst price targets from yfinance
    Returns: dict with targetHigh, targetLow, targetMean
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        return {
            'targetHigh': info.get('targetHighPrice'),
            'targetLow': info.get('targetLowPrice'),
            'targetMean': info.get('targetMeanPrice'),
            'targetMedian': info.get('targetMedianPrice')
        }
        
    except Exception as e:
        print(f"⚠️  No price targets for {ticker}: {e}")
        return {}

def fetch_earnings_calendar(ticker):
    """
    Get next earnings date from yfinance
    Returns: date string (YYYY-MM-DD) or None
    """
    try:
        stock = yf.Ticker(ticker)
        earnings = stock.get_earnings_dates(limit=10)
        
        if earnings is not None and not earnings.empty:
            # Find next future earnings date
            today = datetime.now()
            future_earnings = earnings[earnings.index >= today]
            
            if not future_earnings.empty:
                next_earnings = future_earnings.index[0]
                return next_earnings.strftime('%Y-%m-%d')
        
        return None
        
    except Exception as e:
        print(f"⚠️  No earnings calendar for {ticker}: {e}")
        return None

def fetch_analyst_grades(ticker):
    """
    Get latest analyst grade action from yfinance
    Returns: dict with date, firm, action, toGrade, priceTargetAction
    """
    try:
        stock = yf.Ticker(ticker)
        grades = stock.upgrades_downgrades
        
        if grades is not None and not grades.empty:
            # Get most recent grade
            latest = grades.head(1).iloc[0]
            latest_date = grades.index[0]
            
            # Check freshness (only use if < 90 days old)
            days_old = (datetime.now() - latest_date).days
            
            if days_old > ANALYST_GRADE_MAX_AGE:
                print(f"⚠️  Analyst grade for {ticker} is {days_old} days old (stale)")
                return {}
            
            return {
                'date': latest_date.strftime('%Y-%m-%d'),
                'firm': latest.get('Firm'),
                'action': latest.get('Action'),
                'toGrade': latest.get('ToGrade'),
                'fromGrade': latest.get('FromGrade'),
                'priceTargetAction': latest.get('priceTargetAction'),
                'currentPriceTarget': latest.get('currentPriceTarget'),
                'days_old': days_old
            }
        
        return {}
        
    except Exception as e:
        print(f"⚠️  No analyst grades for {ticker}: {e}")
        return {}

def fetch_all_stock_data(ticker):
    """
    Orchestrator: fetch all data for one stock
    Returns: dict with all components
    """
    print(f"📊 Fetching data for {ticker}...")
    
    return {
        'ticker': ticker,
        'historical': fetch_historical_prices(ticker),
        'current_price': fetch_current_price(ticker),
        'price_targets': fetch_price_targets(ticker),
        'earnings_date': fetch_earnings_calendar(ticker),
        'analyst_grade': fetch_analyst_grades(ticker)
    }

def fetch_portfolio_data():
    """
    Fetch data for all portfolio stocks
    Returns: dict keyed by ticker
    """
    portfolio_data = {}
    
    for ticker in PORTFOLIO.keys():
        portfolio_data[ticker] = fetch_all_stock_data(ticker)
    
    return portfolio_data
