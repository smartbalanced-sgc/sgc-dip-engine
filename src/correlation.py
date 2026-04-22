"""
Correlation Matrix Builder
Calculates stock return correlations for correlated Monte Carlo
"""

import numpy as np
import pandas as pd

def build_correlation_matrix(portfolio_data):
    """
    Build correlation matrix from historical returns
    
    Args:
        portfolio_data: dict of stock data (from data_fetcher)
    
    Returns: correlation matrix (numpy array), ticker order (list)
    """
    
    # Extract returns for all stocks
    returns_dict = {}
    
    for ticker, data in portfolio_data.items():
        if data['historical'] is not None and not data['historical'].empty:
            df = data['historical']
            returns = np.log(df['Close'] / df['Close'].shift(1)).dropna()
            returns_dict[ticker] = returns
    
    # Align dates (use inner join to keep only common dates)
    returns_df = pd.DataFrame(returns_dict)
    returns_df = returns_df.dropna()  # Drop any remaining NaNs
    
    if returns_df.empty:
        print("⚠️  No overlapping data for correlation calculation")
        # Return identity matrix (no correlation)
        n = len(portfolio_data)
        return np.eye(n), list(portfolio_data.keys())
    
    # Calculate correlation matrix
    corr_matrix = returns_df.corr().values
    ticker_order = returns_df.columns.tolist()
    
    return corr_matrix, ticker_order

def generate_correlated_random_numbers(corr_matrix, num_samples):
    """
    Generate correlated random numbers using Cholesky decomposition
    
    Args:
        corr_matrix: correlation matrix (n x n)
        num_samples: number of samples to generate
    
    Returns: array of shape (num_samples, n)
    """
    
    n = corr_matrix.shape[0]
    
    # Cholesky decomposition
    try:
        L = np.linalg.cholesky(corr_matrix)
    except np.linalg.LinAlgError:
        # If matrix is not positive definite, use nearest positive definite matrix
        print("⚠️  Correlation matrix not positive definite, adjusting...")
        corr_matrix = nearest_positive_definite(corr_matrix)
        L = np.linalg.cholesky(corr_matrix)
    
    # Generate independent normal random numbers
    independent = np.random.normal(0, 1, size=(num_samples, n))
    
    # Transform to correlated
    correlated = independent @ L.T
    
    return correlated

def nearest_positive_definite(A):
    """Find the nearest positive definite matrix to A"""
    B = (A + A.T) / 2
    _, s, V = np.linalg.svd(B)
    
    H = V.T @ np.diag(s) @ V
    A2 = (B + H) / 2
    A3 = (A2 + A2.T) / 2
    
    if is_positive_definite(A3):
        return A3
    
    spacing = np.spacing(np.linalg.norm(A))
    I = np.eye(A.shape[0])
    k = 1
    while not is_positive_definite(A3):
        mineig = np.min(np.real(np.linalg.eigvals(A3)))
        A3 += I * (-mineig * k**2 + spacing)
        k += 1
    
    return A3

def is_positive_definite(A):
    """Check if matrix is positive definite"""
    try:
        np.linalg.cholesky(A)
        return True
    except np.linalg.LinAlgError:
        return False
