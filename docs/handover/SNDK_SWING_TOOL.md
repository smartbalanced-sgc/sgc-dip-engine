# SNDK Swing Tool — Operating Handover

> **READ THIS BEFORE TOUCHING THE SWING TOOL OR EVALUATING ITS DAILY OUTPUT.**
>
> This document captures ~50 hours of design, audit, and iteration work for the
> SNDK swing-trade decision tool (`tools/swing_analyzer_analytic.py
> --check-thesis`). It is the authoritative spec. Do not re-litigate locked
> decisions. Do not redesign without explicit approval from Jesse.

---

## 1. CONTEXT — what this tool is for

Jesse holds **10 SNDK shares at $1,490 cost basis** (purchased as a swing trade
based on the parent SGC dip engine's signal). Currently underwater. He wants
to exit profitably (or at minimum at break-even) within a **60 trading-day
patience window**, with **no automatic stop loss**. The tool answers:

> **"What's the HIGHEST sell-limit price X where the model's Monte Carlo gives
> at least 65% probability of touching X within 60 days?"**

If X >= $1,490 (break-even), HOLD with sell-limit at X. Otherwise CUT.

This is NOT the SGC dip engine. The SGC dip engine answers a different
question (where to deploy monthly DCA for 22-year compounding across ~38
stocks). This tool is single-position deep-dive for swing-trade decisions.

---

## 2. LOCKED DESIGN DECISIONS — DO NOT RE-LITIGATE

These decisions were made through extensive iteration, audited by two
independent agents + verified math, and locked. Re-opening them creates
audit-loops that waste cycles and degrade quality.

| Decision | Value | Why locked |
|---|---|---|
| Decision framework | Multi-target conviction scan | NOT EV-optimization (v3 was misaligned with use case) |
| Conviction threshold (default) | **65%** | Investor-grade for high-stakes swing trades; not 60% (too thin) or 70% (paternalistic conservatism bias) |
| Sigma estimation | Triangulated from 5 anchors: GARCH + realized vol (30/60/90d) + yfinance options IV | Multi-source robust; single-source GARCH was unreliable |
| Drift estimation | 9 quality-gated signals, weighted-blended, Bayesian-smoothed | Replaces single-source historical that was extrapolation-biased |
| Verdict logic | HOLD with sell-limit @ X_aggressive (point P >= threshold); TRIM if marginal; CUT if decisive | Replaces cushion/EV bands which didn't match Jesse's decision rule |
| Output tiers | X_safe (lo68 CI >= threshold) + X_aggressive (point >= threshold) | No X_stretch (audit found it's a fantasy target) |
| Bayesian posterior | Drives the verdict (not today's blend) | Smooths daily noise; uses prior + today variance-weighted |
| Default sell-limit | X_aggressive | More conservative X_safe shown as alternative |
| Peers for relative strength | **MU + WDC** | Direct memory competitors; tight peer set |
| AI synthesis model | **Claude Opus 4.7** | High-stakes single-ticker analysis warrants Opus over Sonnet |
| AI position_guidance rules | v4-aligned (input-anti-double-counting rule) | v3-era drift-threshold rules removed |
| Block bootstrap MC | NOT included | Audit found regime-bias with limited post-IPO data |
| Multi-step vol forecast | NOT included | Audit found model risk > accuracy gain |
| Synthesized reliability score | NOT included | Black-box with subjective weights; show components separately instead |
| Threshold spectrum table | NOT included | Anchoring bias toward middle |
| Vol-adaptive threshold bump | NOT included | Overrides user's stated rule |

**Anti-pattern: do not propose "improving" any of the above without explicit user request. Each was deliberately chosen after audit. Re-litigation = quality regression.**

---

## 3. HOW TO RUN

### Command (Mac, after market close)

```bash
cd ~/sgc/sgc-dip-engine
python3 tools/swing_analyzer_analytic.py --check-thesis SNDK \
  --entry 1490 --shares 10 --target 1600 --horizon 60 --show-rationale
```

### Recommended shell alias (one-time setup)

```bash
echo "alias sndk='cd ~/sgc/sgc-dip-engine && python3 tools/swing_analyzer_analytic.py --check-thesis SNDK --entry 1490 --shares 10 --target 1600 --horizon 60 --show-rationale'" >> ~/.zshrc
source ~/.zshrc
```

Then just type `sndk` in terminal.

### Required environment variables (already in ~/.zshrc)

- `FMP_API_KEY` — Financial Modeling Prep (Starter plan confirmed; press-releases is Premium-only and intentionally not called)
- `ANTHROPIC_API_KEY` — Claude API (Opus 4.7 model)

### Run frequency

**Once per day, after market close (21:30-23:00 UK time).** Why this window:
- US market closes 21:00 UK
- SGC daily cron runs at 21:30 UK
- Running 22:00-23:00 UK gets fresh end-of-day data
- Avoid multiple same-day runs (Bayesian same-day artifact — see Section 7)
- Skip weekends/holidays (no new data)

### Cost per run

~$0.75-$1.40 per execution (Anthropic Opus + web search). ~$22-42/month at daily cadence. Acceptable for $14.9k position.

---

## 4. THE /sndk MAGIC-WORD WORKFLOW

Jesse runs the tool on his Mac (~30 seconds + cost). Pastes the full output
into a Claude session with a simple message: `/sndk` or "daily check" or
"god mode SNDK".

When Claude sees the output + magic word, it synthesizes:

1. **One-line verdict** (HOLD/TRIM/CUT + recommended sell-limit price)
2. **What changed vs yesterday** (verdict shift, X_aggressive shift, signal changes)
3. **Flags worth attention** (regime change, AI disagreement, hysteresis warning)
4. **Action recommendation** (place sell-limit / adjust / no change)

Total Jesse time per day: ~3 minutes (run + paste + read response).

### Why Claude does NOT run the tool itself from chat

- API keys are in Jesse's local `~/.zshrc`, not in any container Claude has access to
- Each run costs real money on Jesse's accounts
- Mac is already set up; redundant to duplicate execution path
- Maintains separation: Mac executes, Claude synthesizes

If Jesse asks Claude to "run /sndk" without pasting output, Claude should say:
> "I can't run the tool directly — your API keys are on your Mac. Run `sndk` in your terminal and paste the full output here. I'll synthesize."

---

## 5. OUTPUT INTERPRETATION

The tool produces ~200 lines of structured output across 10+ sections:

| Section | What to look at |
|---|---|
| Inputs | Spot price, sigma, RSI, momentum, YTD — confirms data is fresh |
| Regime & Vol Advisory | Current regime label, vol level. POST_PARABOLA = mean-reversion risk |
| Earnings Calendar | Next earnings date. If "IN HORIZON" → gap risk warning matters |
| Sigma Triangulation | 5 anchors clustered tight = high confidence; wide spread = uncertainty |
| Forward Drift Intelligence | 9 signals + blended drift + 68% CI. Look at dispersion warning |
| Bayesian Belief Update | Today's posterior. The Bayesian weight % (prior vs today) tells you smoothing strength |
| AI Analyst Synthesis | Drift estimate, sources cited, bull/bear factors, AI position view |
| **Multi-Target Conviction Scan** | **THE HEADLINE — full curve of P(touch X) with CI bounds at every X** |
| **Verdict** | **HOLD with sell-limit @ X_aggressive (or TRIM/CUT)** |
| Sensitivity at X_aggressive | How much drift swing flips the verdict |
| Path-dependent risk metrics | Max DD distribution, time-to-target, panic touch probability |
| Reliability Components | Separate items, NOT synthesized. You assess each. |

The 19-column CSV at `tools/output/thesis_history_SNDK.csv` auto-appends each
run. Track the trend over days, not single-day verdicts.

---

## 6. WHEN TO ACT / NOT ACT

| Trigger | Meaning | Action |
|---|---|---|
| Same verdict 2+ days | Stable signal | No action (default state) |
| Verdict shift (HOLD→TRIM) for 2+ consecutive days | Real conviction breakdown | Re-evaluate; consider trimming |
| Verdict shift (HOLD→CUT) for 2+ consecutive days | Decisive breakdown | Cut |
| X_aggressive drops >$50 in one day | Major downward signal shift | Pay attention; tomorrow confirms or denies |
| P(touch BE) falls below 65% for 2+ days | Even break-even now below conviction | Strong signal to cut |
| Sigma spikes >130% | Vol regime change (panic) | Cut immediately — model breaks down here |
| AI position view flips with NEW specific evidence | Real catalyst | Read AI rationale; weigh against math |
| Hysteresis warning fires | Suspicious single-day flip | Wait one more day for confirmation |
| Sell-limit HITS at the set price | Mission accomplished | Profit booked; redeploy capital |

**Most days are "no action."** That's normal. The tool is a monitor, not a
trade-firing engine.

---

## 7. KNOWN MINOR ISSUES (low priority, don't urgently fix)

### Bayesian same-day artifact

The Bayesian update treats consecutive runs as "new evidence" even when run
on the same day (same underlying data). Across 3 same-day runs the std
narrowed 20.5 → 15.0 → 12.4 pp on identical inputs. This is artificial — the
"new" data is the same Friday close.

**Mitigation:** Don't re-run multiple times per day. Once after close is enough.

**Fix (deferred):** Could add logic to skip Bayesian update if prior_age == 0
(same calendar day). Not urgent; doesn't change the verdict.

### Path metrics at high vol

The path metrics (max drawdown, panic touch probability) come from GBM
simulation which doesn't model fat tails or jumps. At σ=96% the GBM
distribution understates extreme tails. The reported "60% chance of $1,100
touch" is probably an underestimate; real-world could be 65-70%.

**Mitigation:** Treat path metrics as DIRECTIONAL ("yes, significant
drawdown risk"), not precise predictions.

### Sigma cap on sector signal

`signal_from_sector` caps annualised sector momentum at +60% in POST_PARABOLA
regimes (was +150% in normal regimes). This is a deliberate dampening of an
otherwise-extrapolative signal. Resulting sector drift contribution to the
blend is muted by design.

---

## 8. ANTI-PATTERNS — what NEW Claude sessions must NOT do

Hard-won from this session's iteration loop. Each item below was a real
mistake that wasted cycles or biased output:

1. **DO NOT bump the conviction threshold above 65%** citing "vol safety" or
   "CI uncertainty." Audit confirmed this is paternalistic AI conservatism
   that overrides Jesse's stated risk tolerance and costs ~$700 of upside.

2. **DO NOT re-introduce EV-cushion math** as the verdict driver. The v3 EV
   framework was misaligned with Jesse's decision rule and has been
   deliberately removed. EV/cushion concepts may appear in legacy auxiliary
   modes (`standalone_mode`) but never in `check_thesis_mode`.

3. **DO NOT propose adding block bootstrap MC.** Audit found that with only
   ~314 post-IPO days dominated by parabolic regime, block bootstrap injects
   regime-specific bias rather than reducing it.

4. **DO NOT propose multi-step vol forecast.** Audit found parameter SEs on
   a 14-month IPO are too wide; multi-step amplifies error.

5. **DO NOT create a synthesized reliability score** (HIGH/MEDIUM/LOW).
   Audit found subjective weights = black-box output. Show reliability
   components separately so Jesse sees each independently.

6. **DO NOT create an audit-loop pattern.** Every audit finds something.
   Sequential audits create indecision. Audits happen ONCE per major change,
   findings get committed, then we move on. If you find new issues mid-build,
   batch them for the next iteration rather than blocking the current one.

7. **DO NOT ask Jesse to validate technical choices.** He hired the AI for
   expertise. Make the call, defend it, commit. Threshold value, peer set,
   weight choices — these are AI-domain decisions. Only ask Jesse for
   USER-DOMAIN choices: which tickers to track, whether to add complexity,
   risk tolerance.

8. **DO NOT propose changes that touch sacred files** (`src/monte_carlo.py`,
   `src/regime_classifier.py`, `src/config.yaml`, etc.) when working on the
   swing tool. The swing tool is isolated in `tools/`. Sacred files belong
   to the daily SGC cron path.

9. **DO NOT propose features for "next session."** If a feature is worth
   building, propose it now or drop it. Deferring features creates handover
   debt and re-opens decisions later.

10. **DO NOT add yfinance to `requirements.txt`** of the main project.
    yfinance is used by the swing tool only and is imported with try/except
    fallback. Adding to requirements creates daily-cron dependency on a
    brittle scraping library.

11. **DO NOT propose redesigning the threshold-spectrum display** (showing
    multiple thresholds in a table). Audit found this anchors users toward
    the middle and creates choice paralysis. Default 65% only; user can
    override via `--conviction-threshold` flag.

12. **DO NOT re-introduce "ROBUST HOLD" / "POINT HOLD" verbiage.** Was
    cleaned up in §2026-05-17 cleanup to "X_safe pass" / "X_aggressive only"
    / "below threshold" to align with v4 vocabulary.

---

## 9. CURRENT STATE (as of session lock, 2026-05-17)

These values will be STALE in future sessions. Always re-run the tool to get
current state. Listed here for context only:

- **Spot:** $1,407.61 (Friday May 15 close)
- **Sigma blended:** ~97%
- **Drift blended (Bayesian posterior):** ~+22%
- **X_safe:** $1,680
- **X_aggressive:** $1,700 (recommended sell-limit)
- **Verdict:** HOLD
- **Profit if X_aggressive hit:** +$2,100 (10 × $210)
- **P(touch BE):** ~89%
- **P(touch X_aggressive):** ~65.5%
- **P(panic floor $1,100 touched):** ~60%
- **Regime:** POST_PARABOLA, EXTREME vol
- **Next earnings:** Aug 13, 2026 (88 days away, 28d after horizon)

---

## 10. DECISION LINEAGE (brief audit/decision trail)

For context on why the design is what it is:

| Phase | Decision | Reason |
|---|---|---|
| v1 (initial) | Single-target MC with EV-cushion verdict | Pattern matched the existing SGC dip engine math |
| v1 audit | Found 5 structural biases + framework mismatch | Multi-agent audit caught architecture issues |
| v2 | Multi-signal drift blend, bias fixes | Audit findings applied |
| v2 audit | Drift signal blend doing all the work; 7-fix opportunity | Second audit |
| v3 | Tier 1 architectural upgrades (Bayesian, CI, regime, path metrics) | Locked option Y scope |
| v3 audit | Tool optimizing for wrong target (profit vs Jesse's BE objective) | Third audit revealed use-case mismatch |
| v3.1 | Dual-target output + Bayesian-posterior verdict + 6 bias fixes | Audit findings applied |
| **Use-case clarification** | **Jesse's actual rule: multi-target conviction scan at 65% threshold** | **Realized the entire framework needed restructuring** |
| v4 (LOCKED) | Multi-target conviction scan, 9 signals, sigma triangulation, no EV math | The current design |
| v4 audit | Found stale AI position_guidance rules (artifact from v3) | Single audit, fixed in v4.1 |
| v4.1 | AI prompt aligned with v4 framework; math+AI now agree | Locked |
| v4.2 cleanup | Removed ~700 lines of v3 dead code; fixed silently-broken hysteresis | Final cleanup |

**Current commit: `6b5e0df` on branch `claude/analyze-sandisk-trading-6zYxn`.**

The design is now LOCKED. Future sessions should:
- Run the tool, synthesize output for Jesse
- Update this doc if material new context emerges
- NOT redesign the framework

---

## 11. SESSION-START PROTOCOL FOR NEW CLAUDE SESSIONS

If you (new Claude session) are reading this doc, here's what to do:

1. **Acknowledge you've read this doc** in your first response to Jesse.
2. **Do not re-litigate locked decisions.** If Jesse seems unsure, point him
   to the relevant section of this doc rather than re-opening the debate.
3. **Wait for Jesse's first instruction.** If it's "/sndk" + output below,
   synthesize per Section 4. If it's a new question, address it WITHIN the
   locked framework.
4. **Apply the anti-patterns in Section 8.** They were paid for in real
   session time.
5. **Update this doc** if material new context emerges (audit findings,
   design changes, position changes). Note your update date at the bottom
   of the doc.
6. **Commit doc updates separately** from any code changes, so the
   decision history is preserved.

When Jesse runs his daily check, he'll paste output and say `/sndk` (or
similar). Respond with:

```
Verdict: HOLD/TRIM/CUT @ $X
What changed vs yesterday: [brief diff]
Flags: [hysteresis / regime / AI disagreement / dispersion etc., or "none"]
Action: [place sell-limit / adjust to $X / no change]
```

Three to six lines. No padding. Jesse can ask deeper questions if needed.

---

## 12. WHAT TO DO IF JESSE SAYS THE SYSTEM IS WRONG

Honest scenario: Jesse re-runs the tool one day and the verdict has shifted
in a way he disagrees with. Common cases:

| Case | Likely cause | Right response |
|---|---|---|
| "Verdict flipped from HOLD to CUT today" | Single-day blip; hysteresis warning should fire | "Hysteresis flag says wait one more day for confirmation; re-run tomorrow before acting" |
| "AI says TRIM but math says HOLD" | Real disagreement | "AI rationale cites X — that's a real qualitative concern not in the model. Up to you to weight" |
| "The verdict isn't matching my gut" | Possible legitimate user judgment | "What's your gut telling you? Let's stress-test the math against your specific concern" |
| "The recommendation hasn't changed for 5 days" | Stable state | "Yes — your sell-limit is set, tool is monitoring. Most days will be 'no action'" |

**DO NOT** spontaneously recommend that the verdict be discarded because
Jesse is hesitant. The tool was built precisely to give an honest, unbiased
read. Honor it. But if Jesse can articulate a SPECIFIC concern not captured
in the model, address it.

---

## Document maintenance

- **Created:** 2026-05-17
- **Last updated:** 2026-05-17 (initial creation, locking v4 design)
- **Authoritative commit at creation:** `6b5e0df`
- **Future updates:** append change log entries below

### Change log

- 2026-05-17 — Initial creation, locking v4 multi-target conviction scan design.

#End
