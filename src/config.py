"""
SGC Dip Engine v7 — Configuration
Loads from config.yaml via config_loader.

All thresholds now in config/config.yaml for easy tuning.
API keys in environment variables only (never hardcoded).
"""

import os
from config_loader import load_config, get_config

# Load config on import
config = load_config()

# =============================================================
# API KEYS (Environment Variables Only - NEVER HARDCODED)
# =============================================================
FMP_API_KEY = os.getenv('FMP_API_KEY', '')  # ← SECURITY FIX: No fallback
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
EULERPOOL_TOKEN = os.getenv('EULERPOOL_TOKEN', '')

if not FMP_API_KEY:
    raise ValueError("FMP_API_KEY environment variable not set")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY environment variable not set")

# =============================================================
# FMP API Configuration
# =============================================================
FMP_BASE_URL = get_config('data', 'fmp_base_url')
API_DELAY = get_config('data', 'api_delay')
API_TIMEOUT = get_config('data', 'api_timeout')

# =============================================================
# PORTFOLIO
# =============================================================
PORTFOLIO = {}
tickers = get_config('portfolio', 'tickers')
weights = get_config('portfolio', 'weights', default={})

for ticker in tickers:
    PORTFOLIO[ticker] = {
        'weight': weights.get(ticker, 0.0),
        'role': 'Core',  # Simplified - full role mapping can be added later
        'block': 'T1'
    }

YFINANCE_TICKERS = set(get_config('data', 'yfinance_tickers', default=[]))

# EUR display tickers: USD-traded ADRs that should display in EUR (e.g. ASML)
# Controlled from config.yaml data.eur_display_tickers
EUR_DISPLAY_TICKERS = set(get_config('data', 'eur_display_tickers', default=[]))

# Power sector tickers: stocks with binary catalysts (PPA wins, restarts)
# Used to generate context note when >20% moves detected — no stock names in warning
POWER_SECTOR_TICKERS = set(get_config('data', 'power_sector_tickers', default=[]))

# =============================================================
# SIMULATION PARAMETERS
# =============================================================
SIMULATION_DAYS = get_config('monte_carlo', 'simulation_days')
NUM_PATHS = get_config('monte_carlo', 'num_paths')
PERCENTILE_TARGET = get_config('signal', 'percentile_target')

# =============================================================
# SIGNAL THRESHOLDS
# =============================================================
MIN_ACTIONABLE_DIP_PCT = get_config('signal', 'min_actionable_dip_pct')

# Rally targets: conviction % and display threshold (from config.yaml)
RALLY_CONVICTION_PERCENTILE = get_config('signal', 'rally_conviction_percentile', default=60)
MIN_ACTIONABLE_RALLY_PCT = get_config('signal', 'min_actionable_rally_pct', default=0.01)

# =============================================================
# DATA QUALITY THRESHOLDS (Guardrails — Gate 1)
# =============================================================
HIST_MIN_ROWS_WARN = get_config('validation', 'gate1', 'hist_min_rows_warn')
HIST_MIN_ROWS_SKIP = get_config('validation', 'gate1', 'hist_min_rows_skip')
HIST_MAX_STALE_DAYS = get_config('validation', 'gate1', 'hist_max_stale_days')
PRICE_CROSSCHECK_MAX_PCT = get_config('validation', 'gate1', 'price_crosscheck_max_pct')
RETURN_OUTLIER_PCT = get_config('validation', 'gate1', 'return_outlier_pct')
ANCHOR_MIN_RATIO = get_config('validation', 'gate2', 'anchor_min_ratio')
ANCHOR_MAX_RATIO = get_config('validation', 'gate2', 'anchor_max_ratio')
VOLUME_MIN_DAILY = get_config('validation', 'gate1', 'volume_min_daily')

# =============================================================
# MODEL OUTPUT THRESHOLDS (Guardrails — Gate 2)
# =============================================================
VOL_UNMODELABLE_PCT = get_config('validation', 'gate2', 'vol_unmodelable_pct')
GARCH_STATIONARITY_WARN = get_config('validation', 'gate2', 'garch_stationarity_warn')
CORR_MAX_OFFDIAG = get_config('validation', 'gate2', 'corr_max_offdiag')

# =============================================================
# SIMULATION OUTPUT THRESHOLDS (Guardrails — Gate 3)
# =============================================================
DIP_EXTREME_FLAG_PCT = get_config('validation', 'gate3', 'dip_extreme_flag_pct')

# =============================================================
# PORTFOLIO-LEVEL THRESHOLDS (Guardrails — Gate 4)
# =============================================================
VIX_FLOOR = get_config('validation', 'gate4', 'vix_floor')
VIX_CEILING = get_config('validation', 'gate4', 'vix_ceiling')
MIN_VALID_STOCKS = get_config('validation', 'gate4', 'min_valid_stocks')
SIGNAL_FLIP_WARN = get_config('validation', 'gate4', 'signal_flip_warn')

# =============================================================
# ANALYST GRADE FRESHNESS
# =============================================================
ANALYST_GRADE_MAX_AGE = get_config('validation', 'gate1', 'analyst_grade_max_age')

# =============================================================
# HISTORICAL DATA LOOKBACK
# =============================================================
LOOKBACK_DAYS = get_config('monte_carlo', 'lookback_days')

# =============================================================
# OUTPUT PATHS
# =============================================================
OUTPUT_DIR = "../docs"
OUTPUT_FILE = "index.html"

# =============================================================
# ENRICHMENT COEFFICIENTS (Phase 2)
# =============================================================
RSI_COEFFICIENT = get_config('enrichment', 'rsi_coefficient')
SENTIMENT_COEFFICIENT = get_config('enrichment', 'sentiment_coefficient')
MOMENTUM_COEFFICIENT = get_config('enrichment', 'momentum_coefficient')
INSIDER_COEFFICIENT = get_config('enrichment', 'insider_coefficient')
MAX_TOTAL_DRIFT = get_config('enrichment', 'max_total_drift')

EARNINGS_VOL_14D = get_config('enrichment', 'earnings_vol', 'within_14_days')
EARNINGS_VOL_30D = get_config('enrichment', 'earnings_vol', 'within_30_days')
EARNINGS_VOL_60D = get_config('enrichment', 'earnings_vol', 'within_60_days')

# =============================================================
# Validation: Check critical values loaded correctly
# =============================================================
print(f"✅ Config loaded: {len(PORTFOLIO)} tickers, percentile={PERCENTILE_TARGET}, threshold={MIN_ACTIONABLE_DIP_PCT}")
