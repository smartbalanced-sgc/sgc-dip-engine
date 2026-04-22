# SGC Dip Engine v6

**Purpose:** Daily BUY/WAIT signal generator for a 14-stock DCA portfolio. Tells the investor whether today's price is the best entry or if a lower price is more likely than not within 60 days.

**Live Dashboard:** https://smartbalanced-sgc.github.io/sgc-dip-engine/
**Schedule:** 9:30 PM BST weekdays (30 min after US market close)

---

## What This Is

A Monte Carlo simulation engine that runs 10,000 correlated price paths per stock over a 60-day horizon. It combines GARCH volatility forecasting, regime detection, analyst consensus, RSI, insider activity, and AI sentiment to answer one question per stock:

> "Should I buy today, or is a lower price more likely than not in the next 60 days?"

The investor DCA's into these 14 stocks monthly regardless. The stocks are already chosen by the Portfolio Constitution. This engine optimises *entry timing*, not *stock selection*.

## What This Is NOT

- **Not a stock picker.** It does not evaluate whether a stock deserves to be in the portfolio.
- **Not a trading bot.** It generates signals on a static HTML page. The investor decides and executes manually.
- **Not a guarantee.** It outputs probabilities, not predictions. A 68% dip confidence means 32% of the time, the dip doesn't come.
- **Not backtested.** As of initial deployment, the model has zero historical track record. Calibration will come from observing live output vs actual prices over the first 2-4 weeks.
- **Not a substitute for judgment.** The dashboard shows warnings when data quality is suspect. The investor should apply common sense, especially after earnings events and during market crashes.

---

## Seven-Layer Architecture

```
Layer 0  MACRO REGIME
         FMP quote for VIX + SPY → bull/neutral/stress classification.
         Adjusts volatility and correlation multipliers for all stocks.

Layer 1  DATA INGESTION
         FMP API (15 endpoints per US stock) + yfinance (LDO.MI only).
         Fetches: OHLCV, price, targets, earnings, grades, momentum,
         forward EPS, target trends, RSI, beta, financial scores,
         grades consensus, DCF intrinsic value, insider trading stats.
         Also: economic calendar (Fed/CPI/jobs dates, 1 call total).

Layer 2  STATISTICAL ENGINE
         GARCH(1,1) → per-stock forward volatility estimate.
         Simplified HMM → per-stock regime (bull/sideways/drawdown).

Layer 3  CORRELATION
         Correlation matrix from 2yr daily returns.
         Cholesky decomposition → correlated random numbers.
         Stocks simulated jointly, not independently.

Layer 4  MONTE CARLO SIMULATION
         10,000 correlated price paths × 60 days per stock.
         Drift = regime adjustment + mean reversion toward analyst target.
         Volatility = GARCH × stock regime × macro regime multiplier.
         Output: 50th percentile of path minimums = "most likely low".

Layer 5  AI INTELLIGENCE
         Claude API sentiment per stock.
         Cross-validates with analyst grade actions.

Layer 6  EXECUTION LOGIC
         Signal: BUY if P(dip) < 50%, else WAIT.
         Materiality gate: if expected dip < 3%, signal BUY regardless.
         Auto-generated one-liner explanations with confidence %.

Layer 7  DASHBOARD
         Static HTML to GitHub Pages. Dark theme. Warning banner
         if any guardrails were tripped during the run.
```

---

## Four-Gate Validation Pipeline

Every run passes through four validation gates. If data is corrupt or model outputs are nonsensical, the dashboard shows warnings rather than false-precision signals.

### Gate 1: Input Data Quality (after fetch)
- Price must be positive and non-NaN
- Price cross-check: quote vs last historical close (< 3% divergence)
- Historical data: warn < 200 rows, skip < 50 rows
- Freshness: last data point within 5 trading days
- Single-day return outlier scan (> 20% flagged as possible split/error)
- Volume: mean daily > 100,000
- Analyst target: must be 0.5x–2.5x current price, else fallback
- NaN check on all numeric fields
- **Action:** Skip stock on critical fail. Warn on soft fail.

### Gate 2: Model Output Sanity (after GARCH/HMM)
- GARCH annualized vol > 150% → stock marked "unmodelable"
- GARCH stationarity: alpha + beta > 0.95 → warn (near unit root)
- Mean reversion anchor: must be 0.5x–2.5x current price, else fallback to MA50 or disable
- Correlation matrix: no off-diagonal > 0.98
- NaN check on volatility, regime, anchor
- **Action:** Degrade gracefully. Never clamp data and pretend it's real.

### Gate 3: Simulation Output Sanity (after MC)
- Individual paths are NOT capped (fat tails are intentional features)
- If 50th percentile minimum > 30% below current → flag as "extreme" (not clamp)
- If dip target ≥ current price → "no dip expected", signal BUY
- Confidence must be [0.0, 1.0]
- NaN check on all MC outputs
- **Action:** Flag extremes for dashboard display. Do not modify distributions.

### Gate 4: Portfolio-Level Coherence (after signals)
- VIX < 5 or > 80 → flag data suspect
- All stocks same signal → flag (possible macro data issue)
- Fewer than 10 valid stocks → degraded dashboard with warning
- 0 valid stocks → error page, do not publish empty grid
- **Action:** Populate warnings list for dashboard banner.

---

## Signal Logic

**BUY** is generated when any of these conditions is true (checked in order):
1. No dip expected (simulated median minimum ≥ current price)
2. Dip is immaterial (expected dip < 3% — not worth waiting for)
3. Price already at or below simulated target (within 1%)
4. Dip confidence < 50% (dip is less likely than not)

**WAIT** is generated when:
- Expected dip ≥ 3%, and confidence ≥ 50%

**Confidence** = fraction of 10,000 MC paths where price reaches the median low or lower at some point in 60 days. 68% confidence on $110.80 means: in 6,800 of 10,000 paths, the stock hit $110.80 or lower.

---

## Data Sources

### FMP API (13 US stocks — 15 endpoints per stock)
| # | Endpoint | Returns | Used For |
|---|----------|---------|----------|
| 1 | `historical-price-eod/full` | 2yr OHLCV | GARCH, HMM, correlation, RSI fallback |
| 2 | `quote` | Price, MA50, MA200 | Current price, fallback anchor |
| 3 | `price-target-consensus` | Analyst high/low/median/consensus | MC mean reversion anchor |
| 4 | `earnings` | Earnings dates + actuals | Sentiment context |
| 5 | `grades` | Latest analyst grade action | Sentiment modifier |
| 6 | `stock-price-change` | 1M/3M/6M momentum | Future: MC drift modifier |
| 7 | `analyst-estimates` | Forward EPS, analyst count | Future: anchor confidence |
| 8 | `price-target-summary` | Monthly/quarterly target trends | Future: anchor direction |
| 9 | `technical-indicators/rsi` | RSI(14) | Dashboard display, future: MC drift |
| 10 | `profile` | Beta, sector | Future: vol calibration |
| 11 | `financial-scores` | Altman Z, Piotroski | Future: distress filter |
| 12 | `grades-consensus` | Buy/hold/sell counts | Future: sentiment enrichment |
| 13 | `discounted-cash-flow` | DCF intrinsic value | Future: second anchor |
| 14 | `insider-trading/statistics` | Net buy/sell activity | Future: MC drift modifier |
| 15 | `economic-calendar` | Fed/CPI/jobs dates (1 call) | Future: macro vol spike |

### yfinance (LDO.MI only — 2 calls)
FMP returns HTTP 402 for European tickers. yfinance provides OHLCV and current price for Leonardo (Milan exchange) in EUR.

### Anthropic Claude API (sentiment)
Per-stock sentiment scoring (-5 to +5) with one-sentence narrative. Cross-validates against analyst grade actions.

---

## Known Weaknesses & Honest Limitations

### 1. Post-Earnings Anchor Staleness
After a stock reports earnings and gaps down significantly, analyst targets take days/weeks to update. The model's mean reversion anchor still points to pre-earnings levels, creating a false "undervalued" signal. **Mitigation:** The investor should apply judgment after earnings events and not rely solely on the signal.

### 2. No Time-Decay / Deployment Urgency
The model treats day 1 and day 59 of the window identically. If the investor hasn't bought by late in the month, the model doesn't bias toward BUY to ensure capital deployment. **Planned enhancement** for a future version.

### 3. No Backtest
The model has never been validated against historical data. After 2-4 weeks of live signals, the 14-day signal grid will reveal whether WAIT signals actually preceded dips. If they didn't, the model needs recalibration.

### 4. Sentiment Is Computed But Not Fed Back Into MC
Claude API scores are generated but do not currently modify the Monte Carlo simulation. They appear in logs but not in the signal calculation. **Planned enhancement.**

### 5. Fetched Data Not Yet Wired
RSI, beta, momentum, forward estimates, insider stats, DCF, and economic calendar are fetched and displayed but do not yet modify the MC simulation. These are Phase 2 enhancements. They are fetched now to establish the data pipeline and to display on the dashboard.

### 6. Conflicting Anchors
When analyst target and DCF disagree on direction, the model currently uses analyst target only. A weighted resolution strategy is a planned enhancement.

---

## Portfolio (Constitution v7)

| Ticker | Weight | Role | Block |
|--------|--------|------|-------|
| NVDA | 13% | Core Growth | T1 |
| MSFT | 13% | Core Growth | T1 |
| GOOGL | 13% | Core Growth | T1 |
| META | 5% | Core Growth | T1 |
| AMZN | 3% | Core Growth | T1 |
| AVGO | 10% | Infrastructure | T2 |
| ASML | 8% | Infrastructure | T2 |
| MU | 4% | Infrastructure | T2 |
| CEG | 4% | Power | P |
| VST | 3% | Power | P |
| MA | 10% | Resilience | D |
| CTAS | 5% | Resilience | D |
| LDO.MI | 5% | Resilience | D |
| WM | 4% | Resilience | D |

---

## File Structure

```
sgc-dip-engine/
├── .github/workflows/
│   └── daily_run.yml        # Cron: 9:30 PM BST weekdays
├── docs/
│   └── index.html           # Dashboard output (GitHub Pages)
├── src/
│   ├── config.py            # All thresholds, portfolio, API config
│   ├── validators.py        # 4-gate validation pipeline
│   ├── data_fetcher.py      # FMP (15 endpoints) + yfinance (LDO.MI)
│   ├── macro_regime.py      # VIX/SPY → risk_on/neutral/risk_off
│   ├── garch_model.py       # GARCH(1,1) volatility forecasting
│   ├── hmm_regime.py        # Simplified regime detection
│   ├── correlation.py       # Correlation matrix + Cholesky
│   ├── monte_carlo.py       # 10K correlated MC paths
│   ├── sentiment.py         # Claude API sentiment per stock
│   ├── execution_logic.py   # BUY/WAIT signals + materiality gate
│   ├── dashboard_generator.py # HTML output with warning banner
│   └── main.py              # Orchestrator (7 steps + 4 gates)
├── requirements.txt
├── .gitignore
└── README.md
```

---

## How To Run

### Local (Mac terminal)
```bash
cd src
pip install -r ../requirements.txt
export FMP_API_KEY="your_key"
export ANTHROPIC_API_KEY="your_key"
python main.py
# Dashboard output: ../docs/index.html
```

### GitHub Actions (automated)
Runs daily at 9:30 PM BST via `.github/workflows/daily_run.yml`.
Secrets required: `FMP_API_KEY`, `ANTHROPIC_API_KEY`.
Output pushed to `docs/index.html` → GitHub Pages serves automatically.

---

## Defense of the Approach

### Why Monte Carlo, not rules-based?
Rules-based ("buy when RSI < 30") are brittle and backward-looking. MC simulation incorporates volatility clustering (GARCH), regime shifts (HMM), cross-stock correlation (Cholesky), and mean reversion (analyst targets) simultaneously. The output is a probability distribution, not a binary rule.

### Why 50th percentile, not 5th or 95th?
The 50th percentile of minimums = "most likely low." The 5th percentile would be "extreme crash" territory — too pessimistic for DCA timing. The 95th would be too optimistic. The median strikes the balance: "what's the dip you'd bet on?"

### Why 60 days, not 30 or 90?
30 days is too short — misses mean reversion cycles. 90 days is too long — the investor needs to deploy capital monthly, and 90-day forecasts have too much uncertainty. 60 days covers two monthly DCA windows, giving enough time for dips to materialise without excessive uncertainty.

### Why materiality threshold?
A model that tells you to wait for a 1.5% dip on a $230 stock is noise. On a £500 monthly DCA contribution, 1.5% saves £7.50 — not worth the risk of missing a run-up. The 3% threshold ensures signals are actionable.

### Why not clamp Monte Carlo paths?
Clamping suppresses tail risk, which is exactly what causes real dips. If NVDA has beta 2.3 and the market drops 7%, a 16% single-day move is plausible. Capping paths at ±15% would systematically underestimate dip probability — the opposite of what the investor wants. Fat tails are features, not bugs.

### Why degrade instead of fabricate?
If GARCH returns 300% annualized vol, clamping to 200% and running MC produces a signal that looks legitimate but is based on fabricated input. The investor might act on it. Instead, marking the stock "unmodelable" and showing ⚠️ on the dashboard tells the investor to use judgment for that stock today. Honesty over false precision.

---

## Enhancement Roadmap (Post-Deployment)

| Phase | Enhancement | Impact |
|-------|------------|--------|
| 2a | Wire momentum into MC drift | Suppress dip probability when momentum is strong positive |
| 2b | Wire RSI into MC drift | Overbought → increase dip probability |
| 2c | Wire insider stats into MC drift | Heavy selling → increase dip probability |
| 2d | Wire earnings date into MC vol spike | Widen σ around earnings window |
| 2e | Wire economic calendar into MC vol spike | Widen σ around Fed/CPI dates |
| 2f | Wire DCF as second anchor | Weighted resolution when analysts and DCF disagree |
| 2g | Wire sentiment score into MC drift | Bearish Claude score → increase dip probability |
| 2h | Enrich Claude prompt with all new data | RSI, momentum, insider, grades consensus, target trends |
| 3a | Post-earnings anchor suppression | Disable mean reversion for 5 days after major earnings |
| 3b | Time-decay deployment urgency | BUY threshold increases as month progresses |
| 3c | Backtest scaffold | Compare 14-day historical signals vs actual outcomes |
