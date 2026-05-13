# 01_SESSION_CONTEXT.md — Where We Are

> **Purpose:** Orient a new Claude session on the current state of the SGC Dip Engine, what was just shipped, and what's next on the agenda.
> **Read this FIRST** after CLAUDE.md.

---

## 🎯 Current Build State

**Last shipped commit:** `0a5b504 — Add per-stock trade regime classifier with AI research integration`
**Pushed to GitHub:** May 13, 2026
**GitHub Actions status:** Will pick up new code on next scheduled cron run

### What's Live On GitHub Right Now

A complete **per-stock trade regime classifier system** is in production. It classifies each of 31 modeled stocks into one of 5 trade execution regimes (NORMAL/MOMENTUM/SQUEEZE_RISK/OVERSOLD_REVERSAL/BREAKDOWN) and uses this to modulate the BUY/WAIT signals from the Monte Carlo simulation.

Built and shipped in the previous session:
- ✅ Regime classifier core module (`src/regime_classifier.py`)
- ✅ AI research integration via Anthropic web search (disambiguates MOMENTUM vs SQUEEZE_RISK)
- ✅ Sector decoupling via FMP per-sector calls
- ✅ Real insider data pipeline (P-Purchase + S-Sale, 30-day aggregation)
- ✅ FMP endpoint corrections (`grades-consensus`, `insider-trading/search`)
- ✅ 402 short-circuit caching for non-US tickers
- ✅ Dashboard regime badges + override annotations
- ✅ Conviction explainer collapsible
- ✅ Fallback BUY suppression
- ✅ CSV archive with regime metadata

---

## 🚧 What's Next — The Daily Probability Bands Feature

**Status:** Spec'd and approved, not yet built.

**Read `04_NEXT_BUILD_SPEC.md` for the full spec.** Brief summary here for orientation:

### The intent (one sentence)
Show Jesse a daily expectation table for each stock over the 60-day Monte Carlo window, with two columns (lower 70% band and upper 60% band), collapsible per-stock, default closed.

### Why this feature
Jesse uses the system for **two purposes**:
1. **Monthly DCA** — long-term compounding (the primary 22-year use case)
2. **Occasional swing trades** — when high-conviction setups appear (recent example: MU $750 → $812)

For swing trading, he wants to see **how conviction evolves day-by-day** over the 60-day window. The Monte Carlo already computes 10,000 paths × 60 days of data; we currently throw 99% of it away and only show 4 summary statistics (dip price, dip date, rally price, rally date).

The new feature exposes the daily evolution while preserving the integrity of the existing signal generation.

### Critical caveats (must be on the dashboard)
The feature MUST include preamble text explaining:
- Each day's percentile is a statistical measurement, NOT a prediction of the price on that day
- Different paths reach their dip/rally on different days — these stats mix paths
- You cannot read this as a "buy on Day 15" signal
- The headline dip/rally targets remain the primary buy/sell levels

### Constraints (locked)
- **DO NOT** change Monte Carlo logic
- **DO NOT** change dip/rally target prices
- **DO NOT** change BUY/WAIT signal generation
- **DO NOT** change regime classifier
- **DO NOT** change backtest
- Display-only feature, no behavioral side effects

---

## 🔍 The Recently-Validated MU Trade Pattern

In the last session, Jesse executed a real swing trade that validated the new regime classifier:

- **Entry:** MU at $744 (Tuesday)
- **System said:** MOMENTUM regime 80% confidence + "dip-buy unlikely to fill"
- **System said:** Rally target $904 in 60 days
- **System said:** AI research confirmed real fundamentals + low short interest (2.25%) + recent CFO insider sales
- **Jesse held through:** Rally to $812 (Wednesday premarket)
- **Jesse exited:** ~$310 / £237 realized gain on 5 shares

**The system's regime classifier called this correctly.** MU's historical backtest hit rate is 0% (0/5 WAIT signals filled) which validates the MOMENTUM regime classification telling Jesse not to wait for the predicted dip.

This is **the canonical use case** for the daily probability bands feature he wants next — to see how the rally and dip conviction evolved day-by-day during the trade.

---

## 🌍 The Bigger Picture

### Jesse's strategy in one paragraph

Jesse is running a **22-year compounding strategy** targeting 7× returns on initial capital, deployed through a Trading 212 Stocks & Shares ISA with monthly DCA contributions. The Smart Growth Compounder (SGC) system has two interlocking components:

1. **Portfolio Constitution v7.1** (in project files) — governs stock selection, weights, CAGR targets across 14 core stocks in 4 sleeves (Core Growth, Infrastructure, Power, Resilience). Blended CAGR target: ~13.47%
2. **Dip Engine** (this codebase) — optimizes daily entry timing without altering stock selection

### How regime classifier serves both use cases

| Use case | How regime classifier helps |
|---|---|
| Monthly DCA | Same as before — BUY/WAIT signals tell you what to fund this month. Regime overrides prevent buying into momentum traps. |
| Swing trading | The regime info + dashboard annotations help with both entry conviction and exit timing |

---

## 🧭 Recommended First Actions In New Session

When you start working on this repo:

1. **Read `CLAUDE.md` and all 6 handover docs** before touching any code
2. **Confirm your understanding to Jesse** before any action
3. **Acknowledge approval gates explicitly** in your first response
4. **Wait for Jesse to tell you what to work on** — don't auto-start

If Jesse asks you to start building the daily probability bands feature, **read `04_NEXT_BUILD_SPEC.md` thoroughly first**, then propose a wave-by-wave plan with explicit approval checkpoints.

---

## 🚫 What NOT To Do

- **Don't auto-commit anything** — every commit needs Jesse's explicit "go"
- **Don't push without `git fetch origin main` first** to check for upstream changes
- **Don't "improve" things without asking** — Jesse explicitly rejected several enhancements (premarket integration, hysteresis band) in the last session
- **Don't confuse `hmm_regime.py` with `regime_classifier.py`** — two different concepts, two different consumers
- **Don't get clever about regime thresholds** — they're tuned for the current market, change only with backtest evidence
- **Don't touch the Portfolio Constitution YAML** — that's the strategic source of truth

---

## 📊 Quick Health Check (For Verification)

When you load the repo, you should see:

- `src/regime_classifier.py` exists (~513 lines)
- `src/config/config.yaml` has a `regime_classifier:` section (~95 lines)
- `src/data_fetcher.py` has `fetch_sector_performance()` function
- `src/data_fetcher.py` has `_FMP_BLOCKED_TICKERS` set
- `src/data_fetcher.py` uses `grades-consensus` endpoint (NOT `upgrades-downgrades-consensus`)
- `src/data_fetcher.py` uses `insider-trading/search` endpoint (NOT `insider-trading-statistics`)
- `src/main.py` imports `classify_portfolio` from `regime_classifier`
- `src/main.py` has STEP 6 with regime classification before execution signals
- Backtest hit rate currently 68% (38/56)

If any of these don't match, something has drifted — STOP and tell Jesse before proceeding.

#End
