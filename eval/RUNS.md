# Evaluation runs

Every run, in order. Development runs are for building; the held-out set is run once, with the configuration
frozen in git beforehand. Model answers are cached in `eval/answers/` and committed, so every model score can be
re-scored without a request (`--replay`).

The benchmark (`data/benchmark.json`, rules in `data/BENCHMARK_GUIDE.md`) was frozen before any of this.

## Development (30 cases: 20 planted, 1 natural tracking failure, 1 broad week, 8 quiet weeks)

### The engine, scored with the deterministic baseline (no model)

The baseline reads the evidence pack with fixed rules: tracking findings first, then the top candidate cause.
Each engine change below was made after reading the development failures; all are general rules, not
per-case fixes.

| Step | Change | Right | Wrong | False alarms |
| --- | --- | --- | --- | --- |
| 1 | First engine: segments ranked on the metric that moved most | 16/30 | 8 | 2/9 |
| 2 | Search each segment's whole profile (sessions, every funnel step, order value); keep only movements that fit a failure type; a segment must diverge at least twice as much as it usually does | 12/30 | 12 | 6/9 |
| 3 | A failure must be worse in absolute terms, not just grow less than the rest (mirror effects); start dates searched in the question week only | 14/30 | 10 | 6/9 |
| 4 | A cause must have made the question's metric worse; rank by impact x severity; a segment that lost most of its traffic stays eligible | 24/30 | 2 | 4/9 |
| 5 | A cause must be on the question metric's path (a product-view drop can't explain the checkout-start rate; traffic loss can't explain a rate) | **26/30** | **2** | **2/9** |

What the steps found:
- **Steps 1-3: natural campaign traffic was the problem, not the planted incidents.** The real data shifts
  between landing pages and between new and returning visitors most weeks, and those segments are genuinely
  distinct from the rest. Ranking by strength (z) or by impact alone kept picking them.
- **Step 4 fixed most of it:** every planted failure made the asked metric worse and was a collapse (59-96%),
  while the natural shifts made it better or drifted by 25-45%.

The four cases still wrong after step 5:

| Case | What happened |
| --- | --- |
| D07 | the planted basket drop on "os = Web" was named as "device = desktop"; the two overlap only 46% in Google's obfuscated data |
| D23 | a planted bot burst from Canada lost to a real drop in product views on Home-page landings that week |
| D09 (quiet) | a real 20% cart drop in Google-search traffic was raised as an incident |
| D21 (quiet) | a real surge of new visitors on 7 Dec, who convert less, was raised as a mix shift |

### The model (gemini-3.5-flash-lite), on the same evidence packs

| Run | Prompt | Model alone | Model + checks | Baseline |
| --- | --- | --- | --- | --- |
| 1 ([log](results/dev_run1.log)) | first version | 18/30, 10 missed | 19/30 | 26/30 |
| 2 ([log](results/dev_run2.log)) | explicit decision order and a summary line, prompt `bfb1e472c9` | **26/30** | **26/30** | 26/30 |

- **Run 1.** The model said "no real change" whenever the headline was within normal variation, even when the
  pack listed a candidate cause or a tracking finding: 10 misses.
- **Run 2.** The prompt now gives the decision order (tracking, then candidates, then the headline), says
  plainly that a segment can break inside normal overall swings, and starts the pack with a code-computed count
  of relevant findings and candidates. The model then matched the baseline exactly, error for error. It never
  used its room for judgement (to set aside ordinary campaign traffic, or to say "can't tell") on the four hard
  cases.
- **Every number traced.** In 60 answers it never typed a number of its own; every figure is a cited fact.

**What this means:** once the evidence is good, the language model adds no decision value on this benchmark.
Its value is the explanation: a plain-English diagnosis and next step built from traced facts.

Development fails the gate:
- wrong 2/30 = 6.7% (needed ≤ 5%);
- false alarms 2/9 = 22% (needed ≤ 10%);
- right on incidents 90% (needed ≥ 75%).

Tuning stopped here: the remaining misses are real natural shifts, and obfuscation, that no principled rule I
found separates.

**Frozen for the release run:** the engine and checks at this commit, gemini-3.5-flash-lite, prompt `bfb1e472c9`,
temperature 0. What ships is the model with the checks.

## Held-out (40 cases)

Not yet run.
