"""
GARCH(1,1) Volatility Forecasting
Estimates forward-looking volatility with clustering effects
"""

import numpy as np
import pandas as pd
from scipy.optimize import minimize

def fit_garch_11(returns):
    """
    Fit GARCH(1,1) model to return series
    Returns: dict with omega, alpha, beta, forecast_variance
    
    GARCH(1,1): σ²(t) = ω + α*r²(t-1) + β*σ²(t-1)
    """
    
    # Remove NaN and infinite values
    returns = returns.replace([np.inf, -np.inf], np.nan).dropna()
    
    if len(returns) < 50:
        # Fallback to simple volatility if insufficient data
        return {
            'omega': 0,
            'alpha': 0,
            'beta': 0,
            'forecast_variance': returns.var()
        }
    
    # Initialize parameters (typical starting values)
    initial_params = [0.01, 0.05, 0.90]  # omega, alpha, beta
    
    def garch_likelihood(params):
        """Negative log-likelihood for GARCH(1,1)"""
        omega, alpha, beta = params
        
        # Constraint: omega > 0, alpha >= 0, beta >= 0, alpha + beta < 1
        if omega <= 0 or alpha < 0 or beta < 0 or (alpha + beta) >= 1:
            return 1e10
        
        T = len(returns)
        sigma2 = np.zeros(T)
        sigma2[0] = returns.var()  # Initial variance
        
        for t in range(1, T):
            sigma2[t] = omega + alpha * returns.iloc[t-1]**2 + beta * sigma2[t-1]
        
        # Log-likelihood (assuming normal distribution)
        ll = -0.5 * np.sum(np.log(2 * np.pi * sigma2) + returns**2 / sigma2)
        
        return -ll  # Return negative for minimization
    
    # Optimize
    try:
        result = minimize(
            garch_likelihood,
            initial_params,
            method='L-BFGS-B',
            bounds=[(1e-6, 1), (0, 1), (0, 1)]
        )
        
        omega, alpha, beta = result.x
        
        # Forecast variance (one-step ahead)
        last_return = returns.iloc[-1]
        last_variance = returns.tail(20).var()  # Recent variance estimate
        forecast_variance = omega + alpha * last_return**2 + beta * last_variance
        
        return {
            'omega': omega,
            'alpha': alpha,
            'beta': beta,
            'forecast_variance': forecast_variance
        }
        
    except:
        # Fallback to rolling variance if optimization fails
        return {
            'omega': 0,
            'alpha': 0,
            'beta': 0,
            'forecast_variance': returns.tail(90).var()
        }

def calculate_forward_volatility(price_df, days_forward=60):
    """
    Calculate annualized forward volatility estimate
    
    Args:
        price_df: DataFrame with 'Close' column
        days_forward: forecast horizon in days
    
    Returns: annualized volatility (sigma)
    """
    
    # Calculate log returns
    returns = np.log(price_df['Close'] / price_df['Close'].shift(1)).dropna()
    
    # Fit GARCH
    garch_params = fit_garch_11(returns)
    
    # Annualize variance (252 trading days/year)
    forecast_variance_annual = garch_params['forecast_variance'] * 252
    
    # Return as volatility (standard deviation)
    return np.sqrt(forecast_variance_annual)
