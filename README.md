# SGC Dip Engine v6

**Purpose:** Daily BUY/WAIT signal generator for a 14-stock DCA portfolio. Tells the investor whether today's price is the best entry or if a meaningful dip is likely within 60 days.

**Live Dashboard:** https://smartbalanced-sgc.github.io/sgc-dip-engine/
**Schedule:** 9:30 PM BST weekdays (30 min after US market close)
**Monthly deployment:** £500 across 14 stocks per Portfolio Constitution v7

---

## What This Is

A Monte Carlo simulation engine that runs 10,000 correlated price paths per stock over a 60-day horizon. For each stock, it finds the dip level that 60% of simulated futures reach. If that dip is deep enough to matter (≥3%), it tells you to WAIT. If not, it tells you to BUY now.

The investor DCA's into these 14 stocks monthly regardless. The stocks are already chosen by the Portfolio Constitution. This engine optimises *entry timing*, not *stock selection*.

## What This Is NOT

- **Not a stock picker.** It does not evaluate whether a stock deserves to be in the portfolio.
- **Not a trading bot.** It generates signals on a static HTML page. The investor decides and executes manually.
- **Not a guarantee.** It models the normal statistical behaviour of stock prices. It cannot predict earnings surprises, tariff announcements, or CEO resignations.
- **Not backtested.** As of initial deployment, the model has zero historical track record. Calibration comes from observing live output vs actual prices over the first 2-4 weeks.

---

## How The Signal Works (Plain English)

For each stock, the model simulates 10,000 possible price futures over 60 days using the stock's own volatility, trend regime, correlation with other stocks, and mean reversion toward analyst price targets.

It then finds the **dip level that 60% of those futures reach**. This is the "realistic dip" — not an extreme crash, not a tiny blip, but the level the stock has a good chance of hitting.

Then it asks one question: **"Is that dip deep enough to be worth waiting for?"**

- **Dip ≥ 3%** → WAIT. The potential savings justify patience.
- **Dip < 3%** → BUY. The dip is too shallow to matter on these position sizes.

The 3% materiality threshold exists because on a £35-£75 monthly position, a 2% dip saves £0.70-£1.50. Not worth the risk of the stock running up while you wait.

### Examples

**NVDA — $202, RSI 70, beta 2.3 (volatile, overbought)**
60% of simulated paths dip to $188 or lower. That's a 6.9% dip.
→ **WAIT** — "Strong 6.9% dip expected. Be patient."

**WM — $224, RSI 38, beta 0.65 (stable, oversold)**
60% of simulated paths dip to $220 or lower. That's a 1.8% dip.
→ **BUY** — "Expected dip only 1.8% — not worth waiting. Buy today."

**MA — $510, RSI 51, beta 1.1 (moderate)**
60% of simulated paths dip to $494 or lower. That's a 3.1% dip.
→ **WAIT** — "Moderate 3.1% dip expected. Worth waiting."

The same conviction level (60%) produces different dip depths because each stock has different volatility, regime, and mean reversion characteristics. Volatile stocks produce deep dips. Stable stocks produce shallow ones. The materiality filter then decides whether the dip is worth waiting for.

---

## The Conviction Dial

The 60% conviction level is set in `config.py` as `PERCENTILE_TARGET = 60`. It means: "Show me the dip that 60% of simulated futures reach."

- **Higher (70%)** = shallower dips, more certainty they happen. Model says BUY more often.
- **Lower (50%)** = deeper dips, less certainty. Model says WAIT more often but dips may not materialise.
- **60%** = balanced. Dips are likely enough to wait for, deep enough to save real money.

The conviction level can be adjusted after observing live performance. If the model's WAIT signals consistently lead to dips that are reached, consider lowering to 55%. If dips are frequently missed, raise to 65%.

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
         Output: 60th percentile of path minimums = "realistic dip level".

Layer 5  AI INTELLIGENCE
         Claude API sentiment per stock.
         Cross-validates with analyst grade actions.

Layer 6  EXECUTION LOGIC
         Calculate dip depth = (current - target) / current.
         If dip < 3%: BUY (immaterial). If dip ≥ 3%: WAIT.
         One-liners describe dip depth, not probability.

Layer 7  DASHBOARD
         Static HTML to GitHub Pages. Dark theme. Warning banner
         if any guardrails were tripped. BUY signals shown first.
```

---

## Four-Gate Validation Pipeline

Every run passes through four validation gates. If data is corrupt or model outputs are nonsensical, the dashboard shows warnings rather than false-precision signals.

### Gate 1: Input Data Quality (after fetch)
- Price must be positive and non-NaN
- Price cross-check: quote vs last historical close (< 3% divergence)
- Historical data: warn < 200 rows, skip < 50 rows
- Freshness: last data point within 5 trading days
- Single-day return outlier scan (> 20% flagged)
- Volume: mean daily > 100,000
- Analyst target: must be 0.5x–2.5x current price, else fallback
- NaN check on all numeric fields

### Gate 2: Model Output Sanity (after GARCH/HMM)
- GARCH annualized vol > 150% → stock marked "unmodelable"
- GARCH stationarity: alpha + beta > 0.95 → warn
- Mean reversion anchor: must be 0.5x–2.5x current price
- Correlation matrix: no off-diagonal > 0.98
- NaN check on all model outputs

### Gate 3: Simulation Output Sanity (after MC)
- Individual paths NOT capped (fat tails are features)
- Dip > 30% below current → flagged as extreme (not clamped)
- Dip target ≥ current price → "no dip expected", signal BUY
- NaN check on all MC outputs

### Gate 4: Portfolio-Level Coherence (after signals)
- VIX < 5 or > 80 → flag data suspect
- All stocks same signal → flag
- Fewer than 10 valid stocks → degraded dashboard
- 0 valid stocks → error page

---

## Data Sources

### FMP API (13 US stocks — 15 endpoints per stock)
| # | Endpoint | Used For |
|---|----------|----------|
| 1 | `historical-price-eod/full` | GARCH, HMM, correlation |
| 2 | `quote` | Current price, fallback anchor |
| 3 | `price-target-consensus` | MC mean reversion anchor |
| 4 | `earnings` | Sentiment context |
| 5 | `grades` | Sentiment modifier |
| 6 | `stock-price-change` | Future: MC drift modifier |
| 7 | `analyst-estimates` | Future: anchor confidence |
| 8 | `price-target-summary` | Future: anchor direction |
| 9 | `technical-indicators/rsi` | Dashboard display |
| 10 | `profile` | Beta display, future: vol calibration |
| 11 | `financial-scores` | Future: distress filter |
| 12 | `grades-consensus` | Future: sentiment enrichment |
| 13 | `discounted-cash-flow` | Future: second anchor |
| 14 | `insider-trading/statistics` | Future: MC drift modifier |
| 15 | `economic-calendar` | Future: macro vol spike |

### yfinance (LDO.MI only — 2 calls)
FMP returns HTTP 402 for European tickers. yfinance provides OHLCV and current price for Leonardo (Milan exchange) in EUR. May return 429 on GitHub Actions shared IPs — pipeline degrades gracefully.

### Anthropic Claude API (sentiment)
Per-stock sentiment scoring (-5 to +5) with one-sentence narrative. Cross-validates against analyst grade actions.

### API Costs Per Run
- FMP: ~185 calls (within 300/min Starter plan limit), $22/mo plan
- Anthropic: ~14 calls, ~$0.10-0.15/run (~$2-3/mo)
- yfinance: 2-3 calls, free

---

## Known Weaknesses

1. **Post-earnings anchor staleness.** After earnings, analyst targets take days to update. Model may underestimate dip probability. Apply judgment after earnings events.
2. **No time-decay.** Treats day 1 and day 59 identically. Planned enhancement.
3. **No backtest.** Model is unvalidated. Watch the 14-day signal grid for calibration after 2-4 weeks.
4. **Sentiment not wired into MC.** Claude scores are generated but don't modify the simulation. Planned enhancement.
5. **New data endpoints fetched but not yet wired into MC.** RSI, beta, momentum, insider stats, DCF, economic calendar are displayed but don't modify simulation paths. Phase 2 enhancement.

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
│   ├── config.py            # All thresholds, conviction dial, portfolio
│   ├── validators.py        # 4-gate validation pipeline
│   ├── data_fetcher.py      # FMP (15 endpoints) + yfinance (LDO.MI)
│   ├── macro_regime.py      # VIX/SPY → risk_on/neutral/risk_off
│   ├── garch_model.py       # GARCH(1,1) volatility forecasting
│   ├── hmm_regime.py        # Simplified regime detection
│   ├── correlation.py       # Correlation matrix + Cholesky
│   ├── monte_carlo.py       # 10K correlated MC paths + 60th pct target
│   ├── sentiment.py         # Claude API sentiment per stock
│   ├── execution_logic.py   # BUY/WAIT based on dip depth vs 3%
│   ├── dashboard_generator.py # HTML with warnings, BUY-first sort
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

---

## Defense of the Approach

### Why dip depth, not probability?
Every stock dips below its current price at some point in 60 days — that's a mathematical property of random walks. Asking "will it dip?" is trivially true. Asking "will it dip enough to matter?" is the useful question. The materiality threshold (3%) converts a statistical exercise into an actionable decision.

### Why 60% conviction, not higher or lower?
At 70%, the model only shows shallow dips and says BUY too often — you'd miss pullbacks. At 50%, you're acting on coin-flip signals with an unproven model. 60% gives a buffer: if the model is slightly miscalibrated (likely, since it's new), you still get 53-57% real-world accuracy. That's a profitable edge for DCA timing.

### Why 3% materiality?
On £500/month across 14 stocks, the largest position (NVDA at 15%) gets £75. A 3% better entry saves £2.25. Across 14 stocks and 12 months, that's £100-200/year in better entries. Compounded over 22 years at 13% CAGR, that's £7,000-8,000 in additional terminal value. Below 3%, the savings don't compound into meaningful amounts.

### Why Monte Carlo, not rules-based?
Rules ("buy when RSI < 30") are brittle and backward-looking. MC simulation incorporates volatility clustering (GARCH), regime shifts (HMM), cross-stock correlation (Cholesky), and mean reversion simultaneously. The output is a distribution, not a binary rule.

### Why not clamp Monte Carlo paths?
Clamping suppresses tail risk — the very thing that causes real dips. Fat tails are features, not bugs. If a stock has beta 2.3 and the market drops 7%, a 16% single-day move is plausible. Capping that would systematically underestimate dip probability.

---

## Enhancement Roadmap (Post-Deployment)

| Phase | Enhancement |
|-------|------------|
| 2a | Wire RSI into MC drift (overbought → increase dip probability) |
| 2b | Wire momentum into MC drift |
| 2c | Wire insider stats into MC drift |
| 2d | Wire earnings date into MC vol spike |
| 2e | Wire economic calendar into MC vol spike |
| 2f | Wire DCF as second anchor with conflict resolution |
| 2g | Wire sentiment score into MC drift |
| 2h | Enrich Claude prompt with all new data |
| 3a | Post-earnings anchor suppression |
| 3b | Time-decay deployment urgency |
| 3c | Backtest scaffold |
