# SGC Dip Engine v1

**Smart Growth Compounder — Tactical Entry Timing System**

**Live Dashboard:** https://smartbalanced-sgc.github.io/sgc-dip-engine/  
**Schedule:** Mon–Fri at 8:30 PM BST (auto-runs via GitHub Actions)  
**Runtime:** ~2.5 minutes per run on cycles with no catalyst

---

## What It Does

The engine answers one question every trading day: **"Should I buy today, or wait for a better dip within the next 60 days?"**

It runs 10,000 Monte Carlo simulations per stock, models correlated portfolio-level price paths using GARCH volatility and HMM regime detection, and generates a BUY or WAIT signal for each of the 14 portfolio holdings. The dashboard shows the predicted dip entry price, the expected recovery rally target, and a plain-English one-liner explaining the signal.

The system is designed for a fixed DCA investing approach — it does not pick stocks, change position sizes, or recommend selling. It only optimises the timing of monthly capital deployment.

---

## Portfolio

14 stocks, £39,090 target allocation, 13.47% weighted CAGR (Portfolio Constitution v7):

| Segment | Tickers | Allocation |
|---|---|---|
| Core Growth | NVDA, MSFT, GOOGL, META, AMZN | 47% |
| Infrastructure | AVGO, ASML, MU | 22% |
| Power | CEG, VST | 7% |
| Resilience | MA, CTAS, LDO.MI, WM | 24% |

The portfolio composition is fully config-driven. Changing tickers or weights requires only editing `src/config/config.yaml` — no code changes needed for US stocks. Adding a European Eulerpool stock requires adding it to `yfinance_tickers` in config. Adding another EUR-denominated ADR requires adding it to `eur_display_tickers` in config.

---

## How to Read the Dashboard

Each stock card shows:

```
NVDA ⏳ WAIT  RSI 71
$208.27 (today)
⬇️ $198.57 · May 14-28 (4.7%)        ← predicted dip entry, 70% conviction
⬆️ $227.38 · May 07-21 (+9.2% rally, 60% conviction)  ← expected recovery peak
Conviction: 70%
Moderate 4.7% dip expected (70% conviction). Worth waiting.
└─ Fallback: BUY NOW (2.7% dip, 80% conviction) Apr 24-May 08
```

```
MA 🟢 BUY  RSI 47
$504.17 (today)
⬇️ $489.68 · May 10-24 (2.9% — immaterial)
⬆️ $531.62 · Apr 27-May 11 (+5.4% rally, 60% conviction)
Conviction: 70%
Expected dip only 2.9% — not worth waiting. Buy today.
```

**Dip line (⬇️):** The price 70% of Monte Carlo paths reach or go below within 60 days. If the predicted dip is less than 3%, it's classed as immaterial and the signal is BUY.

**Rally line (⬆️):** The price 60% of paths reach or go above within 60 days. This is the expected recovery ceiling, not a sell signal — the portfolio is buy-and-hold.

**Fallback line:** If the primary dip target is missed, the 80% conviction fallback gives a shallower but more certain alternative entry level.

**Conviction percentiles:** Both thresholds are adjustable in `config.yaml` — `percentile_target` (dip) and `rally_conviction_percentile` (rally).

---

## Architecture

The engine runs as a sequential pipeline on GitHub Actions every weekday evening:

```
Data Fetch → Gate 1 Validate → Regime Detection → Gate 2 Validate
→ Catalyst Detection → AI Intelligence → Correlation Matrix
→ Monte Carlo → Gate 3 Validate → Signal Generation
→ Gate 4 Validate → Dashboard → Archive
```

### Step 1: Data Fetching

**US stocks (13) — FMP API:**

Each stock fetches up to 15 endpoints including 230 days of OHLCV history, current price and RSI, analyst price targets and consensus, next earnings date, forward EPS estimates, analyst grade history, insider transaction statistics, momentum (1M/3M/6M), DCF estimate, and financial quality scores.

Two macro endpoints fetch the current VIX level and SPY price for regime classification.

**LDO.MI — Eulerpool API:**

Leonardo SpA (Italian defence, Milan exchange) is not available on FMP. All data comes from Eulerpool:

- **Current price:** Calculated as `market_cap ÷ shares_outstanding` from `/equity/profile`. This is always fresh (live exchange data), even when historical candles lag.
- **Historical OHLC:** `/equity/candles` (230 days). May lag by days/weeks for low-liquidity European stocks.
- **Enrichment:** Analyst targets, estimates, grades, beta, insider activity, AAQS quality score.

Because Eulerpool candles do not include volume data, the volume validator is skipped for this stock.

**ASML EUR conversion:**

FMP provides ASML as a USD-listed ADR. The engine converts the current price, full historical OHLC, and analyst targets to EUR using a live FX rate (exchangerate-api.com) before Monte Carlo runs. This ensures the simulation and all targets are EUR-native. The conversion applies to any ticker in the `eur_display_tickers` config list — adding another EUR ADR only requires updating that list.

### Step 2: Gate 1 Validation

Four data quality checks per stock before any modelling runs:

- **Freshness:** If candle data is older than 10 days and no fresh current price is available from a profile endpoint, the stock is skipped. Skipped stocks appear in the warning banner with an explanation.
- **History depth:** Minimum 50 rows to run at all; minimum 200 rows for full GARCH precision.
- **Returns outliers:** Any single-day return above 20% is flagged. This is data-quality-neutral for power sector stocks (binary catalysts are expected) but flagged for transparency.
- **DCF sanity:** If the DCF model price is less than 50% or more than 250% of market price, it is discarded and analyst consensus used instead. Growth stocks consistently trigger this — it is normal and explained in the warning banner.

### Step 3: Regime Detection

**Macro regime (portfolio-wide):** VIX level and SPY price classify the market as risk-on, neutral, or risk-off. This adjusts the drift and volatility multipliers applied to the entire simulation batch.

**Per-stock regime (HMM):** A two-state Hidden Markov Model classifies each stock as currently in a high-volatility or low-volatility regime based on its recent return history. This adjusts the per-stock volatility input to GARCH.

### Step 4: AI Intelligence (Trigger-Based)

This is the most important section to understand. The engine does **not** run AI on every stock every day. That approach was evaluated and found to cost £5/run while producing negligible signal impact (sentiment modifying drift by ±0.82% against 7%+ daily noise). It was replaced with a trigger-based architecture.

**Layer 0 — Free structural enrichment (runs every stock, every day, zero AI cost):**

Analyst disagreement (the spread between analyst high and low price targets) is used as an uncertainty proxy. Wide spread → wider volatility distribution in Monte Carlo. This is calculated from already-fetched FMP data.

Earnings proximity applies a configurable volatility multiplier automatically: 1.5× within 14 days of earnings, 1.3× within 30 days, 1.15× within 60 days. On the earnings day itself, the time-varying vol schedule spikes to 3×.

Insider activity (net buying vs selling ratio) applies a small drift bias using the same already-fetched FMP data.

**Layer 1 — Catalyst-triggered AI (no web search, structured data only):**

AI fires only when one of three triggers is detected:

| Trigger | Condition | What AI does | Cost |
|---|---|---|---|
| Post-earnings | Stock reported within last 3 days | Classifies vol regime as HIGH/MEDIUM/LOW for next 30 days. Monte Carlo widens or narrows distribution by 25–30%. Dashboard shows `⚡ AI: Vol HIGH 🔴` badge. | ~£0.002 |
| Unusual move | Beta-adjusted residual Z-score > 2.5 | Classifies the move as MACRO_CONTAGION / COMPETITOR_EVENT / THESIS_RISK / TECHNICAL. If THESIS_RISK, vol is elevated. | ~£0.002 |
| BUY prioritisation | 2+ BUY signals same day | Ranks deployment order using insider activity, analyst trend, momentum, earnings proximity. | ~£0.002 |

The Z-score trigger is beta-adjusted: a 3% move in NVDA (beta 1.8) requires the same trigger threshold as a 1.7% move in WM (beta 0.7). Raw percentage moves would fire constantly on high-beta stocks during normal market-wide moves.

**Layer 2 — Emergency web search (rare):**

If a stock shows a residual Z-score above 3.0 with no visible FMP explanation (no earnings, no analyst grade change, no insider event), one targeted web search fires to diagnose the cause. This protects against deploying capital into thesis-breaking news that FMP hasn't captured yet. Expected frequency: 2–3 times per month across the entire portfolio.

**Why AI modifies volatility, not drift:**

The engine answers "will the stock be lower at some point in the next 60 days?" — a question about the left tail of the return distribution, not expected return. Drift adjustment (what sentiment scoring does) shifts where all 10,000 paths end up on average. Volatility adjustment changes how wide the distribution is, which directly changes dip depth predictions. A 30% vol regime change moves dip targets by 5–15%, which can flip a BUY/WAIT signal. A drift adjustment of ±5% produces less than 1% change in dip targets.

**Cost:**

On a typical quiet day (no catalysts): **£0.00**. On an earnings week (4–6 post-earnings calls): ~£0.01–0.02. Emergency search: ~£0.08. Monthly total: **£0.20–0.50**, down from £110/month under the previous blanket-search approach.

### Step 5: Monte Carlo Simulation

For each stock, the engine runs 10,000 correlated price path simulations over 60 trading days.

**Volatility input:** GARCH(1,1) fitted on the stock's historical returns. GARCH captures volatility clustering — the tendency of large moves to follow large moves. The estimate is then adjusted by: regime multiplier (from HMM), macro multiplier (from VIX regime), earnings proximity multiplier, analyst spread multiplier, and AI vol regime output (if a catalyst triggered).

**Drift input:** A small positive drift per period (reflecting expected long-term compounding) adjusted by: RSI (overbought stocks pull drift negative), momentum (contrarian — strong recent rallies slightly reduce expected near-term drift), and insider activity (net buying adds a small positive bias).

**Correlation:** A full 14×14 correlation matrix is built from 60-day rolling returns. This is decomposed via Cholesky factorisation to generate correlated random shocks for all 14 stocks simultaneously. The portfolio doesn't simulate each stock independently — they move together as they do in reality.

**Time-varying vol schedule:** Rather than applying a uniform earnings multiplier across all 60 days, the schedule concentrates the vol spike on and around the actual earnings announcement day. The day itself gets 3× base vol, the two days either side get 1.5×, and all other days run at base vol.

**Output extraction:** From the 10,000 path minimums:
- 70th percentile minimum → primary dip target (70% of paths reach this or lower)
- 80th percentile minimum → fallback dip target (80% conviction, shallower)

From the 10,000 path maximums:
- `(100 - rally_conviction_percentile)`th percentile maximum → rally target (default 60% conviction = 40th percentile of maximums, meaning 60% of paths reach this high or higher)

Both percentile thresholds are adjustable in `config.yaml`.

### Step 6: Signal Generation

```
IF predicted_dip < 3%:
    → BUY (immaterial dip, not worth waiting)
ELSE:
    → WAIT (meaningful dip predicted)
    Show fallback target at 80th percentile
```

The 3% materiality threshold is configurable in `config.yaml` (`min_actionable_dip_pct`). On typical £400–500 monthly position sizes, a sub-3% dip saves less than £15 and is not worth the risk of missing a rally.

Date ranges are shown as actual calendar dates (e.g. "May 14–28") anchored to the median day across all 10,000 paths. If the predicted dip aligns within 3 days of an earnings date or major macro event, the display shows "likely around earnings (May 29)" instead.

### Step 7: Dashboard and Archiving

The dashboard is a static HTML file published to GitHub Pages. It refreshes every run and includes:

- Warning banner (collapsed by default) with data quality issues
- Backtest tracker showing how many days of signal history have been collected
- Summary header listing BUY and WAIT stocks
- Per-stock signal cards with dip target, rally target, RSI, conviction, one-liner, fallback
- AI catalyst badge on stocks where AI fired (e.g. `⚡ AI: Vol HIGH 🔴 — Cloud revenue miss on Azure guidance`)

Each run appends to `data/signal_history.csv` for backtest validation.

---

## Backtest Validation

The engine records every WAIT signal with its predicted dip target and predicted date. 60 trading days later, it checks whether the actual price reached the predicted target. The hit rate should approximate the percentile setting (a 70th percentile target should be reached by roughly 70% of paths in reality). If the hit rate diverges materially, the percentile is recalibrated.

**Current status:** 3/14 days collected (first cohort needs 14 days). First validation expected early June 2026.

| Hit Rate | Interpretation | Recalibration |
|---|---|---|
| 65–75% | Well-calibrated | Keep at 70th percentile |
| 75–85% | Dips too shallow (too aggressive) | Lower to 65th |
| 55–65% | Dips too deep (too conservative) | Raise to 75th |
| <55% | Model issue | Investigate GARCH/HMM inputs |

---

## Configuration Reference

All behavioural parameters live in `src/config/config.yaml`. No Python file changes are needed for routine tuning.

```yaml
# --- Signal thresholds ---
signal:
  percentile_target: 70          # Dip conviction (70% of paths reach this low or lower)
  fallback_percentile: 80        # Fallback dip conviction
  min_actionable_dip_pct: 0.03   # Minimum dip to WAIT (3%)
  rally_conviction_percentile: 60 # Rally conviction (60% of paths reach this high or higher)
  min_actionable_rally_pct: 0.01  # Minimum rally to display on dashboard (1%)

# --- Monte Carlo ---
monte_carlo:
  num_paths: 10000               # Simulation iterations
  simulation_days: 60            # Forecast window
  lookback_days: 730             # Historical data for GARCH fitting

# --- Earnings vol schedule ---
volatility_schedule:
  enabled: true
  earnings_day_multiplier: 3.0   # Vol on the earnings day itself
  earnings_pre_post_multiplier: 1.5  # Vol on ±2 days around earnings
  macro_event_multiplier: 2.0    # Vol on FOMC/CPI days

# --- Data sources ---
data:
  yfinance_tickers: [LDO.MI]     # European stocks → Eulerpool fetcher
  eur_display_tickers: [ASML]    # USD ADRs that display in EUR
  power_sector_tickers: [CEG, VST]  # Stocks with binary catalyst outliers

# --- Enrichment coefficients ---
enrichment:
  rsi_coefficient: 500           # (50 - RSI) / 500 → drift modifier
  momentum_coefficient: 1000     # -momentum_1M / 1000 → drift modifier
  insider_coefficient: 25        # (ratio - 0.5) / 25 → drift modifier, capped ±0.03
  max_total_drift: 0.10          # Cap on total enrichment drift

# --- Validation thresholds ---
validation:
  gate1:
    hist_max_stale_days: 5       # Warn if data older than 5 days
    hist_min_rows_skip: 50       # Skip if fewer than 50 rows
    return_outlier_pct: 0.20     # Flag single-day moves >20%
```

---

## Portfolio Agnosticism

The engine is designed so that changing the portfolio requires only config edits, not code changes, with two caveats:

**What only needs config changes:**
- Add or remove any US stock: add/remove from `tickers` and `weights` in config.yaml
- Add another European Eulerpool stock: add to `yfinance_tickers` list
- Add another EUR-denominated USD ADR: add to `eur_display_tickers` list
- Add/remove power sector stocks: update `power_sector_tickers`
- Change allocation weights: update `weights`

**What requires code changes:**
- A stock on a data source other than FMP or Eulerpool (new data fetcher needed)
- A non-EUR/non-USD currency with FX conversion requirements (extend the EUR conversion logic)

---

## Running Locally

```bash
git clone https://github.com/smartbalanced-sgc/sgc-dip-engine.git
cd sgc-dip-engine

pip install -r requirements.txt

# Create .env with API keys
cp .env.example .env
# Edit .env:
# FMP_API_KEY=your_key
# EULERPOOL_TOKEN=your_token
# ANTHROPIC_API_KEY=your_key

cd src
python main.py
```

---

## GitHub Actions Setup

1. Add repository secrets: `FMP_API_KEY`, `EULERPOOL_TOKEN`, `ANTHROPIC_API_KEY`
2. Enable GitHub Pages: Settings → Pages → Source: `main` branch, `/docs` folder
3. The workflow in `.github/workflows/daily_run.yml` runs automatically at 19:30 UTC (8:30 PM BST) Mon–Fri

Manual trigger: Actions tab → "SGC Dip Engine - Daily Run" → Run workflow

---

## File Structure

```
sgc-dip-engine/
├── .github/workflows/
│   └── daily_run.yml          # Cron schedule and GitHub Actions pipeline
├── src/
│   ├── main.py                # Orchestrator: runs all 8 pipeline steps
│   ├── data_fetcher.py        # FMP + Eulerpool data fetching, EUR conversion
│   ├── garch_model.py         # GARCH(1,1) volatility estimation
│   ├── hmm_regime.py          # HMM per-stock regime classification
│   ├── macro_regime.py        # VIX/SPY macro regime classification
│   ├── correlation.py         # Portfolio correlation matrix + Cholesky
│   ├── monte_carlo.py         # Simulation engine, enrichment, statistics
│   ├── sentiment.py           # Trigger-based AI intelligence layer
│   ├── execution_logic.py     # BUY/WAIT signal generation + date formatting
│   ├── dashboard_generator.py # HTML dashboard rendering
│   ├── validators.py          # Four-gate data quality pipeline
│   ├── signal_archiver.py     # Writes signal_history.csv for backtest
│   ├── backtest.py            # Hit rate calculation against actuals
│   ├── config.py              # Exports all constants from config.yaml
│   ├── config_loader.py       # YAML loading utility
│   └── config/
│       └── config.yaml        # All tunable parameters (edit here, not in Python)
├── data/
│   └── signal_history.csv     # Backtest archive (WAIT signals + actuals)
├── docs/
│   └── index.html             # Published dashboard (GitHub Pages)
├── requirements.txt
├── .env.example
└── README.md
```

---

## Dependencies

```
pandas>=2.0.0
numpy>=1.24.0
scipy>=1.10.0
requests>=2.28.0
anthropic>=0.18.0
arch>=5.5.0          # GARCH(1,1)
hmmlearn>=0.3.0      # Hidden Markov Models
statsmodels>=0.14.0
python-dotenv>=1.0.0
pytz>=2024.1
```

---

## API Costs

| Service | Plan | Monthly Cost |
|---|---|---|
| FMP (Financial Modeling Prep) | Starter ($10/month flat) | $10 |
| Eulerpool | Included in subscription | — |
| Anthropic Claude API | Pay-per-use, trigger-based | £0.20–0.50 |
| exchangerate-api.com (FX) | Free tier | £0 |
| GitHub Actions + Pages | Free tier | £0 |
| **Total** | | **~£8–9/month** |

The AI cost was previously £110/month (blanket daily web search on all 13 stocks). The current trigger-based architecture fires 0–6 targeted calls per day using already-fetched structured data, with web search reserved for genuine emergencies (3-sigma unexplained moves). On most days the AI cost is £0.

---

## Troubleshooting

**Dashboard not updating:**
Check GitHub Actions logs. Common causes: API key expired (update Secrets), FMP rate limit hit (wait 1 hour), GitHub Actions queue delay (up to 30 min at peak times).

**LDO.MI skipped:**
The profile endpoint `eulerpool_get()` returns the current price via market cap ÷ shares. If this fails (auth error, network), the candle price is used as fallback and the staleness check may skip the stock. Check that `EULERPOOL_TOKEN` is set correctly in GitHub Secrets and that the token has not expired.

**ASML showing rally instead of dip:**
All ASML prices (current, historical OHLC, analyst targets) must be converted to EUR before Monte Carlo runs. If the EUR conversion fails silently (FX rate API down), the simulation runs on USD prices but displays EUR symbols, inverting the dip/rally direction. Check FX rate fetch logs.

**All stocks showing WAIT:**
This is normal behaviour during earnings clusters (when most portfolio stocks report within a short window) or when average RSI is high across the portfolio. The warning banner will note if all stocks show WAIT. Check earnings dates — if most stocks report within the next 2 weeks, universal WAIT is expected and correct.

---

## Disclaimer

Not financial advice. This system is a personal tool for entry timing optimisation within a pre-defined buy-and-hold DCA strategy. All signals are probabilistic, not deterministic. Past backtest performance does not guarantee future hit rates. Always apply your own judgement before deploying capital.

---

**Version:** 1.0  
**Last Updated:** April 25, 2026  
**Dashboard:** https://smartbalanced-sgc.github.io/sgc-dip-engine/
