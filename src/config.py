"""
SGC Dip Engine v6 — Configuration
Portfolio Constitution v7 - Final Specification

All thresholds documented with rationale for independent audit.
"""

import os

# =============================================================
# FMP API (primary data source for 13 US stocks)
# Stable API pattern: symbol in query params, NOT path
# Ref: rationale.md §2.2
# =============================================================
FMP_API_KEY = os.getenv('FMP_API_KEY', 'ld6wilmawW3FutupImuIMeNIuqafQIMo')
FMP_BASE_URL = "https://financialmodelingprep.com/stable"
API_DELAY = 0.3   # seconds between FMP calls (rate limit)
API_TIMEOUT = 10   # seconds per request

# =============================================================
# PORTFOLIO — Constitution v7
# LDO.MI uses yfinance (FMP returns 402 for European tickers)
# Ref: rationale.md §1.7
# =============================================================
PORTFOLIO = {
    'NVDA':   {'weight': 0.13, 'role': 'Core Growth',    'block': 'T1'},
    'MSFT':   {'weight': 0.13, 'role': 'Core Growth',    'block': 'T1'},
    'GOOGL':  {'weight': 0.13, 'role': 'Core Growth',    'block': 'T1'},
    'META':   {'weight': 0.05, 'role': 'Core Growth',    'block': 'T1'},
    'AMZN':   {'weight': 0.03, 'role': 'Core Growth',    'block': 'T1'},
    'AVGO':   {'weight': 0.10, 'role': 'Infrastructure', 'block': 'T2'},
    'ASML':   {'weight': 0.08, 'role': 'Infrastructure', 'block': 'T2'},
    'MU':     {'weight': 0.04, 'role': 'Infrastructure', 'block': 'T2'},
    'CEG':    {'weight': 0.04, 'role': 'Power',          'block': 'P'},
    'VST':    {'weight': 0.03, 'role': 'Power',          'block': 'P'},
    'MA':     {'weight': 0.10, 'role': 'Resilience',     'block': 'D'},
    'CTAS':   {'weight': 0.05, 'role': 'Resilience',     'block': 'D'},
    'LDO.MI': {'weight': 0.05, 'role': 'Resilience',     'block': 'D'},
    'WM':     {'weight': 0.04, 'role': 'Resilience',     'block': 'D'},
}

# Tickers routed to yfinance instead of FMP
YFINANCE_TICKERS = {'LDO.MI'}

# =============================================================
# SIMULATION PARAMETERS
# Ref: rationale.md §1.2 Layer 4, §1.4
# =============================================================
SIMULATION_DAYS = 60
NUM_PATHS = 10000
PERCENTILE_TARGET = 50   # 50th percentile = median of minimums

# =============================================================
# SIGNAL THRESHOLDS
# Ref: rationale.md §1.4, §1.5
# =============================================================
BUY_THRESHOLD = 0.50   # P(dip) < 50% → BUY

# Materiality: if expected dip < this %, signal BUY regardless.
# Rationale: telling Jesse to wait for a 1.5% dip on WM is noise.
# A 3% threshold means: only signal WAIT if the expected dip
# saves meaningful money on the DCA contribution.
MIN_ACTIONABLE_DIP_PCT = 0.03

# =============================================================
# DATA QUALITY THRESHOLDS (Guardrails — Gate 1)
# =============================================================

# Minimum historical data rows for GARCH to be reliable
HIST_MIN_ROWS_WARN = 200     # warn below this
HIST_MIN_ROWS_SKIP = 50      # skip stock below this

# Historical data freshness: max trading days since last data point
HIST_MAX_STALE_DAYS = 5

# Price cross-check: max divergence between quote and last close
PRICE_CROSSCHECK_MAX_PCT = 0.03   # 3%

# Single-day return outlier threshold (possible data error / split)
RETURN_OUTLIER_PCT = 0.20   # 20% single-day move flagged

# Analyst target bounds relative to current price
ANCHOR_MIN_RATIO = 0.5    # target < 50% of price = suspect
ANCHOR_MAX_RATIO = 2.5    # target > 250% of price = suspect

# Volume floor (mean daily)
VOLUME_MIN_DAILY = 100000

# =============================================================
# MODEL OUTPUT THRESHOLDS (Guardrails — Gate 2)
# =============================================================

# GARCH annualized vol cap: above this, stock is "unmodelable"
# No mega-cap sustains >150% annualized vol in normal markets
VOL_UNMODELABLE_PCT = 1.50   # 150%

# GARCH stationarity warning: alpha + beta near 1.0
GARCH_STATIONARITY_WARN = 0.95

# Correlation matrix: max off-diagonal value
CORR_MAX_OFFDIAG = 0.98

# =============================================================
# SIMULATION OUTPUT THRESHOLDS (Guardrails — Gate 3)
# =============================================================

# Flag (don't clamp) if median dip exceeds this % below current
DIP_EXTREME_FLAG_PCT = 0.30   # 30% dip flagged as extreme

# =============================================================
# PORTFOLIO-LEVEL THRESHOLDS (Guardrails — Gate 4)
# =============================================================

# VIX sanity bounds
VIX_FLOOR = 5.0
VIX_CEILING = 80.0

# Minimum stocks with valid signals to publish dashboard
MIN_VALID_STOCKS = 10

# Signal flip detection: if this many signals change vs prior day
SIGNAL_FLIP_WARN = 10

# =============================================================
# ANALYST GRADE FRESHNESS
# =============================================================
ANALYST_GRADE_MAX_AGE = 90   # days

# =============================================================
# HISTORICAL DATA LOOKBACK
# =============================================================
LOOKBACK_DAYS = 730   # ~2 years of calendar days

# =============================================================
# OUTPUT PATHS (relative to src/)
# Ref: rationale.md §2.6
# =============================================================
OUTPUT_DIR = "../docs"
OUTPUT_FILE = "index.html"
