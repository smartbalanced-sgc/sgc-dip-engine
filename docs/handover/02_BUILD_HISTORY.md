# 02_BUILD_HISTORY.md — What Was Built In The Previous Session

> **Purpose:** Chronological record of what was built, what bugs were caught, what was decided. This is the "what" — see `03_RATIONALE_AND_NUANCES.md` for the "why."
> **Read this SECOND** after `01_SESSION_CONTEXT.md`.

---

## 🏗️ Session Overview

**Date:** May 13, 2026
**Duration:** ~12 hours
**Net result:** One commit (`0a5b504`) shipped to GitHub: per-stock trade regime classifier with AI research integration.

The session started as a focused build of the regime classifier and expanded to include several supporting infrastructure fixes (FMP endpoint corrections, sector data integration, insider data rewrite, 402 handling). Most fixes were discovered through running the system locally and observing real failures.

---

## 📦 Files Modified / Added

### New file
- `src/regime_classifier.py` (513 lines) — entire new module

### Modified files
- `src/config/config.yaml` (+95 lines, regime_classifier section)
- `src/data_fetcher.py` (+220 lines: sector performance fetcher, insider rewrite, 402 caching)
- `src/dashboard_generator.py` (+112 lines: regime badges, override annotations, conviction explainer, fallback suppression, CSS)
- `src/execution_logic.py` (+76 lines: regime modulation logic)
- `src/main.py` (+41 lines: regime classification step, get_client integration, prioritize_buy_signals client fix)
- `src/signal_archiver.py` (+26 lines: new regime columns in CSV)

### Auxiliary
- `.gitignore` cleaned up (44 redundant lines removed — duplicated `src/__pycache__/` entries)
- `src/config.py` — `EUR_DISPLAY_TICKERS` and `POWER_SECTOR_TICKERS` exports verified present
- `data/signal_history.csv` and `docs/index.html` regenerated as part of the run

---

## 🚀 Major Build Items (In Order of Implementation)

### 1. Regime Classifier Module

**File:** `src/regime_classifier.py`

**What:** New module that classifies each stock into one of 5 trade execution regimes:
- `NORMAL` — standard mean-reverting behavior, dip-buy logic valid
- `MOMENTUM` — legitimate strong trend, dip-buy disabled (won't fill)
- `SQUEEZE_RISK` — forced rally characteristics, dip-buy disabled (fragile)
- `OVERSOLD_REVERSAL` — high-conviction bottom signal, dip-buy boosted
- `BREAKDOWN` — sustained decline, no reversal yet, dip-buy disabled

**Composite signals computed per stock:**
- RSI (from existing FMP data)
- 5-day momentum (price change)
- 20-day momentum (price change)
- Drawdown from 60-day rolling high
- Sector decoupling (stock 5d return − sector 5d return)
- Relative volume (today / 30-day avg)

**Classification logic (order matters — most specific first):**
1. SQUEEZE_RISK first (tightest thresholds)
2. MOMENTUM second
3. OVERSOLD_REVERSAL third (volume requirement distinguishes from BREAKDOWN)
4. BREAKDOWN fourth
5. NORMAL default

**Critical implementation detail:** The classification order has a real reason. OVERSOLD_REVERSAL must be checked BEFORE BREAKDOWN because both can technically match the same stock (oversold + drawdown + low RSI), but the volume confirmation is what distinguishes a capitulation reversal from a slow grinding decline. Without correct ordering, all capitulation oversold setups would be misclassified as BREAKDOWN.

**Thresholds (in `config/config.yaml`):**
```yaml
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
oversold_reversal:
  rsi_max: 30
  drawdown_from_high_min: 0.10
  relative_volume_min: 1.2
breakdown:
  drawdown_from_high_min: 0.15
  momentum_20d_max: -0.10
  rsi_max: 45
```

### 2. AI Research Integration

**Where:** `regime_classifier._ai_disambiguate_regime()`

**What:** When the rule-based classifier flags a stock as MOMENTUM or SQUEEZE_RISK (the ambiguous pair — both involve high RSI and big moves but require different actions), Claude is called via Anthropic web search to disambiguate using fresh real-world data.

**Architecture:**
- Reuses the existing `get_client()` pattern from `sentiment.py` (lazy init)
- Uses `claude-sonnet-4-20250514` model
- Uses `web_search_20250305` tool to fetch:
  - Recent short interest (% of float)
  - Insider transactions last 30 days
  - Analyst rating changes last 14 days
  - Material news catalysts
  - Sector context
- Returns structured 5-field response: REGIME, CONFIDENCE, SHORT_INTEREST, REASONING, SOURCES
- Cached 24h per ticker in `data/regime_ai_cache.json`
- Daily cost cap $5

**Why this matters:** The proxy signals alone can't distinguish MOMENTUM from SQUEEZE_RISK. A high-RSI rally with sector decoupling could be either fundamental momentum (NVDA at AI peak) or a short squeeze (rare for large caps but common for small caps). AI research with web access can disambiguate using context the model doesn't have.

**Validation case:** MU was flagged as MOMENTUM. AI research found:
- Short interest 2.25% of float — LOW (would expect >5% for a squeeze)
- CEO sold shares on May 1 — bearish insider signal
- No squeeze characteristics
- Conclusion: Real momentum, not a squeeze
- This matched Jesse's external observation of the trade

### 3. Sector Decoupling Data Source

**Where:** `data_fetcher.fetch_sector_performance()`

**What:** Fetches sector performance from FMP's `historical-sector-performance` endpoint.

**Critical implementation details (HARD-WON):**
- FMP requires `sector` query parameter PER call — must loop over all 11 GICS sectors
- FMP requires `from` and `to` date parameters — WITHOUT THEM, the endpoint returns stale 2024 data
- Returns daily `averageChange` per sector — we aggregate over last 5 days for the decoupling signal
- 11 calls per run (one per sector), adds ~5 seconds to total run time

**11 sectors fetched:**
Technology, Communication Services, Consumer Cyclical, Consumer Defensive, Healthcare, Financial Services, Industrials, Energy, Basic Materials, Utilities, Real Estate

### 4. Insider Data Pipeline Rewrite

**Where:** `data_fetcher.fetch_insider_stats_fmp()`

**Before:** Used `insider-roaster-statistics` endpoint (returned 404 — wrong name) then `insider-trading-statistics` (returns empty arrays for all tickers on Starter plan).

**After:** Uses `insider-trading/search` endpoint, fetches raw insider transactions, filters and aggregates locally.

**Filtering logic:**
- Only `P-Purchase` and `S-Sale` transactions are signal-bearing
- All other types (A-Award, M-Exempt, F-InKind, etc.) are mechanical/compensation events — filtered out
- 30-day window cutoff (signals decay)

**Aggregation:**
- `purchases_count`, `sales_count`
- `purchases_value_30d`, `sales_value_30d` (USD = shares × price)
- `acquiredDisposedRatio` (value-weighted: 0=all selling, 1=all buying, 0.5=neutral) — backward-compatible with `monte_carlo.py`
- `change` ('increasing'/'decreasing'/'neutral') — backward-compatible with `sentiment.py`
- `cluster` (bool: ≥3 unique insiders trading same direction)
- `most_senior_buyer/seller` (highest seniority: CEO > CFO > officer > director)
- `most_recent_date`

**Why value-weighted, not count-weighted:** A single $50M CEO sale carries more signal than 5 director gifts. Volume-weighted aggregation reflects intent more honestly.

### 5. FMP Endpoint Corrections

**`upgrades-downgrades-consensus` → `grades-consensus`**

The endpoint name was wrong. The original returned 404 on every call (31 wasted calls/run). The correct endpoint returns real grades-consensus data:
```json
{"symbol": "NVDA", "strongBuy": 2, "buy": 58, "hold": 16, "sell": 3, "strongSell": 0, "consensus": "Buy"}
```

**`insider-roaster-statistics` → `insider-trading/search`**

The original returned 404 (wrong name). The intermediate fix (`insider-trading-statistics`) returned empty arrays. The correct endpoint is `insider-trading/search` returning raw transactions to filter locally.

### 6. 402 Early-Return Cache

**Where:** `data_fetcher.fmp_get()` + module-level `_FMP_BLOCKED_TICKERS` set

**What:** When `fmp_get()` receives a 402 (Payment Required) response for a ticker, it caches that ticker as "blocked" and short-circuits all subsequent endpoint calls for that ticker.

**Before:** 12 endpoint calls per non-US ticker, all returning 402 = 36 wasted calls/run for IGLN.L + RR.GB + BARC.GB
**After:** 1 endpoint call per non-US ticker (first one triggers cache) = 3 calls/run

**Savings:** ~33 wasted API calls per run. Over 300 weekday runs/year, that's ~10,000 fewer wasted API calls.

### 7. Signal Modulation In `execution_logic.py`

**Modulation map (in config.yaml):**
```yaml
signal_modulation:
  NORMAL: "pass_through"
  MOMENTUM: "suppress_buy"
  SQUEEZE_RISK: "suppress_buy"
  OVERSOLD_REVERSAL: "boost_conviction"
  BREAKDOWN: "suppress_buy"
```

**Behavior:**
- `pass_through` — no change to signal
- `suppress_buy` — BUY signals overridden to WAIT with regime-specific reasoning
- `boost_conviction` — adds high-conviction annotation, signal unchanged

**Triple-gate suppression** (in `execution_logic.process_execution_signals`):
- Master toggle: `regime_classifier.suppress_signals` must be True
- Action must be 'suppress_buy'
- Signal must be 'BUY' (no point suppressing a WAIT)
- All three required for override

**Original signal preserved** before override for backtest tracking.

### 8. Dashboard Upgrades

**Regime badges** — visual indicators per stock:
- 🚀 MOMENTUM
- ⚠️ SQUEEZE_RISK
- 💎 OVERSOLD_REVERSAL
- 📉 BREAKDOWN
- ✨ marker if AI-researched

**Regime note block** — appears below the signal one-liner:
- Style: yellow-tinted background, "REGIME:" prefix in orange
- Content: regime explanation + reasoning + AI-fetched short interest
- Only shown for non-NORMAL regimes

**Dip target override annotation:** `(13.3% — ⚠️ regime override: unlikely to fill)` for stocks in MOMENTUM/SQUEEZE_RISK/BREAKDOWN regimes

**Conviction explainer (collapsible)** at top of dashboard:
- Explains what 70% dip and 60% rally conviction actually mean
- Explains why they can sum >100% (overlapping subsets of same paths)
- Explains the regime override mechanic

**Fallback BUY suppression:** When regime is suppress_buy, the "Fallback: BUY at $X" line is hidden entirely. Prevents the contradictory "dip-buy disabled" + "fallback BUY available" display.

**Trading 212 URL mapping updated** for new GB tickers:
- RR.GB → RR.GB
- BARC.GB → BARC.GB
- `.GB` suffix → `£` currency display

### 9. CSV Archive Schema Update

**Where:** `signal_archiver.py`

**New columns added:**
- `trade_regime` (NORMAL/MOMENTUM/SQUEEZE_RISK/OVERSOLD_REVERSAL/BREAKDOWN)
- `regime_confidence` (0.0-1.0)
- `regime_overrode` (True/False — did regime suppress original signal?)
- `original_signal` (the signal before override, empty if not overridden)

**Backward compat:** Uses `extrasaction='ignore'` so older rows without these columns don't break. New columns are appended to existing schema.

---

## 🐛 Bugs Caught And Fixed During The Build

### 1. F-string format spec error (caught in initial syntax check)
**Code:** `{rsi:.0f if rsi else 'N/A'}` — invalid Python (conditional inside format spec)
**Fix:** Pre-computed `rsi_str = f"{rsi:.0f}" if rsi is not None else "N/A"` before the f-string

### 2. Classification order bug (caught in regime scenarios test)
**Code:** OVERSOLD_REVERSAL check came AFTER BREAKDOWN
**Symptom:** Capitulation oversold setups (RSI 25, deep drawdown, high volume) were being classified as BREAKDOWN
**Fix:** Reordered classification flow. OVERSOLD_REVERSAL now checked first; the volume requirement is what distinguishes the two regimes

### 3. Sector performance API call format wrong (caught in first real run)
**Code:** Bulk call without sector param → HTTP 400
**Symptom:** "Sector performance: HTTP 400" and 0 sector rows
**Discovery:** FMP requires `sector=<name>` parameter PER call (not bulk)
**Fix:** Loop over 11 GICS sectors with explicit sector param + date range

### 4. Stale `config.py` mismatch
**Symptom:** `ImportError: cannot import name 'EUR_DISPLAY_TICKERS' from 'config'`
**Discovery:** Local config.py was older than GitHub version — `cat >> config.py` appended needed lines
**Resolution:** Re-cloned fresh from GitHub, re-applied all patches

### 5. Stale `sentiment.py` (chain of stale code)
**Symptom:** `ImportError: cannot import name 'detect_catalysts'`
**Discovery:** Local repo wasn't a git clone, was a downloaded copy → had drifted from GitHub
**Resolution:** Full fresh clone, re-applied refinement, set up Python 3.10 venv (3.14 was too new for pinned deps)

### 6. BUY prioritization NoneType crash
**Code:** `prioritize_buy_signals(buy_tickers, portfolio_data, None)` in main.py
**Symptom:** AI prioritization failed with "NoneType has no attribute 'messages'"
**Fix:** Pass `get_client()` instead of `None`

### 7. Orphan `·` in regime note (cosmetic)
**Code:** `f"Momentum regime — ... . {regime_reasoning}"` with empty reasoning + `· SI:` suffix produced `". · SI: ..."` 
**Fix:** Conditional inclusion of reasoning suffix; renamed `· SI:` to `Short interest: X.`

---

## 🌍 Environment / Setup Changes During The Session

### Python 3.10 install via Homebrew
- Original venv was Python 3.14 (too new for pinned deps: `numpy==1.24.3`, `pandas==2.0.3`, `scipy==1.11.2`)
- Installed Python 3.10 via `brew install python@3.10`
- Rebuilt venv with `python3.10 -m venv venv`
- GitHub Actions uses Python 3.10 — local now matches

### Fresh git clone of repo
- Original local was a downloaded copy, not git clone (no `.git` folder)
- Moved old folder to `sgc-dip-engine.broken_20260513_034729`
- `git clone https://github.com/smartbalanced-sgc/sgc-dip-engine.git`
- All refinement files re-applied to fresh clone

---

## ⚙️ The Pre-Commit Diagnostic Discoveries

Before pushing, we ran several diagnostic checks. Two findings worth noting:

### `.gitignore` corruption
- File had `src/__pycache__/` repeated 44 times at the bottom (some tool was appending in a loop)
- Cleaned to ~51 lines (deduplication preserving order)
- Included in the push

### `config.py` not modified
- Expected `git status` to show `config.py` modified (we appended EUR_DISPLAY_TICKERS lines)
- It wasn't — meaning the GitHub baseline already had those lines
- Our `cat >>` may have effectively been a no-op against the already-correct file

---

## 🔄 The Wave-By-Wave Pattern Used

The build proceeded in waves with explicit approval at each gate:

1. **Wave 1:** Config + regime_classifier core (pure logic, AI hook stubbed)
2. **Wave 2:** Sentiment.py extension (AI research function)
3. **Wave 3:** Main.py wiring + execution_logic suppression
4. **Wave 4:** Dashboard + CSV archive
5. **Wave 5 (separate sub-session):** FMP endpoint corrections
6. **Wave 6 (final):** Cosmetic + 402 cleanup + conviction explainer

Each wave was applied, tested with `python3 main.py`, verified in the dashboard PDF, then approved before the next wave began.

---

## 📊 Final Test Results Before Push

**Run output (last successful run before push):**
- 31/34 stocks fetched (3 skipped: IGLN.L, RR.GB, BARC.GB)
- 28 simulated (3 too volatile: INOD, AIIO, FWRD)
- 88 sector performance rows fetched
- 7 stocks classified non-NORMAL:
  - 🚀 MU: MOMENTUM (80% conf)
  - 🚀 INOD: MOMENTUM (85% conf)
  - 💎 ENGN: OVERSOLD_REVERSAL (75% conf)
  - 💎 FWRD: OVERSOLD_REVERSAL (75% conf)
  - 📉 VST: BREAKDOWN (75% conf)
  - 📉 GDC: BREAKDOWN (75% conf)
  - 📉 HUBS: BREAKDOWN (75% conf)
- 3 BUY signals: WM, CTAS, LIN
- Backtest hit rate: 68% (38/56)

**Dashboard PDF visually verified:**
- All 7 non-NORMAL stocks show regime badges
- MU dip target shows "⚠️ regime override: unlikely to fill"
- Conviction explainer renders correctly
- MU regime note reads as clean sentence (orphan `·` fixed)
- Fallback BUY suppressed on MU/VST/GDC/HUBS

#End
