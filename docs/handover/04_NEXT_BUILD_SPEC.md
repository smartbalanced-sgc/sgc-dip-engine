# 04_NEXT_BUILD_SPEC.md — Daily Probability Bands Feature

> **Purpose:** Full build specification for the next feature. Read this before writing any code.
> **Status:** Specified and approved. Not yet built.
> **Read this FOURTH** after `01_SESSION_CONTEXT.md`, `02_BUILD_HISTORY.md`, and `03_RATIONALE_AND_NUANCES.md`.

---

## 🎯 Intent (One Paragraph)

Add a collapsible per-stock daily probability bands table to the dashboard. For each stock and each of the 60 days in the Monte Carlo window, show two columns: lower 70% band (dip-side) and upper 60% band (rally-side). Purpose is to give Jesse visibility into how the Monte Carlo conviction evolves day-by-day during the 60-day window, primarily for his occasional swing trade decisions. **The headline dip and rally targets remain the primary action signals.** The daily bands are informational only.

---

## 🧭 Why This Feature

### Jesse's dual use case
- **22-year DCA** — daily bands are mostly noise here (he funds the ISA monthly anyway)
- **Occasional swing trades** — daily bands are useful for entry/exit timing context

### What's already computed but thrown away
The Monte Carlo simulation computes 10,000 paths × 60 days = **600,000 individual price values per stock**. We currently extract 4 summary statistics (dip price, dip date, rally price, rally date) and discard everything else. The daily bands feature surfaces the per-day percentile information without recomputing anything.

### What the bands enable
- "Has the rally already done 60% of its predicted move?"
- "Is the dip target front-loaded (early days) or back-loaded (later days)?"
- "How does conviction evolve through earnings windows?"

---

## ⚠️ CRITICAL CAVEATS (MUST APPEAR ON DASHBOARD)

The dashboard MUST include preamble text at the top of each daily band table (or in the collapsible explainer near the top of the dashboard) explaining the following. **Without these caveats, the feature is misleading.**

### Caveat 1: The cone is NOT a daily prediction

Each day's percentile is a **statistical summary across 10,000 paths**, not "the price on this day." Different paths reach their dip/rally on different days — Day 15's lower-70% band mixes paths that bottomed early with paths that bottomed late.

**Translation:** Do NOT read "Lower 70% on Day 15 = $746" as "MU will be at $746 on Day 15." It is "70% of paths were above $746 at some point by Day 15."

### Caveat 2: You cannot use this as a "buy on Day X" signal

The system isn't structured to predict daily buy windows. The single dip target (e.g., $660 in 60 days) IS the buy target. The daily cone shows when statistics evolve, not when you should act.

### Caveat 3: Different paths take different routes

A single path can both dip deeply AND rally high within 60 days. Day 30's bands don't tell you whether the dip "came first" or the rally — they tell you the percentile range at that moment, mixing all path histories.

### Caveat 4: Regime override still applies

If the stock is in MOMENTUM/SQUEEZE_RISK/BREAKDOWN, the regime override warning on the headline dip target STILL applies. The daily bands inherit this caveat — if the model's dip target is "unlikely to fill," neither is the daily descent toward it.

---

## 🏗️ Technical Specification

### Display format

**Option chosen: Option B from the discussion** — Two columns (lower 70% band and upper 60% band)

**Layout:** HTML table inside a `<details>` block, default closed.

**Example:**
```
▶ 📊 Daily probability bands (60-day window) — click to expand
  
  [Preamble text explaining the caveats]
  
  Day  Date       Lower 70% band   Upper 60% band   Spread
  0    May 13     $766.58          $766.58          $0
  1    May 14     $760.20          $773.40          $13
  5    May 19     $743.10          $791.50          $48
  10   May 27     $733.80          $812.30          $79
  15   Jun 03     $722.40          $832.10          $110
  20   Jun 09     $712.60          $850.50          $138
  ...
  60   Jul 12     $664.60          $904.45          $240
```

**Columns:**
- `Day` — 0 to 60 (60 calendar days but maps to ~42 trading days, see implementation note)
- `Date` — calendar date based on today + N business days
- `Lower 70% band` — the 30th percentile of path prices at this day (70% of paths were above this)
- `Upper 60% band` — the 60th percentile of path prices at this day (60% of paths were below this)
- `Spread` — Upper - Lower, to show widening of uncertainty

### Where the computation happens

**File:** `src/monte_carlo.py`

**Function:** `extract_statistics()` (modify) OR new helper function (cleaner)

**New computation:**
```python
# After existing percentile_low / rally_target / etc. computations:

# §May 14 feature: Daily probability bands for dashboard display
# Compute lower 70% and upper 60% percentiles at EACH day across all paths
# paths shape: (10000, 60)
# Lower 70% = 30th percentile of prices at each day
# Upper 60% = 60th percentile of prices at each day
daily_lower = np.percentile(paths, 30, axis=0)  # shape: (60,)
daily_upper = np.percentile(paths, 60, axis=0)  # shape: (60,)

# Return as a list of (day, lower, upper) tuples for easy templating
daily_bands = [
    {'day': i + 1, 'lower': float(daily_lower[i]), 'upper': float(daily_upper[i])}
    for i in range(len(daily_lower))
]
```

**Return:** Add `daily_bands` field to the stats dict.

### Where the rendering happens

**File:** `src/dashboard_generator.py`

**Where:** Inside the per-stock row, AFTER the existing target/rally lines, BEFORE the analyst consensus row (or wherever it fits best — Jesse can decide visual order).

**Pattern:** Follow the existing `<details>` pattern used for the warnings and backtest sections:

```html
<details class="daily-bands">
    <summary>📊 Daily probability bands (60-day window) — click to expand</summary>
    <div class="db-preamble">
        <p><strong>How to read this:</strong> Each day's percentile is a statistical summary across 10,000 simulated paths, NOT a prediction of the price on that day. Different paths reach their dip/rally on different days — Day 15's bands mix paths that bottomed early with paths that bottomed late.</p>
        <p><strong>Do NOT use as a buy-on-day-X signal.</strong> The headline dip target ($660.79) and rally target ($904.45) above are the primary action levels. These daily bands are informational only.</p>
        <p><em>If this stock has a regime override warning above, those caveats apply here too — the daily descent toward the dip target is just as "unlikely to fill" as the target itself.</em></p>
    </div>
    <table class="db-table">
        <thead><tr><th>Day</th><th>Date</th><th>Lower 70%</th><th>Upper 60%</th><th>Spread</th></tr></thead>
        <tbody>
            <!-- Render rows from daily_bands list -->
        </tbody>
    </table>
</details>
```

### Date calculation

**Trading days vs calendar days:** The Monte Carlo simulates 60 trading days (Mon-Fri, no weekends/holidays). The "Day N" maps to ~N×1.4 calendar days. Use a simple date offset:

```python
from datetime import datetime, timedelta
import numpy as np

today = datetime.now()
trading_dates = []
current = today
n_trading_days = 0
while n_trading_days < 60:
    current = current + timedelta(days=1)
    if current.weekday() < 5:  # Mon-Fri only
        n_trading_days += 1
        trading_dates.append(current.strftime('%b %d'))
```

**Note:** This doesn't account for US holidays. Close enough for display purposes. Don't over-engineer.

### CSS styling

Match the existing dashboard dark theme:

```css
.daily-bands {
    margin-top: 8px;
    background: #1a1f2e;
    border: 1px solid #2d3548;
    border-radius: 4px;
    padding: 6px 10px;
}
.daily-bands summary {
    cursor: pointer;
    color: #88a0c8;
    font-size: 0.82em;
    font-weight: 600;
}
.db-preamble {
    padding: 8px 0;
    color: #c0c5d0;
    font-size: 0.82em;
    line-height: 1.5;
}
.db-preamble p { margin: 4px 0; }
.db-preamble strong { color: #d4dae0; }
.db-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.78em;
    margin-top: 8px;
}
.db-table th {
    background: #2d3548;
    padding: 4px 8px;
    text-align: right;
    color: #88a0c8;
}
.db-table th:first-child,
.db-table th:nth-child(2) {
    text-align: left;
}
.db-table td {
    padding: 3px 8px;
    text-align: right;
    color: #c0c5d0;
}
.db-table td:first-child,
.db-table td:nth-child(2) {
    text-align: left;
}
.db-table tbody tr:nth-child(even) {
    background: #1f2532;
}
```

---

## 📐 Scope Boundaries

### IN scope
- ✅ Per-stock collapsible daily bands table
- ✅ Lower 70% and Upper 60% columns
- ✅ Date column with calendar dates
- ✅ Preamble text with caveats
- ✅ CSS matching existing dark theme
- ✅ Default collapsed (don't expand by default)
- ✅ Should work for all 28 modeled stocks (NOT for skipped stocks IGLN.L/RR.GB/BARC.GB)
- ✅ Should respect regime override warnings (reference them in preamble)

### OUT of scope (don't build, don't suggest)
- ❌ Per-day BUY/SELL markers (false precision)
- ❌ Color-coded "buy zone" highlighting (encourages misuse)
- ❌ Visual chart (HTML table is sufficient — chart is future enhancement)
- ❌ Daily probability of dip-fill (different computation; not requested)
- ❌ Multiple percentile bands (just 2 — lower 70% and upper 60%)
- ❌ Per-day rally/dip date predictions (mixes paths incoherently)
- ❌ Changes to Monte Carlo logic (only add new statistic, don't alter existing)
- ❌ Changes to signal generation (signals stay the same)
- ❌ Changes to backtest (backtest only sees primary targets, not daily bands)

---

## 🧪 Testing Approach

### Manual smoke test
After building, run `python3 main.py` locally and verify:

1. **No regression:** All existing dashboard elements still render correctly
2. **Daily bands appear:** Each modeled stock has a `<details>` block with the daily bands
3. **Default collapsed:** Dashboard isn't visually noisy on initial load
4. **Numbers make sense:** Day 0 lower ≈ upper ≈ current price (very low spread). Day 60 spread should be wide.
5. **Date column correct:** Today, plus business days for each subsequent row
6. **Preamble visible** when expanded
7. **CSS matches** the existing dashboard style (dark theme, readable)

### Specific verification on MU
MU has regime override active. After building:
- Open the dashboard
- Expand MU's daily bands section
- Verify the preamble mentions that regime override warnings apply
- Numbers should evolve: Day 0 $766/$766 → Day 60 $660/$905 (roughly matching headline dip/rally)

### Edge cases to verify
- Stocks with the new low-price warning (ENGN at $1.44, GDC at $0.14) — make sure formatting doesn't break for sub-$1 prices
- ASML in EUR — make sure the currency symbol is consistent (€ throughout)
- LDO.MI — should work even though candle data is older (Eulerpool quirk, separate issue)

### Definition of done
Five visible criteria:
1. ✅ Daily bands collapsible exists on every modeled stock
2. ✅ Preamble explains the caveats clearly
3. ✅ Numbers in the table match Monte Carlo output (sanity: Day 60 lower ≈ headline dip target; Day 60 upper ≈ headline rally target)
4. ✅ Existing dashboard functionality unchanged (regime badges, BUY/WAIT signals, etc.)
5. ✅ Backtest hit rate unchanged (numerical proof the underlying logic is untouched)

---

## 📂 Implementation Plan (Wave-By-Wave)

### Wave 1: Compute daily bands in Monte Carlo

**File:** `src/monte_carlo.py`

1. Modify `extract_statistics()` to also compute daily bands
2. Add `daily_bands` to return dict
3. Test: print one stock's daily_bands and verify shape (60 entries, each with day/lower/upper)

**Approval gate:** Show Jesse the output structure before continuing.

### Wave 2: Render daily bands in dashboard

**File:** `src/dashboard_generator.py`

1. Add the `<details>` block rendering inside per-stock row
2. Add CSS for `.daily-bands`, `.db-preamble`, `.db-table`
3. Build preamble text with caveats
4. Generate dates server-side (don't use JS)
5. Test: open dashboard locally, verify visual

**Approval gate:** Show Jesse the dashboard PDF before committing.

### Wave 3: Commit and push

**Conditional on Jesse's explicit approval.** Follow CLAUDE.md commit checklist:
- Tests passed locally
- Diff reviewed
- Dashboard verified
- "push it" given

---

## 🛡️ Things That Could Go Wrong

### 1. Page size bloat
60 rows × 28 stocks = 1,680 rows of table HTML. Plus CSS. Likely adds ~80-100 KB to the dashboard.
**Mitigation:** Default collapsed means the rows are still in HTML but not rendered visually. Browser handles this fine. File size still grows.
**Acceptance:** ~150-200 KB total dashboard size is acceptable.

### 2. Percentile calculation slow on 28 stocks
`np.percentile(paths, [30, 60], axis=0)` per stock × 28 stocks = 56 percentile computations.
**Reality check:** This is fast (~50ms total). Negligible compared to FMP fetch (10+ seconds).
**No mitigation needed.**

### 3. Sub-$1 stocks (ENGN, GDC) — formatting issues
**Mitigation:** Use `.2f` formatting universally. Sub-$1 prices like $0.11 should render fine.
**Test:** Verify ENGN/GDC daily bands render correctly.

### 4. Currency mismatch (LDO.MI in EUR, ASML in EUR)
**Mitigation:** Pass currency symbol to the template, use it for both headline and daily bands.
**Test:** Verify LDO.MI and ASML show € throughout daily bands.

### 5. Misinterpreting the bands as predictions
**This is the BIGGEST risk.** The caveat preamble exists exactly to mitigate this.
**Triple-check:** Preamble text is clear, prominent, mentions "NOT a daily prediction" explicitly.

---

## 📝 What The First Message To Jesse Should Look Like

When Jesse asks you to build this feature, your first response should:

1. Confirm you've read this spec
2. Restate the goal in 1-2 sentences
3. Propose the 3-wave plan with approval gates
4. Ask if he wants any spec adjustments before Wave 1 begins

**Example opening:**

> Jesse, I've read the daily probability bands spec in `docs/handover/04_NEXT_BUILD_SPEC.md`.
>
> **Goal in one sentence:** Add a collapsible per-stock table showing daily lower-70% and upper-60% percentile bands across the 60-day window, with explicit caveats that these are statistical summaries (not daily predictions).
>
> **[Sonnet]** complexity (new feature, real architectural change in `monte_carlo.py`).
>
> **3-wave plan:**
> - Wave 1: Compute `daily_bands` in `extract_statistics()` in `monte_carlo.py`
> - Wave 2: Render `<details>` block in dashboard with preamble + table + CSS
> - Wave 3: Test locally + commit + push (after your "go")
>
> Want me to start Wave 1, or do you have spec adjustments first?
>
> #End

---

## 🎯 Closing Note

This is a **focused, well-scoped feature**. ~60 lines of code, 2 files. It exposes data the Monte Carlo already computes but currently discards. It's display-only — no behavioral changes to the system.

**The biggest risk is scope creep.** When you start building, you'll be tempted to add "just one chart" or "just per-day signals." **Don't.** Build the table. Add the caveats. Test. Push. Done.

If Jesse asks for enhancements after seeing it work (like a visual chart), that's a **separate future build** — fresh spec, fresh wave plan, fresh approval. Don't bundle it in.

#End
