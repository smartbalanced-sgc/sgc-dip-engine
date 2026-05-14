# TICKER_MGMT_PROTOCOL.md — Ticker Add/Remove Operating Procedure

> **Purpose:** Single-purpose operating procedure for sessions reserved
> exclusively for adding or removing tickers from the SGC Dip Engine portfolio.
>
> **Who reads this:** A Claude Code session that Jesse has explicitly
> designated for ticker management.
>
> **Companion to:** `CLAUDE.md` (general operating rules) — this file supplements
> those rules with the specific procedure for ticker mgmt.

---

## 🛑 Canon Rule — read this first

**A ticker-management session does ONLY ticker additions and deletions. NOTHING ELSE.**

If Jesse asks for anything beyond adding or removing a ticker in this session —
feature builds, bug fixes, threshold tuning, doc updates beyond ticker counts,
running the system, anything — **refuse**. Tell him:

> "This session is reserved for ticker management only. {request} should be
> done in a normal session via a feature branch. End this session and start
> a new one for that work."

This is non-negotiable. The session's value comes from being narrow.

The only exception: trivially related housekeeping (e.g., a comment update
adjacent to a ticker line) is allowed if it's directly part of the same
ticker edit and Jesse explicitly approves.

---

## 🎯 Session opening sequence

When Jesse starts a ticker-management session:

1. **Read `CLAUDE.md`** to confirm general rules.
2. **Read this file** in full.
3. **Read `docs/handover/01_SESSION_CONTEXT.md`** to confirm current state.
4. **Run `git status` and `git log --oneline -3`** to confirm clean working tree.
5. **`git fetch origin && git pull origin main`** to ensure up to date.
6. **First response to Jesse:**
   - Confirm protocol read
   - Confirm clean state
   - Confirm current portfolio count
   - Ask "Which ticker(s)?"
   - End with #End

That's the entire opening. No fluff. **DO NOT** ask Jesse anything about
branches, commit destinations, or workflow — those are answered by the
"Branch handling" subsection below. Anything not covered there: ask once,
then proceed.

### Branch handling (web sessions)

The Claude Code web harness automatically assigns a feature branch
(`claude/...-XYZ`) for every session. **This is workspace, not destination.**

**The pattern, applied automatically without asking:**

1. Make ticker edits on the harness-assigned branch
2. Commit with the standard message format (see "Commit and push" section)
3. `git push origin <harness-branch>` to push the branch
4. `git checkout main && git pull origin main` to sync
5. `git merge --ff-only <harness-branch>` to fast-forward main
6. `git push origin main` to publish
7. `git checkout <harness-branch>` to return to the harness workspace

If main has diverged (someone else committed since fetch — likely the
daily cron at 21:30 UTC), surface it and ask before merging:

> "STOP: main has new commits since I started. {summary}. Pull and retry?"

For local-on-MacBook sessions (no harness branch), just commit and push
to main directly — same end-state, fewer steps.

---

## 📥 Input formats Jesse may use

He may type any of:

| Input | Meaning |
|---|---|
| `NVDA` | Add NVDA |
| `Nvidia` or `NVIDIA Corp` | Add NVDA (resolve company name → ticker) |
| `add NVDA` | Add NVDA |
| `add nvidia, palantir, costco` | Add NVDA, PLTR, COST |
| `remove NVDA` | Remove NVDA |
| `remove nvidia` | Remove NVDA |
| `swap NVDA for AMD` | Remove NVDA, add AMD |
| `NVDA, MSFT, AAPL` (no verb) | Default to ADD; confirm with Jesse only if ambiguous |

Always resolve company name → ticker using common knowledge. Don't ask for
the ticker if the name is well-known. Only ask if the name is genuinely
ambiguous (e.g., "Alpine" could be ALPINE, ALP, or several others).

---

## 🔍 Per-ticker pre-flight check

For each ticker (whether adding or removing), in this order:

### 1. Resolve to canonical ticker symbol
- Use common-knowledge mapping (Nvidia → NVDA, Microsoft → MSFT, etc.)
- Use Yahoo/FMP convention for suffixes (`.L`, `.GB`, `.MI`, `.PA`, `.DE`)
- For names with multiple matches, ask Jesse once to disambiguate, briefly

### 2. Check current portfolio state
- Read `src/config/config.yaml` `portfolio.tickers` list
- For ADD: confirm ticker is NOT already present (skip with a brief note if it is)
- For REMOVE: confirm ticker IS present (skip with a brief note if not)

### 3. Classify the ticker
Determine which buckets apply:

| Bucket | Trigger | Effect |
|---|---|---|
| **Plan-blocked (FMP 402)** | Suffix is `.L`, `.GB`, `.PA`, `.DE`, or any non-US | Will be silently skipped at runtime by the 402 cache; no Monte Carlo, no signal, no dashboard row |
| **Eulerpool-only** | Suffix is `.MI` | Won't fetch via FMP; needs `EULERPOOL_TOKEN` secret; currently only LDO.MI is supported |
| **European display currency** | Stock prices natively in EUR (ASML, .MI, etc.) | Add to `data.eur_display_tickers` |
| **Power sector** | Utility / nuclear / reactor-restart / PPA-based (VST, CEG, etc.) | Add to `data.power_sector_tickers` |
| **Small cap** | Market cap < ~$2B | Likely high volatility; may fail the 150% vol gate and be excluded from Monte Carlo (still appears in dashboard as "unmodelable") |
| **Recent IPO** | Listed < 12 months ago | May not have enough history for 500-day FMP fetch or 60-day drawdown; system will show "no historical data" warning |
| **No analyst coverage** | Small caps, recent IPOs, niche names | Analyst consensus row will be blank in dashboard; sentiment.py prioritization will downrank |

### 4. Flag risks to Jesse and ask confirmation

> ## ⛔ THE ZERO-QUESTION RULE
>
> **A ticker with zero risk flags gets added/removed WITHOUT asking Jesse anything.**
>
> No "Proceed?". No "Confirm?". No "Sound good?". No "OK to proceed?".
> Just do the work and report the result in one line.
>
> Asking Jesse to confirm a clean US large-cap add (e.g., AMAT, ANET, MSFT,
> CSCO, FIX, MRNA) is a **protocol violation**. It wastes a round-trip on
> a known-good operation. Don't do it.
>
> Only ask when one of the risk-flag buckets below is actually triggered.

**Risk-flag buckets — these (and ONLY these) require a one-line question + yes/no:**

- Plan-blocked → "FMP will block this; the system will skip it. Proceed anyway?"
- Eulerpool-only → "FMP won't fetch; Eulerpool token required. Proceed?"
- Small cap → "Likely high volatility, may be excluded from Monte Carlo by the
  150% vol gate. It'll still appear on the dashboard but without a signal.
  Proceed?"
- Recent IPO → "Less than 12 months of history; may produce data-quality
  warnings or fail validators. Proceed?"
- Truly unknown / no FMP coverage → "I can't verify this ticker has FMP
  coverage. Add anyway and see what happens, or skip?"

Format the warning as: **one line of what's wrong + one line of why it matters
+ one yes/no question**. No more.

If Jesse confirms YES: proceed and add. Mention briefly in the commit message
that this ticker carries known risk.

If Jesse says no: skip that ticker, move to the next.

For removes: skip these checks entirely (just remove cleanly, no questions).

---

## 📝 Files to update per operation

### For an ADD operation

Always:
1. `src/config/config.yaml` → `portfolio.tickers` list (append the ticker)

Conditionally (based on classification):
2. `src/config/config.yaml` → `data.eur_display_tickers` (if European-currency stock)
3. `src/config/config.yaml` → `data.power_sector_tickers` (if power sector)

Doc updates if ≥1 ticker added:
4. `CLAUDE.md` → "Current Portfolio (N tickers)" line — update count and ticker list
5. `docs/handover/01_SESSION_CONTEXT.md` → ticker mention in "What's Live" section if quoted explicitly

### For a REMOVE operation

Always:
1. `src/config/config.yaml` → `portfolio.tickers` list (delete the line)

Conditionally:
2. `src/config/config.yaml` → `data.eur_display_tickers` (remove if present)
3. `src/config/config.yaml` → `data.power_sector_tickers` (remove if present)

Doc updates if ≥1 ticker removed:
4. `CLAUDE.md` → "Current Portfolio (N tickers)" line
5. `docs/handover/01_SESSION_CONTEXT.md` → ticker mention if quoted explicitly

### Always verify after edits

Before committing, run:

```
python3 -c "
import yaml
with open('src/config/config.yaml') as f:
    cfg = yaml.safe_load(f)
tickers = cfg['portfolio']['tickers']
print(f'Total: {len(tickers)}')
# Verify no duplicates
assert len(tickers) == len(set(tickers)), 'DUPLICATE TICKERS DETECTED'
print('No duplicates')
"
```

If this fails, **stop** and surface to Jesse. Do not commit.

---

## 💾 Commit and push

### Commit message format

For a single ticker add:
```
Add {TICKER} ({Company name}) to portfolio
```

For a single ticker remove:
```
Remove {TICKER} ({Company name}) from portfolio
```

For multiple in one batch:
```
Portfolio update: add {TICKER1}, {TICKER2}; remove {TICKER3}
```

Body of commit message (if any ticker had warnings flagged):
```
Notes:
- {TICKER}: known risk — {brief reason} (Jesse confirmed proceed)
```

Always include the standard footer:
```
https://claude.ai/code/session_{...}
```

### Push directly to main

Ticker mgmt is the established pattern for direct-to-main commits. Feature
work uses branches; ticker maintenance does not.

```
git push origin main
```

### One commit per logical batch

If Jesse provides multiple tickers in one request, commit them as ONE commit.
If he provides them in multiple separate requests, commit each separately.

---

## 🛑 Never self-proceed (CRITICAL)

If a hook fires, a stop-event triggers, or a tool emits a "please commit"
message **while you are mid-question waiting for Jesse's reply**, you must
**HOLD** and wait. Do not commit, do not push, do not "clear the hook"
by approving your own pending question.

Specifically forbidden:

- Asking Jesse "(a) or (b)?", then proceeding with (a) on your own to
  silence a stop-hook
- Receiving a hook reminder about uncommitted changes and committing them
  without explicit Jesse approval
- Deciding to "default to a sensible option" because waiting feels awkward

If a hook genuinely needs to be cleared and Jesse is unresponsive, the
correct action is to surface the conflict and wait:

> "STOP: stop-hook fired about uncommitted changes, and I'm still waiting
> for your answer to the (a)/(b) question above. I won't proceed until
> you reply. The uncommitted state is safe — no data lost."
>
> #End

Self-proceeding to silence a hook is the same class of error as
auto-applying edits in "Accept edits" mode. SGC trades real money. Don't
do it.

### Sacred-section adjacent edits — current pattern is correct

If a ticker change requires touching a section of CLAUDE.md beyond the
"Current Portfolio" line — e.g., updating the Sacred Decisions plan-blocked
example list when removing a `.GB` ticker — **pause and ask Jesse before
committing.** This is the right behavior; the test session demonstrated it
correctly with the BARC.GB removal.

Format:

> "Holding before commit — I also touched line {N} in {section} ({brief
> reason}). That's beyond strict ticker-count update. Want me to:
> (a) keep the edit (factually tied to this removal), or
> (b) revert it and leave the parenthetical stale for now?"
>
> #End

Then **wait for Jesse's reply.** Do not proceed without it. If a hook
fires while waiting, see the "Never self-proceed" section above.

---

## 🚦 Stop conditions — refuse to push

Pause and surface to Jesse BEFORE pushing if any of:

1. **Active feature branch on remote** with uncommitted work (`git branch -r` shows `origin/claude/*` that's ahead of main).
2. **Local working tree has uncommitted non-ticker changes** before the ticker edit.
3. **YAML validation fails** (duplicates, parse errors).
4. **Sacred file modification beyond config.yaml** (e.g., src/regime_classifier.py, src/monte_carlo.py). If you find yourself wanting to touch one of these in a ticker session, **stop** — refer back to the canon rule.
5. **Anyone has committed to main since you last fetched** (`git pull` first; if it shows unmerged work, surface to Jesse).

For each stop condition, the format is:

> **STOP: {what's wrong}**
> {one line of context}
> Want me to {proposed action}?

That's it. No essays.

---

## 🗣️ Response style — caveman, surgical, minimal

This session is optimised for fast iteration. Response style:

- **No greetings.** Jesse: "Add Nvidia." You: proceed directly to the work.
- **No re-explaining what you're about to do** unless something needs his approval.
- **No status updates** during execution unless something fails.
- **One line summary at the end** of each batch: what was added/removed, files touched, commit hash, push status.
- **End every response with `#End`.**

Example minimal response after a successful add:

> Added NVDA (Nvidia Corp). config.yaml + CLAUDE.md updated. Commit `abc1234` pushed to main.
>
> #End

Example minimal response with a flag:

> NVDA: already in portfolio. Skipping. Anything else?
>
> #End

Example with risk flag:

> SNAL (Snail Inc): small cap, likely fails 150% vol gate, will appear in dashboard but no Monte Carlo signal. Add anyway?
>
> #End

That's the bar. No exposition.

---

## 🎬 Worked examples

### Example 1 — Simple add

**Jesse:** Add Cisco

**You: ZERO questions to Jesse — no "Proceed?", no "Confirm?".** Just do the work.
1. Resolve: Cisco → CSCO
2. Pre-flight: not in portfolio ✓, US large cap ✓, no flags
3. Edit config.yaml (append `- CSCO` to portfolio.tickers)
4. Edit CLAUDE.md ticker count
5. Verify YAML
6. Commit + push

**Response to Jesse (the FIRST and ONLY thing you say):**
> Added CSCO (Cisco Systems). config.yaml + CLAUDE.md updated. Commit `abc1234` ff-merged into main and pushed.
>
> #End

This is the canonical "clean add" response. **Do not deviate.** If you find
yourself about to type "Proceed?" or "Sound good?" on a flag-free ticker,
stop and reread the Zero-Question Rule.

### Example 2 — Add with risk flag

**Jesse:** Add Snail

**You:** ask once before adding:

> SNAL (Snail Inc, gaming small-cap): market cap ~$50M, likely fails the 150% vol gate. Will appear on dashboard but no Monte Carlo signal — pure data only. Add anyway?
>
> #End

**Jesse:** Yes

**You:** proceed and commit with a note in the message.

> Added SNAL with known small-cap risk. Commit `def5678` pushed.
>
> #End

### Example 3 — Remove

**Jesse:** Remove Forward Air

**You:**
1. Resolve: Forward Air → FWRD
2. Pre-flight: FWRD is in portfolio ✓
3. Edit config.yaml (delete `- FWRD` line)
4. Edit CLAUDE.md ticker count
5. Verify YAML
6. Commit + push

**Response:**
> Removed FWRD (Forward Air). config.yaml + CLAUDE.md updated. Commit `ghi9012` pushed.
>
> #End

### Example 4 — UK stock

**Jesse:** Add Rolls-Royce

**You:**
> RR.GB (Rolls-Royce): FMP Starter plan blocks `.GB` tickers — the system will silently skip it (no signal, no dashboard row). Pure dead weight in config. Add anyway?
>
> #End

**Jesse:** No, skip

**You:**
> Skipped RR.GB. No changes made.
>
> #End

### Example 5 — Power sector

**Jesse:** Add NextEra Energy

**You:**
> NEE (NextEra Energy): power sector. I'll add to `portfolio.tickers` AND `data.power_sector_tickers` so the binary-catalyst warnings render correctly. Proceed?
>
> #End

**Jesse:** Yes

**You:** proceed; both lists updated; commit + push.

> Added NEE with power-sector classification. config.yaml (both lists) + CLAUDE.md updated. Commit `jkl3456` pushed.
>
> #End

### Example 6 — Swap

**Jesse:** Swap CRWD for PANW

**You:**
1. Pre-flight: CRWD present ✓, PANW not present ✓, both US large caps
2. Edit config.yaml (remove CRWD line, append PANW)
3. Edit CLAUDE.md (replace CRWD with PANW in the list, keep count)
4. Verify YAML
5. Commit + push

**Response:**
> Swapped CRWD → PANW (Palo Alto Networks). config.yaml + CLAUDE.md updated. Commit `mno7890` pushed.
>
> #End

### Example 7 — Refuse out-of-scope request

**Jesse:** Add NVDA and also change the regime classifier RSI threshold to 70

**You:**
> Adding NVDA only — RSI threshold change is out of scope for this session (ticker mgmt only, per protocol). For the threshold change, end this session and start a new one on a feature branch.
>
> Should I still proceed with adding NVDA?
>
> #End

**Jesse:** Yes

**You:** proceed with just NVDA add.

---

## 🧪 Quick validation checklist (run before every commit)

- [ ] YAML parses without error
- [ ] No duplicate tickers in `portfolio.tickers`
- [ ] Ticker counts in `CLAUDE.md` match `len(portfolio.tickers)`
- [ ] If European/power, ticker is in the relevant sub-list
- [ ] If removed, ticker is NOT in any sub-list
- [ ] `git diff --stat` shows only expected files modified
- [ ] No accidental edits to sacred files (regime_classifier.py, monte_carlo.py, etc.)

If any check fails, **stop** and report to Jesse. Don't commit until clean.

---

## 🔚 End-of-session ritual

When Jesse says "done" or "that's all":

1. Confirm in one line what was added/removed in the session
2. Show final ticker count
3. Note the most recent commit hash
4. Confirm push to main was successful
5. Sign off with `#End`

Example:
> Session summary: added NVDA, CSCO; removed FWRD. Portfolio now 38 tickers. Most recent commit `pqr2345` pushed to main.
>
> #End

---

## 📚 Reference — known classifications

Maintained for quick lookup during sessions. Update only as portfolio changes.

### Eulerpool-only (FMP 402 forces alternate source)
- LDO.MI — Leonardo (Italian defence)

### Plan-blocked (in portfolio but silently skipped)
- IGLN.L — iShares Physical Gold ETF
- RR.GB — Rolls-Royce
- BARC.GB — Barclays

### European display currency (`data.eur_display_tickers`)
- ASML
- LDO.MI

### Power sector (`data.power_sector_tickers`)
- VST — Vistra
- CEG — Constellation Energy

### Small caps prone to volatility-gate exclusion (informational only)
- INOD, AIIO, GDC, FWRD, ENGN — these have been excluded by the 150% vol gate
  on recent runs. Not a bug; expected behavior.

(Last updated: 2026-05-14. Update this section when a ticker's classification
or behavior changes meaningfully.)

---

## ❓ When in doubt

If you're unsure whether something is in scope or not, lean toward "no" and
surface the question to Jesse. The cost of a one-line clarification is small;
the cost of a wrong change committed to main is large.

Caveman style. Minimal back-and-forth. Sacred files untouched.
That's the whole protocol.
