# 01_SESSION_CONTEXT.md — Where We Are

> **Purpose:** Orient a new Claude session on the current state of the SGC Dip Engine, what was just shipped, and what's next on the agenda.
> **Read this FIRST** after CLAUDE.md.

---

## 🎯 Current Build State

**Last shipped commit:** `f548a58 — Merge main into feature branch + clean up config` (merge commit)
**Last shipped FEATURE commit:** `efbff25 — Fix Test B + persist research evidence archive`
**Pushed to GitHub:** May 14, 2026
**GitHub Actions status:** Will pick up new code on next scheduled cron run

### What's Live On GitHub Right Now

Two regime-aware layers + a research evidence archive:

**1. Per-stock trade regime classifier** (shipped May 13). Classifies each of the modeled stocks (currently ~36; 5 added manually on 2026-05-14: CSCO, SNAL, ALP, QUCY, IONQ) into one of 5 trade execution regimes and modulates BUY/WAIT signals.

- ✅ Regime classifier core module (`src/regime_classifier.py`)
- ✅ AI research integration via Anthropic web search
- ✅ Sector decoupling, insider data pipeline, FMP endpoint corrections
- ✅ Dashboard regime badges + override annotations + conviction explainer
- ✅ Fallback BUY suppression, CSV archive schema

**2. Daily probability bands feature** (shipped May 14). Per-stock collapsible 60-day cone tables on the dashboard.

- ✅ `extract_statistics()` computes daily 30th/60th percentiles per stock (`src/monte_carlo.py`)
- ✅ `daily_bands` propagated through `simulate_portfolio()` → `execution_logic.py` → `dashboard_generator.py`
- ✅ Collapsible `<details>` block per stock with preamble explaining caveats
- ✅ Preamble explicitly flags the wrinkle: Day-60 lower will NOT match the headline dip target (different statistics — minima vs per-day distribution)
- ✅ Tied to YAML thresholds (`signal.percentile_target`, `signal.rally_conviction_percentile`)
- ✅ Display-only — no behavioural changes to signals, MC, or backtest

**3. Regime classifier backtest research tool** (shipped May 14). Standalone on-demand script.

- ✅ `research/regime_backtest.py` evaluates current rule (R0) vs three alternatives across portfolio + S&P 100 universes
- ✅ Test A (forward return separation) and Test B (dip-fill rate) implemented; Test B bug fix shipped in `efbff25`
- ✅ Captures live MU prediction for forward evaluation (see Forward Evaluation below)
- ✅ Dated verdict snapshots in `docs/research/` (see `docs/research/README.md` for the policy)

---

## 🚧 What's Next

**No active feature build.** The previously-spec'd daily probability bands feature is now shipped.

### Forward evaluation dates (action items with deadlines)

| Date | Action | Why |
|---|---|---|
| **2026-06-13** | Re-run `python3 research/regime_backtest.py` | MU was labelled NORMAL on 2026-05-14 despite RSI 81 / +20% 5d. Forward evidence (rally or fade) determines whether the rule produced a false negative. Append a follow-up snapshot to `docs/research/`. |

### Latent considerations (not blocking, no deadline)

- **Hysteresis band on BUY/WAIT threshold:** identified architectural issue (stocks at the 3% materiality boundary flap between BUY/WAIT day-to-day). Real but not urgent. Backtest evidence needed before changing.
- **Test B confirmation:** the dip-fill rate test was buggy in the 2026-05-14 run (all zeros); fix shipped in `efbff25`. Next backtest run will produce the first valid Test B results.
- **Watchful for:** real-money regime classifier failures (false negatives or false positives) in the next 60 days that change the verdict in `docs/research/2026-05-14_regime_classifier_backtest.md`.

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

The daily probability bands feature is SHIPPED (2026-05-14); the spec is archived at `04_NEXT_BUILD_SPEC_DEPLOYED.md`. If Jesse proposes a NEW feature, follow the same wave-by-wave pattern with explicit approval checkpoints.

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
