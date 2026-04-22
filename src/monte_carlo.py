"""
Correlated Monte Carlo Simulation Engine
Simulates 10,000 correlated price paths over 60 days
"""

import numpy as np
import pandas as pd
from config import NUM_PATHS, SIMULATION_DAYS, PERCENTILE_TARGET
from correlation import generate_correlated_random_numbers

def run_monte_carlo_stock(
    current_price,
    volatility,
    drift_mult,
    vol_mult,
    mean_reversion_anchor,
    days=SIMULATION_DAYS,
    num_paths=NUM_PATHS,
    correlated_randoms=None
):
    """
    Run Monte Carlo simulation for one stock
    
    Args:
        current_price: current stock price
        volatility: annualized volatility (from GARCH)
        drift_mult: regime drift multiplier
        vol_mult: regime volatility multiplier
        mean_reversion_anchor: price target for mean reversion (e.g., 50-day MA or analyst target)
        days: simulation horizon
        num_paths: number of paths
        correlated_randoms: pre-generated correlated random numbers (optional)
    
    Returns: array of shape (num_paths, days) with simulated prices
    """
    
    dt = 1/252  # Daily time step
    
    # Adjust volatility for regime
    adj_volatility = volatility * vol_mult
    
    # Mean reversion strength (stronger if price deviates more from anchor)
    deviation = (current_price - mean_reversion_anchor) / mean_reversion_anchor if mean_reversion_anchor > 0 else 0
    mean_reversion_pull = -0.1 * deviation  # Pull back toward anchor
    
    # Drift (combine regime adjustment + mean reversion)
    drift = (drift_mult - 1.0 + mean_reversion_pull) * dt
    
    # Diffusion
    diffusion = adj_volatility * np.sqrt(dt)
    
    # Initialize price paths
    paths = np.zeros((num_paths, days + 1))
    paths[:, 0] = current_price
    
    # Generate random shocks (use correlated if provided)
    if correlated_randoms is not None:
        randoms = correlated_randoms
    else:
        randoms = np.random.normal(0, 1, size=(num_paths, days))
    
    # Simulate paths (geometric Brownian motion with mean reversion)
    for t in range(1, days + 1):
        paths[:, t] = paths[:, t-1] * np.exp(drift + diffusion * randoms[:, t-1])
    
    return paths[:, 1:]  # Exclude initial price

def extract_statistics(paths):
    """
    Extract key statistics from simulated paths
    
    Returns: dict with percentile_low, confidence, median_date
    """
    
    # Find minimum in each path
    minimums = paths.min(axis=1)
    
    # Calculate percentile
    percentile_low = np.percentile(minimums, PERCENTILE_TARGET)
    
    # Count paths that hit this level or lower (confidence)
    confidence = np.mean(minimums <= percentile_low)
    
    # Find median date when minimum occurs (among paths hitting the target)
    paths_hitting_target = paths[minimums <= percentile_low]
    if len(paths_hitting_target) > 0:
        min_dates = np.argmin(paths_hitting_target, axis=1)
        median_date_index = int(np.median(min_dates))
    else:
        median_date_index = SIMULATION_DAYS // 2  # Default to mid-window
    
    return {
        'percentile_low': percentile_low,
        'confidence': confidence,
        'median_date_index': median_date_index
    }

def simulate_portfolio(portfolio_data, corr_matrix, ticker_order, regime_info):
    """
    Run correlated simulations for all stocks
    
    Args:
        portfolio_data: dict of stock data
        corr_matrix: correlation matrix
        ticker_order: order of tickers matching corr_matrix
        regime_info: dict with stock-level and macro regimes
    
    Returns: dict of simulation results per ticker
    """
    
    results = {}
    n_stocks = len(ticker_order)
    
    # Generate correlated random numbers (NUM_PATHS x SIMULATION_DAYS x n_stocks)
    correlated_randoms_all = np.zeros((NUM_PATHS, SIMULATION_DAYS, n_stocks))
    for day in range(SIMULATION_DAYS):
        correlated_randoms_all[:, day, :] = generate_correlated_random_numbers(corr_matrix, NUM_PATHS)
    
    for i, ticker in enumerate(ticker_order):
        data = portfolio_data[ticker]
        
        if data['current_price'] is None or data['historical'] is None:
            print(f"⚠️  Skipping {ticker} - missing data")
            continue
        
        # Get regime adjustments
        stock_regime = regime_info['stock_regimes'].get(ticker, {'drift_mult': 1.0, 'vol_mult': 1.0})
        macro_adj = regime_info['macro_adjustments']
        
        combined_drift = stock_regime['drift_mult']
        combined_vol = stock_regime['vol_mult'] * macro_adj['vol_mult']
        
        # Mean reversion anchor (use analyst target mean if available, else 50-day MA)
        price_targets = data.get('price_targets', {})
        if price_targets.get('targetMean'):
            anchor = price_targets['targetMean']
        else:
            # Calculate 50-day MA from historical
            anchor = data['historical']['Close'].tail(50).mean()
        
        # Extract correlated randoms for this stock
        stock_randoms = correlated_randoms_all[:, :, i]
        
        # Run simulation
        from garch_model import calculate_forward_volatility
        volatility = calculate_forward_volatility(data['historical'])
        
        paths = run_monte_carlo_stock(
            current_price=data['current_price'],
            volatility=volatility,
            drift_mult=combined_drift,
            vol_mult=combined_vol,
            mean_reversion_anchor=anchor,
            correlated_randoms=stock_randoms
        )
        
        # Extract statistics
        stats = extract_statistics(paths)
        
        results[ticker] = {
            'current_price': data['current_price'],
            'percentile_low': stats['percentile_low'],
            'confidence': stats['confidence'],
            'median_date_index': stats['median_date_index'],
            'paths': paths  # Store for potential backtesting
        }
    
    return results
