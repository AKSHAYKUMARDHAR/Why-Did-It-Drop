# PRD: "Why Did It Drop?" — an analytics agent that explains metric changes, and says when it can't

Akshay Dhar · 9 October 2026 · Status: draft v1

## TL;DR

When a product metric moves (checkout conversion down 8% this week), a PM asks "why?" and an analyst
loses hours slicing dashboards. In 2026 Amplitude and Mixpanel shipped AI agents that answer that
question. I found no independent measurement of how often they're right, and Mixpanel's own guide warns
that its agent "will confidently explain the wrong thing" when tracking is broken.

**Why Did It Drop?** is an investigator for e-commerce product metrics that is tested before it's trusted:

- **Code does the arithmetic.** Every number comes from a query on the data.
- **Checks come first.** It tests whether the change is real at all, and whether tracking broke, before it
  looks for a cause.
- **The model's job is narrow.** It reads the evidence, picks follow-up checks and writes the diagnosis.
- **Every claim is verified.** Every number in its answer must match a query result. Where the evidence is
  thin, it says "can't tell".

It runs on real event data: the GA4 export of Google's Merchandise Store, November 2020 to January 2021.
It is scored on a frozen benchmark: incidents planted in that real data with known causes, plus quiet
weeks where the right answer is "nothing unusual". Its scores are published, failures included.

The costly error is a **confident wrong cause**. It sends engineers after the wrong release, or tells a
CEO that marketing broke something it didn't. The release gate:

- wrong causes ≤ 5% of cases;
- right diagnoses ≥ 75%;
- false alarms ≤ 10% on quiet weeks;
- 100% of numbers traced to a query.

## Problem and evidence

**"Why did it move?" is a weekly tax on every product team.** Dashboards show that a metric moved, not
why. A data-team survey reported by Basedash puts ad hoc requests at about a third of data teams' time
(Atlan, State of Data Teams 2024, as reported), and in a Fivetran survey analysts said they spend more
than half their time on work other than analysis. I found no survey that isolates metric-change
investigations. The figures above are about ad hoc requests in general, and I treat them as directional.

**The big analytics vendors shipped agents for exactly this in 2026:**

- **Amplitude** launched a Global Agent in February 2026 that "investigates root causes, and explains what's
  driving changes across funnels". It also launched a Dashboard Monitoring Agent that "detects meaningful
  metric changes within hours".
- **Mixpanel's RCA Agent** "returns which dimensions explain the shift, ranked by significance, plus a
  plain-language read and a confidence level".

The pressure to automate the job is real.

**Nobody publishes how often these agents are right, and the vendors' own material shows how they fail:**

- **Rates versus counts.** In the winning story of Amplitude's 2026 AI Impact Awards, the agent first
  "linked the spike to a recent mobile platform release". When the user challenged it, it "recalculated by
  error rate rather than raw counts", which "changed the picture entirely". The cause was a different
  release.
- **Broken tracking.** Mixpanel's guide: "RCA explains what's in your data, not what's true. If the
  underlying events weren't tracked correctly, RCA will confidently explain the wrong thing."
- **Mix shift.** Conversion can fall with no segment getting worse, because more low-intent traffic arrived
  (Simpson's paradox). An agent that only ranks segments by their contribution to the drop will blame
  whichever segment grew.
- **Calendar effects.** A drop on the day after a promotion ends is expected, not an incident. Mixpanel asks
  teams to enter this "business context" for exactly that reason.

**The algorithms are well studied; the trust layer isn't.** Microsoft's Adtributor (NSDI 2014) ranks the
dimensions that explain a revenue change by "explanatory power" and "surprise", and reported over 95%
accuracy in its own ad system. Critics note that it assumes a single-dimension cause. Deterministic
methods like this are a strong baseline, and the open question is what a language model adds on top of
them, and at what risk. This project measures that.

## Users and jobs to be done

- **Product manager at a consumer app or e-commerce company.** *"Conversion dropped this morning. Before
  standup, tell me if it's real, where it's concentrated, and what most likely caused it, with numbers I
  can show my lead."*
- **Analyst on call.** *"Do the first hour of slicing for me, show me every query you ran, and don't hide
  it when the data can't explain the change."*
- **Growth or category lead.** *"Tell me whether the revenue drop is traffic, conversion or basket size,
  and which channel or category it sits in."*

## Competitive landscape and positioning

| Product | What it does | Gap |
| --- | --- | --- |
| Amplitude Global Agent, Dashboard Monitoring Agent (2026) | Autonomous investigation inside Amplitude | Inside one vendor; no published accuracy |
| Mixpanel RCA Agent | Ranks dimensions behind a shift, with a confidence level | Vendor warns it explains broken tracking confidently |
| Tellius, Anamap Cartos | Agentic root-cause tools for BI and dashboards | Vendor claims only |
| Adtributor and successors | Deterministic dimension ranking | Single-dimension assumption; no narrative, no tracking check |

**Positioning:**

- **Tested, not trusted.** A public benchmark of known incidents, with scores that include its failures.
- **Tracking first.** It checks whether the data can be trusted before it explains it.
- **Rates, mix and noise first.** It never blames a segment without separating rate changes from mix
  changes, and real changes from noise.
- **Every number traced.** Each figure in an answer links to the query that produced it.

## Goals and non-goals

Goals:

1. Name the right cause (type, segment and start date) for a planted incident in real data.
2. Never state a wrong cause with confidence; say "can't tell" instead.
3. Stay quiet when nothing unusual happened, and say "expected" for calendar effects.
4. Make every claim checkable in one click.

Non-goals (v1):

- **Proving causation.** It localises and classifies a change. It doesn't replace an experiment, and it
  says "likely cause".
- **Any warehouse, any schema.** v1 runs on a fixed semantic layer over GA4-style e-commerce events.
  Connecting other schemas is a later step.
- **Real-time alerting.** v1 answers a question about a period. It doesn't watch a stream.

## MVP scope

1. **Data.** The GA4 demo-store export (`bigquery-public-data.ga4_obfuscated_sample_ecommerce`),
   extracted once into session-level and event-level tables and frozen in the repo, so everything runs
   locally in DuckDB.
2. **Semantic layer.** Metric and dimension definitions are written once, in code.
   - **Metrics:** sessions, users, conversion rate, add-to-cart rate, checkout-start rate,
     checkout-completion rate, orders, revenue, average order value, revenue per session.
   - **Dimensions:** device category, operating system, browser, country, traffic medium and source,
     new or returning user, landing-page group, and item category for basket metrics.
3. **Business calendar.** Known dates (Black Friday, Cyber Monday, Christmas, New Year) are given as
   context, as Mixpanel's "business context" does.
4. **Investigation.** In order:
   - **Is it real?** Compare against the same weekdays in prior weeks, with a noise band.
   - **Can the data be trusted?** Tracking health checks: an event that vanishes or doubles, impossible
     sequences such as a purchase with no checkout, or one browser going silent.
   - **Where does it sit?** Split the change into funnel steps; separate rate change from mix change; rank
     dimensions and pairs of dimensions by contribution.
   - **What does it mean?** The model reads this evidence pack, may ask for up to three follow-up checks,
     and writes the diagnosis:
     - type: product or funnel break, traffic loss, traffic-mix shift, tracking issue, price or basket
       change, expected calendar effect, no real change, or can't tell;
     - the segment, the start date, the size, a confidence level and the next step to take.
5. **Checks on the answer.** Every number in the narrative must match a query result. The segment named
   must be one the evidence supports. The start date must show in the daily series. Anything that fails
   is removed or downgraded to "can't tell".
6. **Interface.**
   - A web page: pick a metric and period, or a case from the gallery.
   - It shows the verdict card, the charts and tables behind it, and the trace of every query.
   - The gallery shows every benchmark case pre-run, so it works without a live model. Live
     questions run on a small service with a daily cap, if free hosting allows.

## How answers stay trustworthy

- **The model never does arithmetic.** It only cites numbers the tools returned, and code verifies each
  one.
- **The order is fixed.** The "real?" and "tracking?" checks run before attribution, so the model can't
  skip them.
- **Rates before counts, and mix before blame.** These are built into the evidence pack, not left to the
  prompt.
- **Abstention is a valid answer** and isn't scored as an error. A confident wrong cause is the error the
  gate is built around.

## Safety, privacy and compliance

- The data is a public, obfuscated Google demo dataset (CC BY 4.0 page; BigQuery public datasets program).
  It contains no personal data, and user IDs are pseudonymous.
- It is not a production monitoring system. It says "likely cause", and links every number to its query.
- API keys and the BigQuery service-account key live only in gitignored files and hosting secrets.

## Success metrics

| Metric | Target | How measured |
| --- | --- | --- |
| Wrong cause stated with confidence | ≤ 5% of benchmark cases | Held-out benchmark |
| Right diagnosis (type + segment + start date) | ≥ 75% of incident cases | Held-out benchmark |
| False alarms on quiet weeks | ≤ 10% | Held-out null cases |
| Numbers in answers traced to a query | 100% | Code check, every answer |
| Time to a first answer | < 2 minutes | Logged runs |
| Value of the model over the deterministic baseline | Measured, not assumed | Same cases, both versions |

## Evaluation plan

**Data, frozen first.** The GA4 extract is stored with its hashes before any prompt exists.

**The benchmark: planted incidents in real data.** I write an incident catalogue before any prompt. Each
type is modelled on failures documented by the vendors and in incident write-ups:

- **Funnel break:** checkout completion falls for one OS or browser from a date, like Mixpanel's iOS
  payment-SDK example.
- **Traffic loss:** one source or medium loses sessions (a campaign ended, or an SEO drop).
- **Traffic-mix shift:** a burst of low-intent or bot sessions from one country lowers the conversion rate
  while every segment's own rate is unchanged.
- **Tracking issues:**
  - a purchase event stops firing for one browser while checkouts continue;
  - duplicate purchase events inflate revenue.
- **Price or basket change:** average order value falls in one item category.
- **Calendar effects:** real movements around Black Friday, Christmas and the New Year, with no planting.
  The right answer is "expected".
- **Quiet weeks:** windows with no planted incident, checked by hand for real anomalies. The right answer
  is "no real change".
- **Too small to tell:** incidents below the noise band. "Can't tell" is the right answer.

A seeded generator draws each case's segment (with enough volume), start date, size and duration. It
writes the case, the question asked and the expected answer before any prompt exists. Planted data is
created by changing a copy of the real sessions, such as dropping purchases or adding sessions, so the
rest of the data stays real.

**Splits.**
- 30 development cases.
- 40 held-out cases, run once. The held-out set includes **two incident types that don't appear in
  development** (planned: a currency or units bug, and a broken landing page), reported separately to show
  whether the agent generalises.

**Scoring.** Each case is scored as:
- **right:** the type matches, the segment matches (or is an accepted parent or child), and the start date
  is within one day;
- **wrong:** a confident answer naming the wrong type or segment;
- **abstained:** "can't tell".

On quiet and calendar cases, any incident claim is a **false alarm**.

**Versions compared on the same cases:**
- a deterministic baseline (Adtributor-style ranking plus fixed rules);
- the model with tools and no evidence pack;
- the evidence pack plus the model;
- the evidence pack plus the model plus the checks, which is what ships.

**Models.** gemini-3.5-flash-lite first, because the free tier allows many requests a day.
gemini-3.5-flash is compared on development within its 20 requests a day. The cheaper model ships if it
passes.

**Release gate (held-out, run once):** all four targets above. Held-out cases are never edited after a
run. If the gate fails, I fix the system on development cases and draw a fresh held-out set.

## Launch and distribution plan

- **Live demo:** a gallery of every benchmark case with its full investigation trace, plus live questions
  if hosting allows.
- **The benchmark itself is published:** cases, generator and expected answers, so anyone can test their
  own agent on it.
- **Portfolio:** a case study and this PRD, with the held-out results, pass or fail.

## Timeline and risks

| Step | Work |
| --- | --- |
| 1 | Data extract and freeze; semantic layer; incident catalogue and generator; benchmark cases written |
| 2 | Evidence pack, deterministic baseline, scoring harness |
| 3 | Model investigator, checks, development runs |
| 4 | Held-out run, web demo, case study |

Risks:
- **Data access needs a Google Cloud project with BigQuery.** A one-time setup by me, the owner.
- **Planted incidents may be easier than real ones.** Mitigations: realistic sizes, real background
  noise, two unseen incident types in held-out, and calendar cases where nothing is planted.
- **The obfuscated data has placeholders and limited internal consistency.** Mitigations: document it,
  and choose segments with clean values.
- **Free-tier quota.** Mitigations: cache every model answer, and use 1 to 3 model calls per case.

## Open questions

- Does the model add anything over a strong deterministic baseline? The benchmark answers this either
  way.
- Which metrics matter most to PMs at Indian consumer companies? Test with three PMs after launch.

## Sources

- Amplitude, "Amplitude Introduces Agentic AI Analytics for the Next Era of Product Experiences" (Feb 2026):
  https://amplitude.com/press/amplitude-introduces-agentic-ai-analytics-for-the-next-era-of-product-experiences
- Amplitude, 2026 AI Impact Awards winners: https://amplitude.com/blog/ai-impact-awards-2026-winners
- Mixpanel Agent use cases (RCA): https://docs.mixpanel.com/guides/mixpanel-agent/use-cases
- Mixpanel, anomaly detection and root cause analysis (2023): https://mixpanel.com/blog/anomaly-detection-custom-alerts-root-cause-analysis/
- Tellius, AI data analysis agents compared (vendor): https://www.tellius.com/resources/blog/best-ai-data-analysis-agents-in-2026-12-platforms-compared-for-nl-to-sql-autonomous-investigation-and-governance
- Anamap Cartos launch: https://www.producthunt.com/p/anamap
- Bhagwan et al., "Adtributor: Revenue Debugging in Advertising Systems", NSDI 2014:
  https://www.usenix.org/conference/nsdi14/technical-sessions/presentation/bhagwan
- Basedash, on ad hoc reporting (reports Atlan 2024 and Mode 2024 figures): https://www.basedash.com/blog/what-is-ad-hoc-reporting-how-on-demand-data-analysis-works
- Fivetran, analyst survey: https://www.fivetran.com/blog/youre-not-getting-the-most-out-of-your-data-analysts
- Google, GA4 e-commerce demo dataset: https://developers.google.com/analytics/bigquery/web-ecommerce-demo-dataset
