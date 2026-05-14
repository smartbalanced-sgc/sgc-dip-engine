# Research Evidence Archive

> **Purpose:** Permanent record of empirical analyses run on the SGC Dip Engine.
> Each report here is a timestamped snapshot of a research question
> investigated against historical data.
>
> **NOT executable.** See `research/` (top-level) for the scripts that
> generate these. This folder holds the dated verdicts.

---

## Why this folder exists

`docs/handover/` describes the system **as designed.**
This folder captures the system **as validated.**

Each entry here answers a specific question with data — "does X actually work?",
"should we change Y?", "did the prediction we logged come true?" — and serves
as the durable evidence trail for rule changes, threshold tuning, and
architectural decisions.

When a future Claude session is asked to revise a sacred file or tune a
threshold, the first question should be: **"is there a prior research report
about this?"** If yes, cite it. If the proposed change contradicts prior
evidence, that needs a new research report, not an opinion.

---

## File naming

`YYYY-MM-DD_short-description.md`

- **Date** = the run date that produced the evidence
- **Description** = the question or feature being evaluated
- One report per investigation. Don't append to existing reports — create
  a new dated file. Old reports stay as historical record.

---

## Index of reports

| Date | Topic | Verdict | Forward eval / re-run |
|---|---|---|---|
| 2026-05-14 | [Regime classifier rule validation (R0 vs alternatives)](2026-05-14_regime_classifier_backtest.md) | Status quo wins — R0 supported by evidence | Re-run 2026-06-13 with MU forward outcome |

---

## When to add an entry

Always add when:

- A research script in `research/` has produced a verdict on a specific question
- A backtest motivates (or rejects) a production change
- A live prediction logged in a prior report has reached its evaluation date
  (append a "forward verification" report referencing the original)
- A future Claude session would benefit from knowing this empirical finding

Do **not** add for:

- Speculative analyses that didn't produce a clear verdict (those are scratch work)
- Re-runs that confirm an existing report (note in the original instead)
- One-off curiosity questions unrelated to system design decisions

---

## Required structure for each report

Every report file must include:

1. **Header block** (frontmatter):
   - Date generated
   - Script that generated it (relative path to `research/`)
   - Commit hash at time of generation (so we know what the production code was)
   - Status: Complete / In-progress / Superseded
   - Forward evaluation date (if applicable)

2. **Question** — one paragraph stating what was investigated and why

3. **TL;DR Verdict** — one paragraph stating the conclusion plainly

4. **Methodology** — universes, lookback, rules tested, statistical tests applied

5. **Key findings** — tables or quoted output from the research script

6. **Synthesis** — interpretation in plain English, distinguishing what the
   data showed from what the analyst inferred

7. **Forward action** — what (if anything) to do based on this evidence

8. **Limitations** — honest enumeration of biases and caveats. Future sessions
   will weigh this evidence; they need to know the constraints.

9. **How to reproduce** — exact command to re-run the analysis

---

## How to use these reports in a new session

When making rule-change or threshold-tuning recommendations:

1. **First search this folder** for prior reports on the relevant rule or
   threshold.
2. **Cite specific reports** when proposing or rejecting a change.
3. **Don't propose changes that prior reports have evaluated and rejected**
   without new empirical evidence justifying re-examination.
4. **If a prior report has a forward-eval date that has passed**, re-run the
   relevant script and append a follow-up report before any new decision.
5. **Stale reports (>12 months without re-run) are advisory only** — the
   market environment may have changed enough that conclusions need refresh.

---

## Relationship to other docs

| Folder | Contains | When to update |
|---|---|---|
| `docs/handover/` | System as designed (intent, rationale, sacred decisions) | When the design changes |
| `docs/research/` | System as validated (empirical evidence) | When new data arrives |
| `research/` (top-level) | Executable scripts that generate evidence | When new tests are needed |

Together these form the system's institutional memory. The handover docs say
"this is what we built and why"; the research docs say "this is whether it
works"; the scripts say "this is how we verified it."
