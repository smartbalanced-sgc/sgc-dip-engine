# SGC Dip Engine for Portfolio v7 — AI-Enhanced Tactical Entry System

**Purpose:** Daily BUY/WAIT signal generator for a 14-stock DCA portfolio. Tells the investor whether today's price is the best entry or if a meaningful dip is likely within 60 days.

**Live Dashboard:** https://smartbalanced-sgc.github.io/sgc-dip-engine/<br>
**Schedule:** 8:30 PM BST weekdays (30 min after US market close)<br>
**Monthly deployment:** £500 across 14 stocks per Portfolio Constitution v7

---

## What It Does

The SGC Dip Engine answers one question daily: **"Buy today or wait for a better dip within 60 days?"**

- 📊 **Monte Carlo Simulation:** 10,000 price paths per stock using GARCH volatility + HMM regime detection
- 🤖 **AI Enrichment:** Real-time market intelligence via Claude API (web search + sentiment scoring)
- 🎯 **Precise Targets:** Specific entry prices with 70%/80% conviction levels
- 📅 **Date-Aware:** Catalyst timing (earnings, macro events) integrated into forecasts
- ⚡ **Automated:** Runs Mon-Fri at 8:30 PM BST, publishes to GitHub Pages

**Portfolio:** 14 stocks, £39,090 target allocation, 13.47% weighted CAGR
- **Core Growth:** NVDA, MSFT, GOOGL, META, AMZN (47%)
- **Infrastructure:** AVGO, ASML, MU (22%)
- **Power:** CEG, VST (7%)
- **Resilience:** MA, CTAS, LDO.MI, WM (24%)

---

## Quick Start

### Prerequisites

- Python 3.10+
- API Keys:
  - **FMP (Financial Modeling Prep):** Starter plan ($10/month)
  - **Eulerpool:** Free trial or paid (for LDO.MI data)
  - **Anthropic:** Claude API access (~£10.50/month at current usage)

### Installation

```bash
# Clone repository
git clone https://github.com/smartbalanced-sgc/sgc-dip-engine.git
cd sgc-dip-engine

# Install dependencies
pip install -r requirements.txt

# Configure API keys
cp .env.example .env
# Edit .env with your API keys:
# FMP_API_KEY=your_fmp_key
# EULERPOOL_TOKEN=your_eulerpool_token
# ANTHROPIC_API_KEY=your_anthropic_key

# Run locally
cd src
python main.py
```

### GitHub Actions Setup

1. **Add Secrets** to your GitHub repository:
   - `FMP_API_KEY`
   - `EULERPOOL_TOKEN`
   - `ANTHROPIC_API_KEY`

2. **Enable GitHub Pages:**
   - Settings → Pages → Source: Deploy from branch `main`
   - Folder: `docs/`

3. **Workflow runs automatically:**
   - Mon-Fri at 8:30 PM BST (19:30 UTC cron)
   - Manual trigger: Actions → "SGC Dip Engine - Daily Run" → Run workflow

---

## How It Works

### 1. Data Fetching (3 minutes)

**FMP API (13 US stocks):**
```python
# 15 endpoints per stock
- historical-price-eod/full  # 230 days OHLC
- quote                       # Current price, MA50, MA200, RSI
- price-target-consensus      # Analyst targets
- earnings                    # Next earnings date
- analyst-estimates           # Forward EPS, revenue
- upgrades-downgrades-consensus
- insider-trading
- institutional-ownership
- stock-price-change          # 1M, 3M, 6M momentum
# Plus 2 macro endpoints (VIX, SPY)
```

**Eulerpool API (LDO.MI):**
```python
- equity/profile              # Current price (mcap ÷ shares)
- equity/candles              # Historical OHLC
- equity/price-target         # Analyst targets
- equity/estimates            # Forward estimates
- sentiment/price-metrics     # Beta
- research/recommendations    # Analyst grades
- equity-extended/aaqs        # Quality score
- sentiment/insider-sentiment # Insider activity
```

**Currency Conversion:**
- ASML: Fetch USD, display EUR (Trading212 native currency)
- FX rates: exchangerate-api.com

### 2. AI Enrichment (5 minutes, £0.48/day)

**For each stock:**

**Step 1: Web Search (Claude API)**
```python
prompt = f"Search for recent news (last 30 days) about {company}.
Focus on: earnings, analyst actions, product launches, contracts.
Provide 3-5 key facts impacting 60-day price action."

# Settings (optimized for cost)
max_tokens = 1000  # Was 4000 (60% cost reduction)
```

**Step 2: Sentiment Scoring (Claude API)**
```python
prompt = f"Score sentiment (-10 to +10) based on:
- Search results: {news}
- Analyst consensus: {grade}
- Price targets: {targets}
- Insider activity: {transactions}

Cross-validate score against analyst consensus."

# Settings (optimized for cost)
max_tokens = 200  # Was 300
```

**Output:** Sentiment score adjusts Monte Carlo drift (-10% to +10%)

**Cost Breakdown:**
- Web search: £0.03/stock × 13 = £0.39/day
- Sentiment: £0.01/stock × 13 = £0.13/day  
- **Total: £0.48/day = £10.50/month** (60% cheaper than original)

### 3. Monte Carlo Simulation (3 minutes)

**Per stock:**

```python
# 1. GARCH(1,1) volatility forecast
garch_model = GARCH(1, 1).fit(returns[-230:])
volatility_forecast = garch_model.forecast(horizon=60)

# 2. HMM regime detection (high/low volatility states)
hmm = HMM(n_states=2).fit(returns[-230:])
current_regime = hmm.predict(returns[-5:])
drift_adjustment = regime_drift_map[current_regime]

# 3. Simulate 10,000 correlated paths
for path in range(10000):
    for day in range(60):
        drift = base_drift + sentiment_adjustment + drift_adjustment
        shock = cholesky_correlation @ random_normal(14)
        price[day+1] = price[day] * exp(drift + volatility * shock)
    
    min_price[path] = min(price)  # Track lowest low per path

# 4. Extract percentile targets
target_70 = percentile(min_price, 70)  # 70% of paths reach this
target_80 = percentile(min_price, 80)  # 80% of paths reach this
```

**Correlation Matrix:**
- Full 14×14 matrix from 230-day rolling window
- Preserves portfolio relationships
- Cholesky decomposition for efficiency

### 4. Signal Generation (<1 minute)

```python
# Decision logic
if P(today is 60-day minimum) > 0.5:
    signal = "BUY"
    reasoning = "Unlikely to dip further"
elif (current_price - target_70) / current_price < 0.03:
    signal = "BUY"
    reasoning = f"Expected dip only {dip_pct:.1f}% — not worth waiting"
else:
    signal = "WAIT"
    reasoning = f"{dip_desc} {dip_pct:.1f}% dip expected ({conviction}% conviction)"
    fallback_target = target_80  # Shallower backup target

# Format date range
if near_earnings:
    date_range = f"likely around earnings ({earnings_date})"
else:
    date_range = f"{window_start} - {window_end}"  # e.g., "May 9-23"
```

### 5. Dashboard Publishing (<1 minute)

**HTML output includes:**
- Current signals (BUY/WAIT) with targets and conviction
- Data quality warnings (stale data, model breakage)
- Backtest tracker (hit rate validation)
- Historical signal grid (last 14 days)
- One-liner explanations per stock

**Example output:**
```
NVDA ⏳ WAIT RSI 71
$208.27 (today)
⬇️ $198.96 · May 16-Jun 4 (4.5%)
Conviction: 70%
Moderate 4.5% dip expected (70% conviction). Worth waiting.
└─ Fallback: BUY at $202.88 (2.6% dip, 80% conviction) within 2 weeks
```

---

## Configuration

### Core Settings (`src/config/config.yaml`)

```yaml
simulation:
  paths: 10000                    # Monte Carlo iterations
  horizon_days: 60                # Forecast window
  percentile_primary: 70          # Main target (70% conviction)
  percentile_fallback: 80         # Backup target (80% conviction)
  materiality_threshold: 0.03     # 3% minimum dip to WAIT

volatility:
  garch_order: [1, 1]             # GARCH(1,1) specification
  hmm_states: 2                   # High/low volatility regimes
  lookback_days: 230              # Historical data window
  earnings_spike: 1.5             # Volatility multiplier for earnings

enrichment:
  sentiment_enabled: true         # Toggle AI enrichment
  sentiment_range: [-10, 10]      # Score bounds
  web_search_lookback_days: 30    # Recent news window
  max_tokens_search: 1000         # Web search token limit
  max_tokens_sentiment: 200       # Sentiment token limit

schedule:
  timezone: "Europe/London"       # BST/GMT auto-adjust
  cron: "30 19 * * 1-5"          # 8:30 PM BST Mon-Fri

data_quality:
  max_staleness_days: 10          # Skip if data >10 days old
  min_historical_days: 200        # Require 200+ days for GARCH
```

### Portfolio (`src/config/portfolio.yaml`)

```yaml
stocks:
  - ticker: NVDA
    weight: 0.12
    sector: Core Growth
  - ticker: ASML
    weight: 0.08
    sector: Infrastructure
    currency_display: EUR  # Show EUR on dashboard
  # ... 12 more stocks
```

---

## Dashboard Interpretation

### BUY Signal (Deploy Capital Today)

```
MA 🟢 BUY RSI 47
$504.17 (today)
⬇️ $490.26 · May 9-23 (2.8% — immaterial)
Conviction: 70%
Expected dip only 2.8% — not worth waiting. Buy today.
```

**Meaning:**
- RSI 47 = Neutral momentum (not overbought)
- Predicted dip: 2.8% (below 3% threshold)
- 70% of Monte Carlo paths reach $490 or lower
- **Action:** Buy MA with this month's allocation

### WAIT Signal (Hold Cash, Monitor)

```
AMZN ⏳ WAIT RSI 80
$263.99 (today)
⬇️ $247.98 · May 9-23 (6.1%)
Conviction: 70%
Strong 6.1% dip expected (70% conviction). Be patient.
└─ Fallback: BUY at $254.71 (3.5% dip, 80% conviction) likely around earnings (Apr 29)
```

**Meaning:**
- RSI 80 = Very overbought (due for pullback)
- Predicted dip: 6.1% (exceeds 3% threshold)
- Primary target: $248 (70% conviction)
- Fallback target: $255 (80% conviction, easier to hit)
- **Action:** 
  - Days 1-30: Wait for $248
  - Days 31-45: Accept $255 if $248 missed
  - Days 46-60: Buy at market if both missed

### Data Quality Warnings

```
⚠️ DATA QUALITY WARNINGS (3)
GOOGL: Model $152 vs market $344. Model broken OR stock overvalued. Using analyst targets.
ASML: Model $363 vs market $1246. Model broken OR stock overvalued. Using analyst targets.
CEG: 3 moves over 20% (max 25%). Real volatility OR data corrupted. Check quality.
```

**Interpretation:**
- **DCF breakage:** Growth stocks trade at AI premium, traditional models undervalue → use analyst targets instead
- **Extreme moves:** Power sector (CEG, VST) has binary catalysts (PPA wins) → 20%+ moves are normal

---

## Backtest Validation

### Framework

**Daily archive:**
```python
# signal_history.csv
date,ticker,signal,predicted_dip,predicted_target,current_price,actual_60day_low,hit
2026-04-25,NVDA,WAIT,4.5,198.96,208.27,null,null  # Filled 60 days later
```

**Validation (60 days later):**
```python
actual_low = min(prices[date : date+60])
hit = actual_low <= predicted_target
hit_rate = sum(hits) / len(signals)
```

**Current Status:**
- 3/14 days collected (started Apr 23)
- Need 11 more days for first cohort
- **Target:** Hit rate ≈ 70% (matches percentile)
- **Recalibration:** If hit rate ≠ 70%, adjust percentile

### Expected Outcomes

| Hit Rate | Interpretation | Action |
|----------|---------------|---------|
| 65-75% | Well-calibrated | Keep 70th percentile |
| 75-85% | Too conservative | Lower to 65th (deeper dips) |
| 55-65% | Too aggressive | Raise to 75th (shallower dips) |
| <55% | Model broken | Investigate GARCH/HMM |

---

## Troubleshooting

### Dashboard not updating

```bash
# Check GitHub Actions logs
# Visit: https://github.com/[your-repo]/actions

# Common issues:
1. API key expired → Update GitHub Secrets
2. FMP rate limit → Wait 1 hour or upgrade plan
3. Cron delay → GitHub queue, wait 15-30 min
```

### LDO.MI skipped

```bash
# Issue: Data >10 days old
# Fix: Profile endpoint should provide current price via mcap/shares

# Check logs for:
"💰 LDO.MI: Current price €54.01 (from profile - TODAY'S PRICE)"

# If seeing candle fallback:
"⚠️ LDO.MI: Using last candle price €57.78 (from 2026-04-07, 18 days old)"

# Then profile endpoint failed → check Eulerpool token
```

### High AI costs

```bash
# Current: £10.50/month (optimized)
# Original: £26/month (pre-optimization)

# Further optimize:
1. Reduce tokens: max_tokens_search 1000 → 800 (sentiment.py line 69)
2. Add caching: Skip sentiment if no catalyst (cache 24h)
3. Disable AI: Set sentiment_enabled: false in config.yaml

# ROI check:
# Did sentiment change any actual trades in last 30 days?
# If no → Consider disabling, RSI alone may be sufficient
```

### Timezone display wrong

```bash
# Issue: Dashboard shows UTC instead of BST
# Fix: Ensure pytz installed

pip install pytz>=2024.1

# Check dashboard_generator.py imports:
from pytz import timezone
london_tz = timezone('Europe/London')
```

---

## File Structure

```
sgc-dip-engine/
├── .github/
│   └── workflows/
│       └── daily_run.yml           # GitHub Actions cron (8:30 PM BST)
├── src/
│   ├── main.py                     # Orchestrator (11 min runtime)
│   ├── data_fetcher.py             # API calls (FMP, Eulerpool, Claude)
│   ├── monte_carlo.py              # GARCH + HMM + simulation
│   ├── sentiment.py                # AI enrichment layer
│   ├── execution_logic.py          # Signal generation
│   ├── dashboard_generator.py      # HTML rendering
│   └── config/
│       ├── config.yaml             # System settings
│       └── portfolio.yaml          # 14 stocks + allocations
├── data/
│   └── signal_history.csv          # Backtest archive
├── docs/
│   └── index.html                  # Published dashboard (GitHub Pages)
├── requirements.txt                # Python dependencies
├── .env.example                    # API key template
└── README.md                       # This file
```

---

## Dependencies

```txt
# requirements.txt
pandas>=2.0.0
numpy>=1.24.0
scipy>=1.10.0
requests>=2.28.0
anthropic>=0.18.0
arch>=5.5.0          # GARCH models
hmmlearn>=0.3.0      # Hidden Markov Models
statsmodels>=0.14.0
python-dotenv>=1.0.0
pytz>=2024.1         # Timezone handling
```

---

## API Cost Breakdown

| Service | Usage | Monthly Cost |
|---------|-------|--------------|
| **FMP (Starter)** | 13 stocks × 15 endpoints/day × 22 days | $10 (flat) |
| **Eulerpool** | 1 stock × 8 endpoints/day × 22 days | Included |
| **Claude API** | 13 stocks × 2 calls/day × 22 days @ £0.48/day | £10.50 |
| **FX Rates** | 1 call/day × 22 days | Free |
| **GitHub** | Actions (242 min/month) + Pages | Free |
| **TOTAL** | | **£20.50/month ($26)** |

**Optimization History:**
- Original AI cost: £1.19/day = £26/month
- After token reduction: £0.48/day = £10.50/month (60% savings)
- Potential with caching: £0.18/day = £4/month (85% savings)

---

## Roadmap

### ✅ Completed (v7.0)

- [x] Monte Carlo simulation with GARCH + HMM
- [x] AI enrichment (web search + sentiment)
- [x] Token cost optimization (60% reduction)
- [x] ASML EUR conversion for Trading212
- [x] LDO.MI current price from profile endpoint
- [x] Stale data protection (>10 days → skip)
- [x] Percentile calibration (70th/80th)
- [x] Timezone handling (BST/GMT auto-adjust)
- [x] GitHub Actions automation
- [x] GitHub Pages dashboard

### 🔄 In Progress

- [ ] Backtest validation (3/14 days collected)
- [ ] Hit rate measurement (target: 70%)
- [ ] Post-earnings re-evaluation (May 1)

### 📋 Planned

**Short-term (30 days):**
- [ ] Date range enhancement ("May 9-23" vs "weeks 2-4")
- [ ] Sentiment caching (70% cost reduction)
- [ ] European data reliability monitoring

**Medium-term (90 days):**
- [ ] Adaptive percentile (auto-calibrate from backtest)
- [ ] Extreme volatility investigation (VST, CEG, AVGO)
- [ ] Multi-timeframe targets (30/60/90 day)

**Long-term (12 months):**
- [ ] Regime-aware calibration (VIX-based adjustment)
- [ ] Portfolio-level optimization (correlation-aware deployment)
- [ ] Alternative data sources (reduce FMP dependency)

---

## Contributing

This is a personal project for my SGC portfolio. Not accepting external contributions, but feel free to fork for your own use.

## License

MIT License - Use at your own risk. Not financial advice.

## Contact

- **Author:** Jesse (Aidy)
- **Portfolio:** Smart Growth Compounder (SGC)
- **Dashboard:** https://smartbalanced-sgc.github.io/sgc-dip-engine/

---

**Version:** 7.0 (AI-Enhanced Production + LDO.MI Fix)  
**Last Updated:** April 25, 2026  
**Build Status:** ✅ Operational  
**Next Review:** June 8, 2026 (Post-Backtest Validation)
