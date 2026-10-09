# Why Did It Drop?

An analytics agent that explains why a product metric moved, and says when it can't. Ask "what changed in
conversion this week, and why?". It checks whether the change is real and whether tracking broke, finds the
segment that behaved differently from the rest of the site, dates it, and writes the diagnosis. Every number
comes from a query, never from the model.

**Benchmark explorer: https://akshaykumardhar.github.io/Why-Did-It-Drop/**. All 70 test cases, each with the
agent's diagnosis, what was really there, and the evidence it saw.

- Product requirements: [docs/PRD.md](docs/PRD.md)
- How the benchmark was built: [data/BENCHMARK_GUIDE.md](data/BENCHMARK_GUIDE.md)
- Every run, with its errors: [eval/RUNS.md](eval/RUNS.md)

## Why

In 2026 Amplitude and Mixpanel shipped AI agents that explain metric changes. Neither publishes how often
they're right. Mixpanel's own guide warns that its agent "will confidently explain the wrong thing" when tracking
is broken. This project is the opposite approach: tested, not trusted, with the results published, failures
included.

## The test

- **Real data.** Google's GA4 demo-store export: 4,295,584 events, checked against the published count, built
  into 360,129 sessions. The data has three tracking failures of its own, which became test cases:
  - `add_to_cart` missing until 25 November;
  - purchase events double-firing from 19 to 24 November;
  - zero-revenue orders from 26 January.
- **Planted incidents with known answers.** A seeded generator plants checkout breaks, lost traffic, bot bursts,
  duplicate or missing tracking events and basket drops into copies of the real sessions. It places them only
  where no natural effect touches the metric. Every case is labelled clear or too small by a statistical test
  before any agent runs. There are also real holiday weeks and quiet weeks.
- **70 cases, frozen before any prompt existed.** 30 for development. 40 held out, including two incident types
  development never saw.

## Results

The gate: at most 5% wrong causes, at least 75% of incidents right, at most 10% false alarms on quiet weeks.

| Held-out release run (40 cases) | Right | Wrong cause | False alarms | Incidents right |
| --- | --- | --- | --- | --- |
| Rules only (no model) | 30 | 7 (18%) | 2/12 | 71% |
| Model only | 27 | 9 (22%) | 2/12 | 61% |
| **Model + code checks (ships)** | **28** | **8 (20%)** | **2/12 (17%)** | **64%** |

**Failed the gate.** On development the same system got 26/30 right, with wrong causes 6.7% and false alarms
2/9, so it failed there too.

What the failures show:
- **Real campaign shifts compete with the planted incidents.** The data's own surge of new visitors in early
  December, and its drop in Home-page product views over New Year, are genuine changes. The agent named them
  instead of the planted incident in 7 of the 12 held-out misses.
- **An unseen incident type broke one of my rules.** For a currency-units bug, "name the broadest segment the
  finding appears in" picked the browser carrying the orders, not the country.
- **The model added no decision value.** The first prompt was worse than fixed rules (18 vs 26 of 30), because
  it trusted "within normal variation" over the listed causes. With an explicit decision order it matched the
  rules exactly. Its value is the explanation.
- **The checks did their job on numbers.** The model typed dates itself in 24 of 69 answers; code removed every
  one, so every figure a reader sees comes from a query.

## How it works

1. **Is the change real?** This week against the median weekly change in the weeks before.
2. **Can the data be trusted?** Tracking checks for signatures no shopper produces:
   - purchase events per order above 1;
   - purchases with no checkout start;
   - an event appearing or vanishing;
   - zero-revenue orders;
   - revenue per order jumping tenfold.
3. **Where is it?** Each segment against the rest of the site, with a difference-in-differences test. A candidate
   cause must pass all of these:
   - it diverged at least twice as much as it usually does;
   - it worsened in absolute terms;
   - it made the asked metric worse;
   - its failing metric is on that metric's path.

   Candidates are ranked by impact times severity, and each is dated by when it split from the rest.
4. **What does it mean?** gemini-3.5-flash-lite reads the evidence pack. Every number in the pack is a numbered
   fact, and the model cites facts by id while code prints the values. Checks turn unsupported claims into
   "can't tell".

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
python -m scripts.fetch_ga4 && python -m scripts.build_tables      # download and verify the data (1.8 GB)
python -m eval.run_eval --split dev --no-model                      # the rules alone, no API key needed
python -m eval.run_eval --split holdout --replay                    # re-score the cached model answers
python -m pytest -q tests
```

Data: Google's public GA4 demo-store export (`bigquery-public-data.ga4_obfuscated_sample_ecommerce`), obfuscated
by Google, via a flattened copy published on Hugging Face (see [data/README.md](data/README.md)).

Built with Claude Code, after [RBI Circular Radar](https://github.com/AKSHAYKUMARDHAR/RBI-Circular-Radar),
[Will My Policy Pay?](https://github.com/AKSHAYKUMARDHAR/Will-My-Policy-Pay),
[Is This a Scam?](https://github.com/AKSHAYKUMARDHAR/Is-This-A-Scam) and
[UPI Triage Agent](https://github.com/AKSHAYKUMARDHAR/UPI-Triage-Agent).
