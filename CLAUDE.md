# CLAUDE.md — Working Instructions for Claude Code

> **READ THIS FIRST. Every session. No exceptions.**
> 
> If you are an AI assistant working on this repository, this file contains your operating rules. Sacred decisions are listed here. Approval gates are mandatory. Read the full handover documentation at `docs/handover/*.md` before making any non-trivial change.

---

## 🚨 CRITICAL — APPROVAL GATES (NEVER VIOLATE)

### Before ANY of the following, you MUST get explicit approval from Jesse:

1. **Any `git commit`** — must show diff first, get "go" from Jesse, then commit
2. **Any `git push`** — must run local tests first, get "push it" from Jesse, then push
3. **Any change to files in this list** (sacred — see "Sacred Decisions" section):
   - `src/config/config.yaml` 
   - `src/regime_classifier.py` (only with deep understanding)
   - `src/monte_carlo.py`
   - `src/hmm_regime.py`
   - `src/macro_regime.py`
   - `src/validators.py`
4. **Any change that touches more than 3 files** — stop and ask first
5. **Any new dependency in `requirements.txt`**
6. **Any change to `.github/workflows/`** — could break GitHub Actions

### "Accept edits" mode warning

If Claude Code is in "Accept edits" mode (auto-apply), **explicitly turn it off** for this repo or always ask before applying. SGC trades real money. A wrong edit pushed silently could cost money.

### Phrase patterns to honor

When Jesse says:
- **"Stop"** or **"Wait"** — halt immediately, await further instruction
- **"Restate"** — repeat back the task in 1-2 sentences before acting
- **"Caveman"** — keep responses terse and surgical
- **"Don't be a yes-man"** — pushback expected, especially when challenged

---

## 👤 WHO IS JESSE

- **Address him as Jesse** (his preferred name)
- He's a UK retail investor running the **Smart Growth Compounder (SGC)** system
- Primary use case: **22-year compounding strategy** via monthly DCA into a Trading 212 Stocks & Shares ISA
- Secondary use case: **occasional swing trades** when the system shows high-conviction setups (recent example: MU $750 → $812 in 4 days)
- He's technically capable but **runs all code locally on his MacBook** — you don't have direct access to his machine
- He prefers **caveman-style responses**: clear, concise, surgical. No fluff.

### Working rules he expects you to follow

- End every complete response with `#End` (so he knows you didn't get truncated)
- Restate his task before acting on it
- Wait for explicit approval before writing code
- One change at a time
- Real understanding > pretending to understand

### CRITICAL — Safe code block delivery (avoid paste corruption)

When delivering bash/python heredocs that Jesse will paste into his terminal, **NEVER** nest triple-quoted strings inside other triple-quoted blocks. The markdown renderer in claude.ai will split your code into two visible blocks at the inner triple-quote, and Jesse will only copy the first half. Result: a corrupted paste that breaks his file.

**Rules:**

1. **Never put triple-backtick (markdown code fence) inside a triple-quoted Python string** that is being delivered inside a markdown code block to Jesse
2. **Never put triple-quoted Python strings inside other triple-quoted Python strings** even with different quote types
3. **Prefer list-of-strings joined with newlines** when building multi-line text to write to a file:
   - INSTEAD OF: `content = """line1\nline2\nline3"""`
   - USE: `content = "\n".join(["line1", "line2", "line3"])`
4. **Use unique heredoc delimiters** (e.g., `EOF_FEATURE_X_PATCH`) instead of generic ones like `PYEOF` or `EOF` to avoid accidental termination
5. **When in doubt, deliver code via `create_file` or as a file attachment** rather than as a heredoc Jesse must paste

**The bug pattern to watch for:**

Writing `content = '''...some markdown with backtick-backtick-backtick code blocks inside...'''` inside a bash heredoc that Jesse copies. The outer code fence (in your response) closes prematurely at the inner backticks. Half your code is invisible to Jesse.

**Validation:** Before delivering any heredoc to Jesse, mentally scan the output for ANY ` ``` ` (triple-backtick) characters inside the heredoc body. If present, refactor to use the list-of-strings pattern above.
- Honest pushback > sycophancy
- Annotation Mandate: every logical check cites the Constitution section
- Flag `[Haiku]` or `[Sonnet]` model complexity at task start
- Surgical diffs preferred over full-file rewrites for small changes
- Never edit files Jesse owns without asking
- Never make assumptions silently — state them explicitly

---

## 🏛️ SACRED DECISIONS (NEVER CHANGE WITHOUT EXPLICIT APPROVAL)

The following are locked decisions. Do not "improve" them. Do not "modernize" them. Do not "refactor" them. They exist for specific reasons documented in `docs/handover/RATIONALE_AND_NUANCES.md`.

### Constitution & Strategy
- **Portfolio Constitution v7.1** governs stock selection, weights, and CAGR targets
- **Grinold-Kroner correction:** Set `Buyback = 0%` in Step 3 (EPS growth already embeds buyback — adding again double-counts)
- **Non-GAAP EPS required** for AVGO, CEG, VST
- **MU uses 30/50/20 cyclical probability weighting** (not standard 25/50/25)

### System Architecture
- **Two distinct regime concepts must NOT be confused:**
  - `hmm_regime.py` returns `bull/sideways/drawdown` — used by Monte Carlo for drift/vol multipliers
  - `regime_classifier.py` returns `NORMAL/MOMENTUM/SQUEEZE_RISK/OVERSOLD_REVERSAL/BREAKDOWN` — used by execution_logic to modulate BUY/WAIT signals
- **Monte Carlo uses 500 days of close-to-close history.** Do not add intraday/premarket prices to MC inputs. This was explicitly debated and rejected by Jesse on May 13, 2026.
- **Backtest uses close prices.** Do not change this — it would invalidate all historical comparisons.
- **Regime classifier thresholds** are tuned to current market behavior. Do not loosen them without backtest evidence.

### Data Sources
- **FMP Starter plan only** — `.L` and `.GB` tickers (IGLN.L, RR.GB, BARC.GB) return 402 and are correctly skipped via early-return caching. Do not try to "fix" this — it requires plan upgrade.
- **LDO.MI uses Eulerpool only** (FMP returns 402 on .MI)
- **Anthropic SDK must use lazy initialization** inside `get_client()` — module-level init causes import-time crashes
- **yfinance dividend yield is in percent, not decimal** — there is a known correction in the code; do not remove it
- **FMP requires `sector` param + `from`/`to` dates** for `historical-sector-performance` — without date params it returns stale 2024 data

### Endpoint Names (Already Corrected — Do Not Revert)
- ✅ Use `grades-consensus` (NOT `upgrades-downgrades-consensus`)
- ✅ Use `insider-trading/search` with local P+S filtering (NOT `insider-trading-statistics` which returns empty)
- ✅ Use `historical-price-eod/full` (NOT `historical-price-full`)

### Process Discipline
- **No backwards-compatibility hacks** — if you must change a contract, change it cleanly
- **No over-engineering** — minimum complexity for the current task only
- **Wave-by-wave approval** — Jesse approves each wave before the next begins
- **Complete files over surgical patches** when delivering bigger changes; surgical diffs for tiny ones
- **File delivery:** Jesse runs code on his MacBook; you provide patches he applies

---

## 📁 PRE-EVERY-CHANGE CHECKLIST

Before writing ANY code, you must:

1. **Restate Jesse's request as a precise task in 1-2 sentences**
2. **List specific files or components to change**
3. **List exact changes to make**
4. **Flag complexity** (`[Haiku]` for boilerplate, `[Sonnet]` for new features/architecture)
5. **Wait for Jesse's explicit confirmation before writing code**
6. **For changes affecting >3 files: stop and get confirmation BEFORE starting**

---

## 🧪 PRE-EVERY-COMMIT CHECKLIST

Before EVERY commit:

1. **Tests passed locally** — Jesse confirms `python3 main.py` runs cleanly
2. **Diff reviewed** — Jesse has seen `git diff --stat` and the actual changes
3. **Dashboard verified** — for any change touching `dashboard_generator.py`
4. **GitHub Actions risk assessed** — could this break tonight's cron run?
5. **Explicit "push it" from Jesse** — never push without this exact authorization

---

## 🛠️ TECHNICAL CONTEXT (BRIEF)

### Repository
- **Repo:** `smartbalanced-sgc/sgc-dip-engine`
- **Branch:** `main`
- **Daily run:** GitHub Actions cron at 9:30 PM UTC (= 10:30 PM BST / 9:30 PM BST during DST)
- **Dashboard:** Published to GitHub Pages at `https://smartbalanced-sgc.github.io/sgc-dip-engine/`
- **Local repo path on Jesse's machine:** `/Users/jesse/sgc/sgc-dip-engine`
- **Python version:** 3.10 (matches GitHub Actions setup)
- **Venv path:** `~/sgc/sgc-dip-engine/venv`

### Required Environment Variables (in Jesse's `~/.zshrc`)
- `FMP_API_KEY` — Financial Modeling Prep API
- `ANTHROPIC_API_KEY` — for AI research integration
- `EULERPOOL_TOKEN` — for LDO.MI data

### Required GitHub Secrets (for Actions)
- `FMP_API_KEY`
- `ANTHROPIC_API_KEY`
- `EULERPOOL_TOKEN`

### Current Portfolio (39 tickers as of 2026-05-14)
**Modeled (~36):** NVDA, MSFT, GOOGL, META, AMZN, MA, WM, MU, ASML, AVGO, CTAS, VST, CEG, LDO.MI, TSLA, INOD, ADP, V, LLY, LIN, WMT, PLTR, AMD, INTC, SNDK, ENGN, AIIO, GDC, FWRD, HUBS, CRWD, CSCO, SNAL, ALP, QUCY, IONQ

**Skipped (3 — FMP plan limitation):** IGLN.L, RR.GB, BARC.GB

> Note on "Modeled (~36)": exact runtime count varies. Volatility-gate exclusions
> typically remove 4-6 small caps each run (recent excludes: ENGN, INOD, GDC,
> AIIO, FWRD). The 5 most recent additions (CSCO, SNAL, ALP, QUCY, IONQ) were
> manually added on 2026-05-14; some may produce data-quality warnings or be
> excluded by volatility gates on first runs. Verify on next dashboard.

---

## 🔄 MAINTENANCE PROTOCOL — KEEP HANDOVER DOCS FRESH

> **Handover documents go stale.** Future sessions will read them as authoritative truth. If they do not reflect the current state of the system, they will mislead future Claude sessions and create real bugs.

### Mandatory update triggers

Update handover docs BEFORE pushing in these cases:

| If you... | You MUST update... |
|---|---|
| Ship a new feature | `01_SESSION_CONTEXT.md` (state + what is next), `02_BUILD_HISTORY.md` (append section) |
| Make an architectural change | `06_SYSTEM_ARCHITECTURE.md` (file map, data flow, endpoints) |
| Add a new sacred decision | `CLAUDE.md` (this file Sacred Decisions section) + `03_RATIONALE_AND_NUANCES.md` (the why) |
| Add a new config section | `06_SYSTEM_ARCHITECTURE.md` (YAML structure section) |
| Ship the feature in `04_NEXT_BUILD_SPEC.md` | **REPLACE** `04_NEXT_BUILD_SPEC.md` with the next feature spec, OR rename to `04_NEXT_BUILD_SPEC_DEPLOYED.md` and create a new spec for the next thing |
| Discover a new bug pattern | `03_RATIONALE_AND_NUANCES.md` "Lessons Learned" section |
| Reject an idea (like premarket was rejected) | `03_RATIONALE_AND_NUANCES.md` "Decisions Explicitly Rejected" section |

### The session-close ritual

At the end of any session where you shipped code:

1. **Ask Jesse explicitly:** "Should I update the handover docs before we close this session?"
2. **If yes, list which docs need updating** and propose the changes
3. **Wait for approval, then apply updates**
4. **Commit handover updates separately** from the feature commit OR as part of the same commit (Jesse decides)

### Staleness detection on session start

When a new session loads these docs:

1. Check the date of the last entry in `02_BUILD_HISTORY.md`
2. Check `git log --oneline -5` for recent commits
3. **If recent commits are NOT reflected in handover docs → stale**
4. **Flag it to Jesse immediately:** "These handover docs appear stale relative to recent commits. Should I update them before proceeding?"

### Version tracking convention

Each handover doc should have this header (add if missing):

- **Last updated:** May 13, 2026
- **Last reflected commit:** 0a5b504

When updating a doc, update both fields. Mismatch = stale.

### The honest truth

These rules will only work if **you (the AI) actively enforce them**. Jesse will not always remember to ask. **Make handover-doc maintenance part of your commit checklist.** Treat it as non-negotiable as testing.

---

## 📚 DEEP CONTEXT — READ THESE NEXT

After reading this file, read the handover documentation in order:

1. **`docs/handover/01_SESSION_CONTEXT.md`** — Where we are, what just shipped, what's next
2. **`docs/handover/02_BUILD_HISTORY.md`** — What was built in the last session and why
3. **`docs/handover/03_RATIONALE_AND_NUANCES.md`** — The "why" behind decisions, lessons learned
4. **`docs/handover/04_NEXT_BUILD_SPEC_DEPLOYED.md`** — Daily probability bands feature spec (SHIPPED 2026-05-14)
5. **`docs/handover/05_USER_PROFILE.md`** — Jesse's working style in depth
6. **`docs/handover/06_SYSTEM_ARCHITECTURE.md`** — Technical map of files, data flow, dependencies

---

## 🎯 YOUR FIRST RESPONSE IN A NEW SESSION

When you start a new session on this repo, your first response to Jesse should:

1. Confirm you've read this `CLAUDE.md` file
2. Confirm you've read the handover docs in `docs/handover/`
3. State the **current build state** (regime classifier shipped, daily probability bands is next)
4. Acknowledge the **approval gates** explicitly
5. Ask what Jesse wants to work on first

**Do not skip this confirmation step.** Jesse uses it to verify you're aligned before any work begins.

#End
