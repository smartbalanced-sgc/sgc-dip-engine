# 03_RATIONALE_AND_NUANCES.md — The Why Behind The What

> **Purpose:** Decisions, lessons learned, alternatives rejected, gotchas, and the reasoning behind each choice. This is the document that prevents future-you from "improving" things in ways that break the system's design intent.
> **Read this THIRD** after `01_SESSION_CONTEXT.md` and `02_BUILD_HISTORY.md`.

---

## 🎯 Why The Regime Classifier Exists (The Origin)

### The MU trade that started it all

Before the regime classifier, the system would consistently produce WAIT signals for MU like:
```
MU: WAIT - Deep 13.8% dip expected (70% conviction). Hold firm. Wait.
```

These signals never filled. The backtest hit rate for MU was 0% (0/5 WAIT signals). The system was telling Jesse to wait for dips that fundamentally weren't coming during a multi-week momentum rally.

**Diagnosis:** Monte Carlo assumes mean-reversion using 500 days of historical volatility. In a momentum regime, the stock is NOT mean-reverting — it's trending. The Monte Carlo model is technically valid (it computes percentiles correctly), but its assumption (mean-reversion) is broken for this regime.

**Solution:** Add a layer ABOVE Monte Carlo that detects when the mean-reversion assumption is violated, and modulates the signal accordingly. This is the regime classifier.

**Validation:** Jesse's MU trade (entered $750, exited $812 in 4 days, +$310/£237 realized) was a real-world test of whether the new regime classifier would catch the "don't wait for the dip" scenario. **The system correctly flagged MU as MOMENTUM 80% confidence and explicitly said "predicted dip unlikely to fill."** Jesse held, the dip didn't come, he took profit at the rally instead. The regime classifier validated.

---

## 🧠 Design Decisions And Their Alternatives

### Decision 1: Rule-based classification + AI enrichment for ambiguous cases

**Alternative considered:** Pure AI classification of all stocks every run
**Why rejected:**
- Cost would be ~$5-10/day vs current ~$0.50-2/day
- AI is noisy — different searches return different conclusions
- Rule-based logic is auditable; AI is opaque
- AI confirmed unnecessary for NORMAL/OVERSOLD/BREAKDOWN — only MOMENTUM/SQUEEZE need disambiguation
**What we built:** Rules classify all 28 stocks (free). AI only called for MOMENTUM/SQUEEZE_RISK (~2-3 stocks/day, ~$0.50-2/day)

### Decision 2: Two distinct regime modules (hmm_regime.py vs regime_classifier.py)

**Alternative considered:** Unified regime detector returning both pieces of info
**Why rejected:**
- They serve different consumers (Monte Carlo vs execution_logic)
- They use different time horizons (hmm uses long-term, classifier uses short-term)
- They have different outputs (bull/sideways/drawdown vs NORMAL/MOMENTUM/etc)
- Combining them would create false coupling
**What we built:** Two separate modules. **DO NOT CONFUSE THEM.** This is the most likely source of future confusion when adding features.

### Decision 3: 70th percentile target (PERCENTILE_TARGET = 70)

**Why this value:** Backtest hit rate target is 65% — we're currently running 68% which is above target. The dashboard even flags this: "Raise PERCENTILE_TARGET (hit rate 68% above 65% target)" suggesting we could go to 75 or 80 for higher hit rate.
**Why we haven't raised it:** Trade-off between hit rate and depth. Raising percentile → shallower dip targets → higher hit rate BUT smaller reward when hit. Current value is a balanced compromise.
**Future consideration:** Could be Jesse's call after the daily probability bands feature exposes the daily evolution data.

### Decision 4: Insider data value-weighted, not count-weighted

**Alternative considered:** Count purchases vs sales (e.g., "5 sales, 2 purchases = net selling")
**Why rejected:**
- A single $50M CEO sale carries more signal than 5 director gifts
- Count-weighting treats all transactions equal — wrong
**What we built:** Value-weighted ratio `purchases_value / (purchases_value + sales_value)`. 0=all selling, 1=all buying, 0.5=neutral.

### Decision 5: 30-day insider window

**Alternative considered:** 90-day window (more data) or 7-day window (more recent)
**Why 30-day:**
- 7-day too short for sparse insider activity (might miss everything)
- 90-day too long — signals decay (a Jan 2026 sale doesn't predict May 2026 prices)
- 30-day = monthly granularity, matches DCA rhythm, reasonable signal half-life
**Note:** If the AI research mentions insider activity outside 30 days, that's separate context.

### Decision 6: AI cache TTL of 24 hours

**Alternative considered:** Cache for 7 days (one call per stock per week)
**Why 24h:**
- News breaks daily; classifications can change overnight
- 7-day cache would miss earnings announcements, M&A news, regulatory events
- Cost is acceptable: ~$0.08 per AI call × 2-3 stocks/day = ~$0.20/day total
**What we built:** 24h TTL. Cache file: `data/regime_ai_cache.json`. Survives across runs.

### Decision 7: Filter only P-Purchase + S-Sale (drop all other insider transaction types)

**Why:** The other 16 types (A-Award, M-Exempt, F-InKind, C-Conversion, etc.) are mechanical/compensation/estate events — they don't represent insider conviction about valuation. Including them as signal would inject noise.
**Verification:** Reviewed all 18 types from FMP docs. Only P and S represent voluntary directional decisions.

### Decision 8: Suppress fallback BUY in suppress_buy regimes

**Original behavior:** When primary signal is WAIT, system shows a "fallback BUY at higher conviction" alternative
**Problem in regime context:** Saying "dip-buy disabled, this won't fill" + "fallback BUY at $X if you want it sooner" is **internally contradictory**
**Why this matters:** A trader reading both lines might act on the fallback (more conservative target → tempting) and ignore the regime warning. That's exactly the failure mode the classifier exists to prevent.
**What we built:** When regime is suppress_buy, the fallback line is hidden entirely.

---

## 🚫 Decisions Explicitly Rejected (Don't Re-Litigate)

### Premarket / Live Price Integration

**Discussed extensively in last session. Decision: NO.**

**Reasoning:**
- Monte Carlo uses 500 days of close-to-close history. Premarket prices don't improve predictions.
- Backtest comparisons must stay consistent (all close prices).
- Premarket prices are noisy (low volume, easily moved by single trades).
- Updating dip% based on premarket would cause BUY/WAIT signals to flap day-to-day.
- The "freshness" benefit is cosmetic only — no signal quality improvement.

**Jesse considered building it for swing trades, then decided against** after understanding the architecture risk: it would muddy the long-term DCA system to serve the occasional swing trade. He may build a separate system for swing-trade-specific premarket awareness in the future.

**If a future session tries to add premarket: STOP. This is sacred. Jesse explicitly rejected it on May 13, 2026.**

### 0.5% Hysteresis Band On BUY/WAIT Threshold

**Status:** Identified as a real issue. Not yet built. Jesse said "future consideration."

**The problem:** Stocks parked at the 3% materiality threshold (e.g., WM at 3.0% one day, 3.1% the next) oscillate between BUY and WAIT based on Monte Carlo noise. Not a bug, but visually noisy.

**Why not built yet:** Real architectural decision (where does the hysteresis state get stored? CSV? Memory?). Worth doing properly when we have backtest data to validate the band width. Don't rush this.

### Per-Day Buy/Sell Recommendations

**Discussed in daily bands feature scoping. Decision: NO.**

**Why:** The Monte Carlo isn't structured to predict daily buy windows. The signal at the 60-day horizon is the actual prediction. Daily percentile bands are *informational* (how conviction evolves), not actionable. Don't add per-day BUY/SELL markers — they'd be false precision.

---

## 🔬 The Critical Caveat About Monte Carlo Percentiles

This is **the most important conceptual understanding** in the system, and the most easily misread:

### What 70% Conviction Actually Means

**It does NOT mean:** "70% chance MU will dip 13.8%"

**It DOES mean:** "Of 10,000 simulated 60-day paths, 70% touched ≤ $660 at SOME point during the window"

### What 60% Rally Conviction Means

**It does NOT mean:** "60% chance MU will rally 18%"

**It DOES mean:** "Of 10,000 simulated paths, 60% touched ≥ $904 at SOME point during the window"

### Why 70% + 60% Can Sum > 100%

**Same paths can be in BOTH buckets.** A path that dipped to $640 on Day 10 then rallied to $920 on Day 45 is:
- ✅ In the dip bucket (touched ≤ $660)
- ✅ In the rally bucket (touched ≥ $904)

The percentages measure overlapping subsets, not mutually exclusive ones.

### How This Affects The Daily Bands Feature

The daily bands feature (next build) MUST include a preamble that explicitly explains this. Without it, the bands look like a "buy at X on Day Y" guide, which they aren't. Different paths reach their dip/rally on different days — Day 15's "lower 70%" statistic mixes them all.

**This caveat is in the dashboard's conviction explainer already (collapsible block).**

---

## 🛡️ Lessons Learned (Mistakes To Avoid)

### Lesson 1: Don't assume endpoint behavior without testing
**What happened:** I assumed `insider-trading-statistics` was a dead endpoint after seeing 2 empty responses. Jesse pushed back. We tested 2 alternative params + a second ticker — still empty. THEN I drafted an FMP support inquiry. THEN Jesse found `insider-trading/search` returns rich data.
**Takeaway:** Empty response from an API doesn't mean the endpoint is dead. Could be wrong path, wrong params, wrong tier, wrong filtering. **Test exhaustively before concluding.**

### Lesson 2: Documentation says X, FMP behaves Y
**Example:** FMP's `historical-sector-performance` docs don't clearly require `sector` param. Bulk calls return HTTP 400. Date params aren't optional — without them you get stale 2024 data.
**Takeaway:** When integrating FMP endpoints, **test with curl first** before writing code. Verify the response format, required params, tier requirements.

### Lesson 3: Stale local code is a real risk
**What happened:** Jesse's local repo was a downloaded copy, not a git clone. Multiple files had drifted from GitHub silently. We hit cascading ImportErrors.
**Takeaway:** First diagnostic check on a new session: `git status` and `git remote -v`. If it's not a git repo, fresh clone before anything else.

### Lesson 4: f-strings with conditionals in format spec are invalid Python
**Code that failed:** `f"{rsi:.0f if rsi else 'N/A'}"`
**Why:** Format spec doesn't allow inline conditionals
**Fix:** Pre-compute `rsi_str = f"{rsi:.0f}" if rsi is not None else "N/A"` before the f-string

### Lesson 5: Classification order matters even when conditions seem orthogonal
**What happened:** OVERSOLD_REVERSAL and BREAKDOWN have overlapping conditions (both can match a deeply oversold stock). Without correct ordering, all capitulation oversold setups misclassified as BREAKDOWN.
**Takeaway:** When designing rule-based classifiers with overlapping conditions, **define ordering explicitly and test edge cases** (capitulation vs grinding decline is a real distinction).

### Lesson 6: "I'll just write a quick patch" is dangerous
**Pattern observed multiple times:** Quick patch → real bug discovered → bigger refactor needed. Each "small fix" surfaced a related issue.
**Takeaway:** Use the **wave-by-wave pattern**. Even for small fixes, plan the wave: restate task, list files, list changes, wait for approval, then act.

### Lesson 7: Cosmetic fixes can hide real architectural questions
**Example:** The orphan `·` in MU's regime note was 2 lines to fix, but the deeper issue was concatenation of two strings from two different files (regime_note from execution_logic + ai_extra from dashboard_generator) without buffer logic. The cosmetic fix worked, but the underlying contract could be cleaner.
**Takeaway:** When you find a cosmetic bug, ask: is this a one-line fix or is it papering over a contract issue? Sometimes the right answer is the quick fix, sometimes it's a refactor — be honest about which.

### Lesson 8: AI cache can mask AI variance
**What happened:** Two consecutive runs showed different LDO.MI thesis labels (INTACT → AT_RISK → INTACT) from AI search. The cache stabilizes this for 24h, but if a search runs fresh again the same day, you can see flip-flopping.
**Takeaway:** AI is non-deterministic. Cache it appropriately, don't over-index on a single label, and design display logic that doesn't break if a label changes between sessions.

---

## ⚖️ The "Don't Be A Yes-Man" Principle

Jesse explicitly flagged this in his working rules: when challenged, the AI should validate independently, not appease.

**Pattern that emerged in this session:** Jesse challenged my analysis multiple times. Each time:
- Sometimes he was right (e.g., "your endpoint diagnosis was based on too few test cases")
- Sometimes I was right but needed to explain better (e.g., why premarket integration doesn't help signal quality)
- Sometimes we discovered a new option together (e.g., the `insider-trading/search` endpoint)

**The principle:** When Jesse pushes back, **re-examine your own reasoning honestly**. If you were wrong, say so clearly. If you were right, explain better. Don't fold to social pressure.

**Specific examples from the session:**
- I wrongly concluded `insider-trading-statistics` was a dead endpoint. Jesse pushed back. I drafted FMP support inquiry instead of giving up. Jesse found the right endpoint. **My fault for jumping to conclusions.**
- I initially recommended against premarket integration. Jesse argued for it (his active trading use case). I reversed my position thoughtfully (real signal for swing trades). Jesse then decided against it himself (architectural risk to long-term system). **Both of us updated based on new information.**

This is healthy. Plan to push back honestly and accept honest pushback in return.

---

## 🎓 Specific Technical Lessons For Future Builds

### The right way to test FMP endpoints
1. `curl -s "https://financialmodelingprep.com/stable/<endpoint>?symbol=X&apikey=$FMP_API_KEY" | head -c 500`
2. Try with and without optional params
3. Try multiple tickers
4. Check for empty array vs error vs 402 vs 404
5. THEN write code

### When to use AI vs rules
- **Rule-based:** Cheap, fast, deterministic, auditable. Use for first-pass classification.
- **AI:** Expensive, slow, non-deterministic. Use for disambiguation when rules can't fully decide.
- **Don't:** Use AI for things rules can do. Don't use rules for things requiring real-world context.

### Backward compatibility patterns
When extending a function's return value, preserve old fields explicitly:
```python
return {
    # Backward-compat fields (consumed by existing code)
    'acquiredDisposedRatio': ratio,
    'change': change_map[net_direction],
    
    # New fields (additive only)
    'purchases_30d': purchases_count,
    # ... etc
}
```
This pattern saved us from breaking `monte_carlo.py` and `sentiment.py` when we rewrote insider stats.

### Annotation Mandate
Every logical check (`if rsi >= sq_cfg.get('rsi_min', 80)`) should have a comment citing the Constitution or config section:
```python
# §regime_classifier.squeeze_risk.rsi_min — extreme overbought threshold
if rsi is not None and rsi >= sq_cfg.get('rsi_min', 80):
    ...
```
This makes the code self-documenting and traceable to design intent.

---

## 🧬 The Constitutional Hierarchy

For the SGC system, here's the priority order when decisions conflict:

1. **Portfolio Constitution v7.1** (strategic source of truth)
2. **CLAUDE.md** (operational mandate for AI assistance)
3. **YAML config** (`src/config/config.yaml` — tactical thresholds)
4. **Code** (last priority — should reflect 1-3, not contradict them)

**If code disagrees with YAML:** YAML wins. Update code.
**If YAML disagrees with Constitution:** Constitution wins. Discuss with Jesse before updating YAML.
**If you find yourself wanting to change the Constitution:** Stop. That's a Jesse decision after deep deliberation.

---

## 📌 Closing Thoughts For Future Sessions

The build that just shipped (regime classifier with AI research) was substantial — 10 files, 1681 insertions, ~12 hours of focused work. It validates a specific failure mode the system had: misclassifying momentum stocks and producing dip signals that never filled.

The next build (daily probability bands) is **smaller and more focused** — ~60 lines, 2 files, primarily display logic. Don't let scope creep. The temptation will be to add "just one more thing" like premarket prices or per-day signals. **Resist.** The current build is intentional. Stay narrow.

If you find yourself wanting to refactor something that's already working, **stop and ask Jesse**. The system has years of design decisions baked in. "I think I can simplify this" is almost always a red flag.

#End
