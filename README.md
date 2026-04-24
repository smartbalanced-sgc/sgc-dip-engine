# SGC Dip Engine for Portfolio v7

**Purpose:** Daily BUY/WAIT signal generator for a 14-stock DCA portfolio. Tells the investor whether today's price is the best entry or if a meaningful dip is likely within 60 days.

**Live Dashboard:** https://smartbalanced-sgc.github.io/sgc-dip-engine/
**Schedule:** 9:30 PM BST weekdays (30 min after US market close)
**Monthly deployment:** £500 across 14 stocks per Portfolio Constitution v7

---

## What This Is

A Monte Carlo simulation engine that runs 10,000 correlated price paths per stock over a 60-day horizon. For each stock, it finds the dip level that 60% of simulated futures reach. If that dip is deep enough to matter (≥3%), it tells you to WAIT. If not, it tells you to BUY now.

The simulation is informed by 7 layers of data: macro regime, 2 years of price history, GARCH volatility, per-stock regime detection, cross-stock correlation, AI sentiment analysis, and five enrichment signals (RSI, momentum, insider activity, earnings proximity).

The investor DCA's into these 14 stocks monthly regardless. This engine optimises *entry timing*, not *stock selection*.

## What This Is NOT

- **Not a stock picker.** Does not evaluate whether a stock deserves to be in the portfolio.
- **Not a trading bot.** Generates signals on a static HTML page. The investor decides and executes manually.
- **Not a guarantee.** Models the normal statistical behaviour of stock prices. Cannot predict surprise events.
- **Not backtested.** Calibration comes from observing live output vs actual prices over the first 2-4 weeks.

---

## How The Signal Works (Plain English)

For each stock, the model simulates 10,000 possible price futures over 60 days using:

- The stock's own GARCH volatility (how much it typically swings)
- Its current regime (bull, sideways, or drawdown)
- Correlation with other portfolio stocks (they move together)
- Mean reversion toward analyst price targets (stocks tend to drift toward consensus)
- **RSI** — overbought stocks drift down, oversold stocks drift up
- **AI sentiment** — Claude API scores bearish/bullish narrative
- **Momentum** — stocks that ripped up tend to pull back (contrarian)
- **Insider activity** — heavy insider selling pushes drift down
- **Earnings proximity** — imminent earnings widen volatility

It then finds the **dip level that 60% of those futures reach** and asks: **"Is that dip ≥ 3%?"**

- **Yes** → WAIT. The potential savings justify patience.
- **No** → BUY. The dip is too shallow to matter at these position sizes.

---

## Phase 2 Enrichment Modifiers (Implemented)

Five data streams feed into the Monte Carlo simulation, modifying drift and volatility per stock:

| Modifier | Source | Effect | Scale |
|----------|--------|--------|-------|
| RSI | FMP `technical-indicators/rsi` | Overbought (>70) pushes drift down; oversold (<30) pushes drift up | (50 - RSI) / 500 |
| Sentiment | Claude API | Bearish score pushes drift down; bullish pushes up | score / 100 |
| Momentum | FMP `stock-price-change` | Strong positive 1M momentum → contrarian drag | -momentum / 1000 |
| Insider | FMP `insider-trading/statistics` | Heavy selling → drift down; net buying → drift up | (ratio - 0.5) / 25, capped ±0.03 |
| Earnings | FMP `earnings` | Imminent earnings → vol spike | ≤14d: ×1.5, ≤30d: ×1.3, ≤60d: ×1.15 |

Total enrichment drift is capped at ±0.10 to prevent extreme combined effects from overwhelming the regime drift.

### Example: NVDA (RSI 70, sentiment +2, momentum +17%, insider ratio 0.16, earnings in 27 days)

```
RSI:        (50 - 70) / 500     = -0.040  (overbought → dip likely)
Sentiment:  2 / 100             = +0.020  (mildly bullish)
Momentum:   -17.3 / 1000        = -0.017  (contrarian drag)
Insider:    (0.163 - 0.5) / 25  = -0.013  (heavy selling)
                                  -------
Total drift adjustment:           -0.050  (net bearish)
Earnings vol: 27 days away        × 1.30  (vol spike)
```

vs WM (RSI 38, sentiment 0, momentum +1.2%, balanced insiders, earnings in 5 days)

```
RSI:        (50 - 38) / 500     = +0.024  (oversold → bounce likely)
Sentiment:  0 / 100             = +0.000
Momentum:   -1.2 / 1000         = -0.001
Insider:    (0.5 - 0.5) / 25    = +0.000
                                  -------
Total drift adjustment:           +0.023  (net bullish)
Earnings vol: 5 days away         × 1.50  (big vol spike)
```

NVDA gets a bearish drift push → deeper dip target → WAIT.
WM gets a bullish drift push → shallower dip target → more likely BUY.

---

## The Conviction Dial

Set in `config.py` as `PERCENTILE_TARGET = 60`. Means: "Show me the dip that 60% of simulated futures reach."

- **Higher (70%)** = shallower dips, more certainty. Model says BUY more often.
- **Lower (50%)** = deeper dips, less certainty. Model says WAIT more often.
- **60%** = balanced starting point for an unbacktested model.

Adjust after 2-4 weeks of live observation.

---

## Seven-Layer Architecture

```
Layer 0  MACRO REGIME
         FMP quote for VIX + SPY → risk_on / neutral / risk_off.
         Adjusts vol and correlation multipliers for all stocks.

Layer 1  DATA INGESTION
         FMP API (15 endpoints per US stock) + yfinance (LDO.MI only).
         Economic calendar (1 call).

Layer 2  STATISTICAL ENGINE
         GARCH(1,1) → per-stock forward volatility.
         Simplified HMM → per-stock regime (bull/sideways/drawdown).

Layer 3  AI INTELLIGENCE
         Claude API sentiment per stock (-5 to +5).
         Runs BEFORE MC so scores feed into simulation.

Layer 4  CORRELATION
         Correlation matrix from 2yr returns → Cholesky decomposition.
         Stocks simulated jointly.

Layer 5  MONTE CARLO SIMULATION
         10,000 correlated paths × 60 days.
         Drift = regime + mean reversion + RSI + sentiment + momentum + insider.
         Vol = GARCH × regime × macro × earnings proximity.
         Output: 60th percentile of minimums = "realistic dip level".

Layer 6  EXECUTION LOGIC
         Dip ≥ 3% → WAIT. Dip < 3% → BUY.
         One-liners describe dip depth.

Layer 7  DASHBOARD
         Static HTML to GitHub Pages. Warning banner if guardrails tripped.
         BUY signals first, WAIT sorted by deepest dip.
```

---

## Four-Gate Validation Pipeline

### Gate 1: Input Data Quality
Price positive, non-NaN. Price cross-check (quote vs last close < 3%). History: warn < 200 rows, skip < 50. Freshness within 5 trading days. Return outlier scan (> 20%). Volume > 100K. Analyst target 0.5x–2.5x. NaN check.

### Gate 2: Model Output Sanity
GARCH vol > 150% → unmodelable. Stationarity: alpha + beta > 0.95 → warn. Anchor 0.5x–2.5x or fallback. Correlation no off-diagonal > 0.98. NaN check.

### Gate 3: Simulation Output Sanity
Paths NOT capped. Dip > 30% → flagged extreme. Target ≥ current → no dip expected. NaN check.

### Gate 4: Portfolio-Level Coherence
VIX < 5 or > 80 → flag. All same signal → flag. < 10 valid stocks → degraded. 0 → error page.

---

## Data Sources & API Costs Per Run

| Source | Calls | Cost |
|--------|-------|------|
| FMP (13 US stocks × 14 endpoints + 2 macro + 1 calendar) | ~185 | $22/mo plan |
| Anthropic Claude (sentiment per stock) | ~14 | ~$0.10-0.15/run |
| yfinance (LDO.MI only) | 2-3 | Free |

---

## Known Weaknesses

1. **Post-earnings anchor staleness.** After earnings, analyst targets lag. Model may underestimate dip probability for a few days. Apply judgment after earnings events.
2. **No time-decay.** Treats day 1 and day 59 identically. Planned Phase 3 enhancement.
3. **No backtest.** Model is unvalidated. Watch the signal grid for calibration after 2-4 weeks.
4. **LDO.MI may fail.** yfinance gets 429 on GitHub Actions. Pipeline degrades gracefully but LDO.MI may show as SKIPPED.

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
│   └── index.html           # Dashboard (GitHub Pages)
├── src/
│   ├── config.py            # Thresholds, conviction dial, portfolio
│   ├── validators.py        # 4-gate validation pipeline
│   ├── data_fetcher.py      # FMP (15 endpoints) + yfinance
│   ├── macro_regime.py      # VIX/SPY regime classification
│   ├── garch_model.py       # GARCH(1,1) volatility
│   ├── hmm_regime.py        # Regime detection
│   ├── correlation.py       # Correlation + Cholesky
│   ├── monte_carlo.py       # MC engine + enrichment modifiers
│   ├── sentiment.py         # Claude API sentiment
│   ├── execution_logic.py   # BUY/WAIT on dip depth vs 3%
│   ├── dashboard_generator.py # HTML + warnings + sort
│   └── main.py              # Orchestrator (7 steps + 4 gates)
├── requirements.txt
├── .gitignore
└── README.md
```

---

## Defense of the Approach

### Why dip depth, not probability?
Every stock dips below its current price in 60 days — that's random walk math. Asking "will it dip?" is trivially true. Asking "will it dip enough to matter?" is the useful question.

### Why 60% conviction?
At 70%, only shallow dips show — model says BUY too often. At 50%, signals are coin flips on an unproven model. 60% gives a buffer for miscalibration.

### Why 3% materiality?
On £500/month, a 3% better entry across 14 stocks and 12 months compounds to £7,000-8,000 over 22 years at 13% CAGR. Below 3%, savings don't compound meaningfully.

### Why enrichment modifiers are small?
Each modifier (±0.01 to ±0.05) is conservative. The model is unbacktested — aggressive modifiers could swing signals incorrectly. Better to under-correct and observe, then tune coefficients based on live performance.

### Why cap total enrichment at ±0.10?
Without the cap, a stock with RSI 80 + bearish sentiment + strong momentum + heavy insider selling could accumulate -0.15 enrichment drift, overwhelming the regime signal. The cap ensures enrichment *informs* the simulation without *dominating* it.

---

## Enhancement Roadmap

| Phase | Enhancement | Status |
|-------|------------|--------|
| 2a | RSI → MC drift | ✅ Implemented |
| 2b | Momentum → MC drift | ✅ Implemented |
| 2c | Insider stats → MC drift | ✅ Implemented |
| 2d | Earnings date → MC vol spike | ✅ Implemented |
| 2e | Sentiment → MC drift | ✅ Implemented |
| 2f | Sentiment moved before MC in pipeline | ✅ Implemented |
| 2g | Enrichment modifier logging | ✅ Implemented |
| 2h | Economic calendar → MC vol spike | Planned |
| 2i | DCF as second anchor with conflict resolution | Planned |
| 2j | Enrich Claude prompt with RSI/momentum/insider data | Planned |
| 3a | Post-earnings anchor suppression | Planned |
| 3b | Time-decay deployment urgency | Planned |
| 3c | Backtest scaffold | Planned |
