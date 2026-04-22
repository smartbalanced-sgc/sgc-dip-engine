"""
Configuration for SGC Dip Engine
Portfolio Constitution v7 - Final Specification
"""

# Portfolio specification from v7 constitution
PORTFOLIO = {
    'NVDA': {'weight': 0.13, 'role': 'Core Growth', 'block': 'T1'},
    'MSFT': {'weight': 0.13, 'role': 'Core Growth', 'block': 'T1'},
    'GOOGL': {'weight': 0.13, 'role': 'Core Growth', 'block': 'T1'},
    'META': {'weight': 0.05, 'role': 'Core Growth', 'block': 'T1'},
    'AMZN': {'weight': 0.03, 'role': 'Core Growth', 'block': 'T1'},
    'AVGO': {'weight': 0.10, 'role': 'Infrastructure', 'block': 'T2'},
    'ASML': {'weight': 0.08, 'role': 'Infrastructure', 'block': 'T2'},
    'MU': {'weight': 0.04, 'role': 'Infrastructure', 'block': 'T2'},
    'CEG': {'weight': 0.04, 'role': 'Power', 'block': 'P'},
    'VST': {'weight': 0.03, 'role': 'Power', 'block': 'P'},
    'MA': {'weight': 0.10, 'role': 'Resilience', 'block': 'D'},
    'CTAS': {'weight': 0.05, 'role': 'Resilience', 'block': 'D'},
    'LDO.MI': {'weight': 0.05, 'role': 'Resilience', 'block': 'D'},  # Leonardo (Milan)
    'WM': {'weight': 0.04, 'role': 'Resilience', 'block': 'D'}
}

# Simulation parameters
SIMULATION_DAYS = 60  # Forward window
NUM_PATHS = 10000     # Monte Carlo paths
PERCENTILE_TARGET = 50  # Use median (50th percentile) as "most likely low"

# Signal thresholds
BUY_THRESHOLD = 0.50  # P(Now=Best) > 50% → BUY

# Analyst grade freshness (days)
ANALYST_GRADE_MAX_AGE = 90  # Only use grades < 90 days old

# Historical data lookback
LOOKBACK_DAYS = 504  # 2 years of trading days

# Output paths
OUTPUT_DIR = "docs"
OUTPUT_FILE = "index.html"
