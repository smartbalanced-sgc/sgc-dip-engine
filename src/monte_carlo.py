"""
Correlated Monte Carlo Simulation Engine
Simulates 10,000 correlated price paths over 60 days

CONFIDENCE FIX (Apr 2026):
  Old: confidence = fraction of paths hitting median low (always ~50% by definition)
  New: confidence = fraction of paths where price dips below current price at any point
  This answers the actual question: "If I wait, is a better price coming?"
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
        mean_reversion_anchor: price target for mean reversion
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

def extract_statistics(paths, current_price):
    """
    Extract key statistics from simulated paths.

    Returns: dict with percentile_low, confidence, median_date

    CONFIDENCE DEFINITION:
      Fraction of 10,000 paths where the stock goes BELOW current price
      at any point in 60 days. This answers: "If I wait, is a lower price coming?"

    PERCENTILE_LOW DEFINITION:
      50th percentile (median) of the minimum price across all paths.
      This answers: "If a dip happens, how deep is the most likely low?"
    """

    # Find minimum in each path
    minimums = paths.min(axis=1)

    # Target: median of the minimums = "most likely low"
    percentile_low = np.percentile(minimums, PERCENTILE_TARGET)

    # Confidence: what fraction of paths dip below current price?
    # THIS is the question Jesse cares about — not the tautological median check
    confidence = float(np.mean(minimums < current_price))

    # Find median date when minimum occurs (among paths that do dip)
    dipping_paths = paths[minimums < current_price]
    if len(dipping_paths) > 0:
        min_dates = np.argmin(dipping_paths, axis=1)
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
    Run correlated simulations for all stocks.

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

        # Extract statistics — pass current_price for correct confidence calc
        stats = extract_statistics(paths, data['current_price'])

        results[ticker] = {
            'current_price': data['current_price'],
            'percentile_low': stats['percentile_low'],
            'confidence': stats['confidence'],
            'median_date_index': stats['median_date_index'],
            'paths': paths
        }

    return results
