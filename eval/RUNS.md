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

## Held-out (40 cases: 26 planted, 4 of them of two types never seen in development; 2 natural tracking failures; 2 broad weeks; 10 quiet weeks)

**Release run 1**, frozen configuration from commit 7180897, run once ([log](results/holdout_run1.log)).
**Failed the gate.**

| Version | Right | Wrong | Missed | Can't tell | False alarms | Right on incidents |
| --- | --- | --- | --- | --- | --- | --- |
| Deterministic baseline | 30/40 | 7 | 1 | 0 | 2/12 | 71% |
| Model alone | 27/40 | 9 | 1 | 1 | 2/12 | 61% |
| **Model + checks (ships)** | **28/40** | **8** | **1** | **1** | **2/12** | **64%** |

Gate:
- wrong 8/40 = 20% (needed ≤ 5%) ✗
- right on incidents 64% (needed ≥ 75%) ✗
- false alarms 2/12 = 17% (needed ≤ 10%) ✗
- numbers traced 100%: none of the 40 answers typed a number ✓

By case type (model + checks):

| Case type | Right | Wrong | Missed / can't tell | False alarm |
| --- | --- | --- | --- | --- |
| Planted, clear (20) | 14 | 4 | 2 | - |
| Planted, unseen types (4) | 2 | 2 | 0 | - |
| Planted, too small (2) | 0 | 2 | 0 | - |
| Natural tracking failures (2) | 2 | 0 | 0 | - |
| Quiet weeks (10) | 9 | - | - | 1 |
| Broad weeks (2) | 1 | - | - | 1 |

What went wrong:
- **Real campaign weeks, 7 of the 12 misses.** The real drop in product views on Home-page landings in the week of
  28 Dec, and the real surge of new visitors in the week of 7 Dec, were named as the cause instead of the planted
  incident, or raised on quiet and broad weeks. These are true changes in the data, and the same two caused the
  development failures.
- **The unseen "units bug", 2 wrong.** A country's revenue recorded x100 was found by the health check. But my rule
  "when a finding appears in overlapping segments, name the broadest" picked the operating system or browser
  carrying those orders, not the country. For a value jump, the right segment is where the signature is most
  concentrated. The rule was written for duplicate events, where the broadest segment is right.
- **One source inside "referral" traffic was named as "referral", 2 wrong.** The rule that prefers the narrower
  segment didn't fire, because the broader one's impact was more than the narrower one's by more than the rule
  allows.
- **One planted traffic loss got "can't tell", and one planted bot burst was missed.**

The deterministic baseline beat the model with checks (30 vs 28 right). As in development, the model added no
decision value.

The held-out cases are unchanged. A release claim would need the fixes above tested on development and a fresh
held-out set.

## Corrections after the release run

- **"Numbers typed by the model: 0" was wrong.** The run logs' line read the model's raw answer, where the count
  is never computed, so it always printed zero. Recounted from the checked answers by replaying the cached answers
  (no requests): the model typed numbers itself in 9 development answers (21 numbers) and 15 of 40 held-out answers
  (35 numbers). Almost all were dates written out ("Fri 18 Dec"). The checks removed every one, so no untraced
  number reached a reader, but the claim "the model never typed a number" was false. The model also sometimes cited
  facts as [F12] instead of {F12}; those now render as the facts they point to. Neither change touches a verdict, and
  both replays reproduce the scores above exactly.
- **Replay is not fully deterministic.** When two candidates tie, DuckDB doesn't guarantee their order, so one
  development case (D01) rebuilt slightly different evidence text, and its cached answer no longer matched. Fixing
  the ordering would invalidate the cached held-out answers, so the logged runs stay the record. Next: break ties by
  name before the next run.
