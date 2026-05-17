# 06_SYSTEM_ARCHITECTURE.md — Technical Map Of The System

> **Purpose:** Reference document. Technical layout of files, data flow, dependencies, and danger zones.
> **Read this SIXTH** (reference document — dip in as needed rather than reading cover-to-cover).

---

## 🗂️ Repository Structure

```
sgc-dip-engine/
├── CLAUDE.md                    ← Mandate for AI assistants (read first)
├── README.md                    ← Public project description
├── requirements.txt             ← Python deps (pinned versions)
├── .github/
│   └── workflows/
│       └── daily-run.yml        ← GitHub Actions cron (9:30 PM UTC daily)
├── .gitignore
├── data/
│   ├── signal_history.csv       ← Daily signals archive (282+ days)
│   ├── regime_ai_cache.json     ← 24h cache of AI research per ticker
│   └── (other generated files)
├── docs/
│   ├── index.html               ← Published dashboard (GitHub Pages)
│   ├── handover/                ← System as DESIGNED (intent, rationale, sacred)
│   │   ├── 01_SESSION_CONTEXT.md
│   │   ├── 02_BUILD_HISTORY.md
│   │   ├── 03_RATIONALE_AND_NUANCES.md
│   │   ├── 04_NEXT_BUILD_SPEC_DEPLOYED.md
│   │   ├── 05_USER_PROFILE.md
│   │   └── 06_SYSTEM_ARCHITECTURE.md
│   └── research/                ← System as VALIDATED (empirical evidence)
│       ├── README.md            ← Archive policy + index of reports
│       └── YYYY-MM-DD_*.md      ← Dated verdict snapshots
├── research/                    ← Standalone research scripts (NOT production)
│   ├── regime_backtest.py       ← Regime classifier rule validation tool
│   ├── .cache/                  ← yfinance pull cache (gitignored)
│   └── regime_backtest_report.md← Auto-generated, overwritten each run (gitignored)
└── src/
    ├── main.py                  ← Orchestrator — entry point
    ├── config.py                ← Config loader + exports
    ├── config_loader.py         ← YAML loader (used by config.py)
    ├── config/
    │   └── config.yaml          ← All thresholds, tickers, weights
    ├── data_fetcher.py          ← FMP + Eulerpool integration
    ├── monte_carlo.py           ← GARCH + correlated 10K-path simulation
    │                              (also computes daily probability bands)
    ├── hmm_regime.py            ← Macro regime detection (bull/sideways/drawdown)
    ├── macro_regime.py          ← VIX-based macro context
    ├── regime_classifier.py     ← Per-stock trade regime classifier
    ├── execution_logic.py       ← BUY/WAIT signal generation + regime modulation
    │                              (also propagates daily_bands to dashboard)
    ├── sentiment.py             ← AI catalyst detection + prioritization
    ├── signal_archiver.py       ← CSV archive of daily signals
    ├── dashboard_generator.py   ← HTML dashboard rendering
    │                              (renders daily probability bands per stock)
    ├── validators.py            ← Gate 1-4 sanity checks
    └── backtest.py              ← Daily hit-rate evaluation of past WAIT signals
```

---

## 🔄 Data Flow (End-To-End)

```
[GitHub Actions cron at 9:30 PM UTC]
            ↓
   src/main.py orchestrator
            ↓
┌──────────────────────────────────────────────────────────────┐
│ STEP 1: Fetch portfolio data                                  │
│   src/data_fetcher.py                                         │
│   For each ticker in config.yaml:                            │
│     • Historical OHLC (FMP historical-price-eod/full)        │
│     • Current quote (FMP quote)                              │
│     • Price targets, analyst grades, earnings dates          │
│     • RSI, financial scores, DCF, insider transactions       │
│   LDO.MI: Eulerpool only (FMP returns 402)                  │
│   Non-US tickers: 1 call each (then short-circuit on 402)   │
│   + Sector performance (11 sectors × dates)                  │
│   + Macro indicators (VIX, treasury rates)                   │
└──────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────┐
│ GATE 1: Validate input data                                   │
│   src/validators.py                                           │
│   • Anchor sanity (model vs market price)                    │
│   • Volatility outliers                                      │
│   • Skip tickers with no current price                       │
└──────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────┐
│ STEP 2: Detect regimes (HMM-style)                            │
│   src/hmm_regime.py                                           │
│   Returns per-stock: bull / sideways / drawdown               │
│   src/macro_regime.py                                         │
│   Returns macro: BULLISH / NEUTRAL / RISK_OFF                 │
└──────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────┐
│ GATE 2: Validate model outputs                                │
│   • Volatility > 150%: exclude from simulation                │
└──────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────┐
│ STEP 3: AI catalyst detection (emergency searches)            │
│   src/sentiment.py                                            │
│   • Z-score anomaly detection (price moves)                  │
│   • If unexplained move: AI web search for catalyst          │
│   • Output: thesis INTACT / AT_RISK / BROKEN                 │
└──────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────┐
│ STEP 4: Build correlation matrix                              │
│   src/monte_carlo.py                                          │
│   • Pearson correlations from log returns                    │
│   • Cholesky decomposition for correlated paths              │
└──────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────┐
│ STEP 5: Run Monte Carlo simulations                           │
│   src/monte_carlo.py                                          │
│   For each modeled stock:                                     │
│     • GARCH(1,1) for vol prediction                          │
│     • 10,000 correlated paths × 60 days                      │
│     • Drift modifiers (HMM regime, RSI, momentum, insider)   │
│     • Time-varying vol schedule (Phase 2 enrichment)         │
│   Output per stock:                                           │
│     • paths array (10000, 60)                                │
│     • percentile_low (70th of minimums)                      │
│     • rally_primary (30th of maximums = 70% conviction)      │
│     • date indices, terminal price                           │
└──────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────┐
│ GATE 3: Validate simulation outputs                           │
└──────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────┐
│ ⭐ NEW: Classify per-stock trade regimes                      │
│   src/regime_classifier.py                                    │
│   For each modeled stock:                                     │
│     • Compute composite signals (RSI, mom_5d, drawdown,      │
│       sector_decoupling, relative_volume)                    │
│     • Rule-based classification: SQUEEZE/MOMENTUM/OVERSOLD/  │
│       BREAKDOWN/NORMAL                                       │
│     • If MOMENTUM or SQUEEZE: AI research disambiguation     │
│     • Cache AI results 24h                                   │
└──────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────┐
│ STEP 6: Generate execution signals                            │
│   src/execution_logic.py                                      │
│   For each stock:                                             │
│     • Compute dip_pct from MC percentile_low                 │
│     • Threshold: dip_pct < 3% → BUY, else → WAIT             │
│     • Regime modulation:                                     │
│       - suppress_buy: BUY → WAIT (preserve original_signal)  │
│       - boost_conviction: add ⭐ annotation                  │
│       - pass_through: no change                              │
│     • Generate one-liner explanation                         │
└──────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────┐
│ AI Prioritization (BUY signals only)                          │
│   src/sentiment.py prioritize_buy_signals()                   │
│   • Rank multiple BUYs by quality                            │
│   • Cite reasoning per stock                                 │
└──────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────┐
│ GATE 4: Validate portfolio signals                            │
└──────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────┐
│ Run backtest                                                  │
│   src/backtest.py                                             │
│   • Look at WAIT signals from 14+ days ago                   │
│   • Did the predicted dip actually occur?                    │
│   • Compute per-ticker hit rate                              │
└──────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────┐
│ STEP 7: Generate HTML dashboard                               │
│   src/dashboard_generator.py                                  │
│   • Warnings section (collapsible)                           │
│   • Conviction explainer (collapsible)                       │
│   • Backtest section (collapsible)                           │
│   • Today's deployment (BUY/WAIT lists)                      │
│   • Per-stock detail rows with regime badges                 │
│   • Output: docs/index.html                                  │
└──────────────────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────────────────┐
│ STEP 8: Archive signals                                       │
│   src/signal_archiver.py                                      │
│   • Append today's signals to data/signal_history.csv        │
│   • Include regime metadata columns                          │
└──────────────────────────────────────────────────────────────┘
            ↓
[GitHub Actions commits dashboard + CSV back to repo]
[GitHub Pages publishes docs/index.html]
```

---

## 🧩 Two Distinct Regime Concepts (Do NOT Confuse)

This is the #1 architectural pitfall. They sound similar but serve different consumers.

### `hmm_regime.py` — Macro market regime
- **Returns:** `bull` / `sideways` / `drawdown`
- **Time horizon:** Long-term (uses HMM state on 500-day window)
- **Consumer:** `monte_carlo.py` — adjusts drift and vol multipliers
- **Decision:** "How should I tune the simulation parameters?"

### `regime_classifier.py` — Per-stock trade regime
- **Returns:** `NORMAL` / `MOMENTUM` / `SQUEEZE_RISK` / `OVERSOLD_REVERSAL` / `BREAKDOWN`
- **Time horizon:** Short-term (RSI, 5d/20d momentum, recent drawdown)
- **Consumer:** `execution_logic.py` — modulates BUY/WAIT signals
- **Decision:** "Should the signal be acted on, or is this special?"

### Why both exist
- HMM regime affects WHAT the simulation predicts (a drawdown regime tilts drift negative)
- Trade regime affects WHETHER to act on the simulation's prediction (a momentum regime means "don't trust the dip prediction")

### If you find yourself merging them, STOP
This was a deliberate separation. Combining them would create false coupling and break independent reasoning.

---

## 🔌 API Endpoints (FMP — Starter Plan)

### Confirmed working endpoints

| Endpoint | Used For | Params |
|---|---|---|
| `historical-price-eod/full` | 500-day OHLC | `symbol` |
| `quote` | Current price snapshot | `symbol` |
| `price-target-consensus` | Analyst price target consensus | `symbol` |
| `earnings` | Earnings calendar (recent + upcoming) | `symbol` |
| `grades` | Recent analyst grade changes | `symbol` |
| `grades-consensus` | Analyst consensus distribution | `symbol` |
| `stock-price-change` | 1M / 3M / 6M price change | `symbol` |
| `analyst-estimates` | Forward EPS/revenue estimates | `symbol` |
| `price-target-summary` | Mean/high/low price targets | `symbol` |
| `technical-indicators/rsi` | RSI value | `symbol`, `periodLength=14` |
| `profile` | Company profile + beta | `symbol` |
| `financial-scores` | Piotroski / Altman scores | `symbol` |
| `discounted-cash-flow` | DCF intrinsic value | `symbol` |
| `insider-trading/search` | Raw insider transactions | `symbol` |
| `economic-calendar` | Macro events | `from`, `to` |
| `historical-sector-performance` | Daily sector returns | **`sector`, `from`, `to` (ALL REQUIRED)** |

### Endpoints that DO NOT work on Starter plan
- Any non-US ticker (returns 402 — short-circuited by `_FMP_BLOCKED_TICKERS` cache)
- `aftermarket-quote` (available in higher tier, intentionally not used)

### Endpoint corrections (do not revert)
- ✅ Use `grades-consensus` (NOT `upgrades-downgrades-consensus`)
- ✅ Use `insider-trading/search` (NOT `insider-trading-statistics` or `insider-roaster-statistics`)
- ✅ Use `historical-price-eod/full` (NOT `historical-price-full`)

---

## 📦 Other Data Sources

### Eulerpool (LDO.MI only)
- **Why:** FMP returns 402 on .MI tickers
- **Endpoint:** Internal Eulerpool MCP integration via `EULERPOOL_TOKEN`
- **Quirk:** Candle data often lags by 30+ days; current price is fresh from profile endpoint
- **Limit:** 2 calls/run max (avoid 429 on shared GitHub Actions IPs)

### Anthropic Claude API
- **Used in 3 places:**
  1. `sentiment.py` — emergency catalyst detection (web search)
  2. `sentiment.py` — BUY signal prioritization (reasoning)
  3. `regime_classifier.py` — MOMENTUM/SQUEEZE_RISK disambiguation (web search)
- **Model:** `claude-sonnet-4-20250514`
- **Web search tool:** `web_search_20250305`
- **Cost:** ~$0.20-2/day depending on triggers
- **Lazy init:** `get_client()` function (module-level init crashes at import)

---

## 🗃️ Config Structure (`src/config/config.yaml`)

Key sections (NOT exhaustive — read the file for full schema):

```yaml
# Data sources
data:
  tickers: [NVDA, MSFT, GOOGL, ...]
  eur_display_tickers: [ASML, LDO.MI]
  power_sector_tickers: [VST, CEG]

# Signal generation thresholds
signal:
  percentile_target: 70           # 70th percentile of minimums
  materiality_threshold: 0.03     # 3% dip → BUY threshold
  fallback_percentile: 80         # Fallback BUY conviction level
  rally_conviction_percentile: 70 # 70% conviction rally target (raised from 60 on 2026-05-16)

# Monte Carlo
monte_carlo:
  num_paths: 10000
  simulation_days: 60
  correlation_window: 60          # Days for correlation matrix

# Regime classifier (NEW — added in last session)
regime_classifier:
  enabled: true
  suppress_signals: true
  momentum:
    rsi_min: 75
    momentum_5d_min: 0.10
    sector_decoupling_min: 0.05
    relative_volume_min: 1.3
  squeeze_risk:
    rsi_min: 80
    momentum_5d_min: 0.15
    sector_decoupling_min: 0.10
    relative_volume_min: 1.8
    short_interest_threshold: 0.10
  oversold_reversal:
    rsi_max: 30
    drawdown_from_high_min: 0.10
    relative_volume_min: 1.2
  breakdown:
    drawdown_from_high_min: 0.15
    momentum_20d_max: -0.10
    rsi_max: 45
  signal_modulation:
    NORMAL: "pass_through"
    MOMENTUM: "suppress_buy"
    SQUEEZE_RISK: "suppress_buy"
    OVERSOLD_REVERSAL: "boost_conviction"
    BREAKDOWN: "suppress_buy"
  ai_research:
    enabled: true
    cache_ttl_hours: 24
    daily_cost_cap_usd: 5.0
    model: "claude-sonnet-4-20250514"
```

### YAML is the source of truth
- Never hardcode thresholds in Python — always pull from YAML
- Never change YAML without Jesse's explicit approval
- The `get_config('section', 'key', default=X)` pattern is universal

---

## 🤖 GitHub Actions Workflow

### File: `.github/workflows/daily-run.yml`

**Schedule:** Cron `30 21 * * *` (9:30 PM UTC daily)

**What it does:**
1. Checks out repo
2. Sets up Python 3.10
3. Installs requirements
4. Runs `python src/main.py`
5. Commits updated `docs/index.html` and `data/signal_history.csv`
6. Pushes back to main

### Secrets required (in repo Settings → Secrets and variables → Actions)
- `FMP_API_KEY`
- `ANTHROPIC_API_KEY`
- `EULERPOOL_TOKEN`

### Implications for any code change
- **Cron will pick up your code at next 9:30 PM UTC.** If you push broken code at 9:25 PM UTC, the next run will fail and Jesse won't have a fresh dashboard.
- **Anything new requiring a secret must be added to GitHub Secrets** BEFORE pushing.
- **Local-only behaviors (e.g., reading from `~/.zshrc`) won't work in CI.** Use os.environ.
- **Dependencies added to requirements.txt** will be installed at next CI run — beware of slow installs or version conflicts.

---

## 💾 Persistence Layers

### `data/signal_history.csv`
- One row per (ticker, date) pair
- Schema: ticker, date, signal, dip_pct, dip_price, dip_date, rally_pct, rally_price, confidence, **trade_regime, regime_confidence, regime_overrode, original_signal**, ...
- Used by `backtest.py` to evaluate past signals
- **Tracked by git** (explicitly excluded from `.gitignore`'s `data/*.csv` rule)
- **GitHub Actions commits this back to repo daily** — don't manually push outdated versions

### `data/regime_ai_cache.json`
- Key: ticker name (e.g., "MU")
- Value: { regime, confidence, short_interest, reasoning, sources, timestamp }
- TTL: 24 hours per entry
- **NOT tracked by git** (in `data/*.json` ignore)
- Regenerated daily as needed

### `docs/index.html`
- Generated by `dashboard_generator.py`
- Published via GitHub Pages
- **Tracked by git**
- Updated daily by GitHub Actions

---

## ⚠️ Danger Zones

### Files where changes have high blast radius

| File | Risk | Why |
|---|---|---|
| `config/config.yaml` | High | Hardcoded everywhere; changes affect every stock |
| `monte_carlo.py` | High | Drives all predictions; subtle bugs invalidate backtest |
| `regime_classifier.py` | Medium | New module, less battle-tested than MC |
| `data_fetcher.py` | High | API calls are expensive; bugs waste budget |
| `validators.py` | Medium | Skipping wrong stocks → wrong signals |
| `.github/workflows/*.yml` | **Critical** | Bad workflow breaks daily run for everyone |

### Behaviors with hidden side effects

- **Adding tickers to config.yaml** — requires API calls for each, increases run time
- **Changing materiality_threshold** — flips BUY/WAIT signals across portfolio
- **Changing percentile_target** — changes ALL dip prices and backtest baseline
- **Removing a backward-compat field** — breaks downstream consumers silently
- **Changing CSV schema** — old rows missing new columns (use `extrasaction='ignore'`)
- **Re-running main.py manually** — appends another row for today (duplicate signals)

### Cost-incurring operations
- Each FMP call costs API quota (Starter plan limits)
- Each Anthropic call costs $0.05-0.10 (sentiment, regime AI)
- Each Eulerpool call has rate limits (~2/min)
- Total daily cost target: < $5

---

## 🧪 Local Development Setup

### Jesse's environment
- **OS:** macOS (MacBook Air)
- **Python:** 3.10 (Homebrew install)
- **Repo path:** `/Users/jesse/sgc/sgc-dip-engine`
- **Venv:** `/Users/jesse/sgc/sgc-dip-engine/venv`
- **Env vars:** Sourced from `~/.zshrc`

### Running locally
```bash
cd ~/sgc/sgc-dip-engine
source venv/bin/activate
cd src
python3 main.py
```

### Dashboard preview
```bash
open ~/sgc/sgc-dip-engine/docs/index.html
```

---

## 🧰 Common Diagnostic Patterns

### Check current git state
```bash
cd ~/sgc/sgc-dip-engine && git status && git log --oneline -3
```

### Verify file integrity after a patch
```bash
python3 -c "import ast; ast.parse(open('path/to/file.py').read()); print('OK')"
```

### Test an FMP endpoint manually
```bash
curl -s "https://financialmodelingprep.com/stable/<endpoint>?symbol=NVDA&apikey=$FMP_API_KEY" | head -c 500
```

### Check what's about to be committed
```bash
git diff --stat
git diff <file>
```

### Sanity check the dashboard
```bash
grep -c "regime-badge" docs/index.html  # Should be ~7 (matching non-NORMAL stocks)
grep "regime override" docs/index.html | head -5
```

---

## 🔭 Future Architecture Considerations (Not Today's Work)

Areas Jesse has flagged for future build phases:

### Hysteresis band on BUY/WAIT threshold
- Add 0.5% band to prevent flapping at materiality boundary
- Requires persistent state (where to store?)
- Backtest validation before deploy

### Regime classifier threshold tuning
- AMZN/ASML/AVGO at 40% hit rate suggests classifier may be missing milder momentum cases
- Need 30+ more days of backtest data before adjusting

### Daily probability bands feature (SHIPPED 2026-05-14)
- Implemented in `src/monte_carlo.py` (compute) + `src/execution_logic.py` (propagate) + `src/dashboard_generator.py` (render)
- Spec archived at `docs/handover/04_NEXT_BUILD_SPEC_DEPLOYED.md`
- Display-only, no behavioral changes (signal generation, MC, backtest all unchanged)

### Regime classifier rule validation (SHIPPED 2026-05-14)
- `research/regime_backtest.py` tool, run on-demand
- See `docs/research/2026-05-14_regime_classifier_backtest.md` for current verdict
- Next forward eval: 2026-06-13 to evaluate MU prediction

### Separate swing-trade dashboard
- If swing trading becomes more frequent, dedicated tooling with premarket prices, position tracking, P&L
- Explicitly separate from this long-term system

---

## 🎯 Summary

The system is a multi-layer pipeline:
1. Fetch data (FMP + Eulerpool)
2. Detect regimes (HMM macro + per-stock trade regimes)
3. Simulate paths (Monte Carlo, GARCH, correlated)
4. Generate signals (BUY/WAIT with regime modulation)
5. Backtest, render, archive

Each layer has clear inputs/outputs. **Respect those boundaries.** When extending, add features in the appropriate layer, don't bleed concerns across layers.

The two-regime distinction (`hmm_regime` vs `regime_classifier`) is the most subtle architectural decision in the system. Internalize it before making any change involving the word "regime."

#End
