# Benchmark guide

How the benchmark is built and scored. It was written, and the cases generated and committed, before
any prompt for the investigator existed. Held-out cases are never changed after a run; mistakes found later
are logged at the end of this file.

## The data

Google's GA4 demo-store export (Google Merchandise Store, 2 Nov 2020 - 31 Jan 2021): 4,295,584 events, built
into 360,129 sessions (`data/sessions.parquet`, hashes in `data/manifest.json`). Weeks run Monday to Sunday.
A question always compares one week with the week before.

The real data has three tracking failures of its own, found by the health checks and confirmed by hand
before the benchmark was built:

| When | What happened |
| --- | --- |
| until 25 Nov 2020 | `add_to_cart` is barely recorded: none until 16 Nov, a burst 16-20 Nov, none 21-24 Nov |
| from 19 Nov 2020 | purchase events fire about twice per order (1.0 per order until 17 Nov, 1.8-1.96 from 19 Nov), inflating revenue |
| from 26 Jan 2021 | orders are recorded with zero revenue (about 2 a day before; 29-50 a day after; all of them on 31 Jan) |

It also has strong real seasonality (a December peak, a Christmas and New Year slump, a January recovery)
and weekly shifts in landing pages and channels from campaigns. `scripts/survey_weeks.py` records all of it
in `data/natural_weeks.json`.

## The question and the answer

Every case asks, neutrally: *"What changed in {metric} in the week of {date}, compared with the week before,
and why?"*

The answer has:

| Field | Values |
| --- | --- |
| `verdict` | `incident` (something specific happened), `broad` (a real change spread evenly across segments, as seasonality or demand would cause), `no_change` (within normal variation), `cant_tell` |
| `type` (incident only) | `product_break`, `traffic_loss`, `mix_shift`, `tracking_issue`, `basket_change` |
| `segment` (incident only) | up to two dimension values, e.g. `{"os": "iOS"}`; `{}` means site-wide |
| `start_date` (incident only) | the first day affected |

## Case kinds

**Planted incidents.** An incident is planted in a copy of the real sessions (`wdid/incidents.py`). The type,
week, segment, question metric, start day and size are drawn with a fixed seed (`scripts/build_benchmark.py`):

| Planted type | What changes | Correct `type` | Question metric |
| --- | --- | --- | --- |
| `product_break` | a funnel step (purchase, checkout start or add-to-cart) stops for a share of a segment's sessions, with every later step | `product_break` | conversion rate or revenue |
| `traffic_loss` | a share of a channel's or country's sessions disappears | `traffic_loss` | revenue or sessions |
| `mix_shift` | copies of a segment's real bounced sessions are added (a bot burst or a low-intent campaign) | `mix_shift` | conversion rate |
| `tracking_dup` | purchase events fire twice in a segment: revenue doubles, orders don't | `tracking_issue` | revenue or average order value |
| `tracking_gap` | checkout-start events stop in a segment while purchases continue | `tracking_issue` | checkout-start rate |
| `basket_change` | revenue per order falls by a factor in a segment | `basket_change` | revenue or average order value |
| `landing_break` (held-out only) | sessions landing on one site section stop viewing products | `product_break` | conversion rate |
| `units_bug` (held-out only) | a country's revenue is recorded x100 | `tracking_issue` | revenue |

Placement rules:
- Weeks starting 7 Dec - 11 Jan only. The base week of 7 Dec is 30 Nov, the first full week after both
  November tracking failures ended (25 Nov). This also avoids the revenue failure of 25 Jan and the natural
  concentrated change of 18 Jan.
- No case on a metric that a real tracking finding touches that week. For example, the 28 Dec week has a real
  Safari double-firing, so no planted revenue or order-value case goes there.
- A segment is eligible if it has at least 5% of the week's sessions and isn't a placeholder (`<Other>`,
  `(not set)`, `(data deleted)`).
- The segment's dimension must have no naturally distinct segment, in that week, for either the question metric
  or the metric the incident moves directly. So the planted incident is the only concentrated change of its
  kind.
- Starts fall Monday to Friday of the question week, so at least three days are affected.

Detectability, measured before any agent runs, on the metric the incident moves directly:
- product breaks: the step's rate;
- traffic loss and mix shift: sessions;
- basket change: average order value;
- tracking incidents: the matching health check.

| Label | Rule | Right answers |
| --- | --- | --- |
| clear | the planted segment is distinct from the rest of the site (difference-in-differences \|z\| >= 3 and at least 15% apart), or the health check fires | the incident: right type, matching segment, start within one day |
| too small | \|z\| < 1.5 and no health finding | `no_change` or `cant_tell`; the correct incident also counts as right |

A draw between the two is redrawn, so every planted case is clearly one or the other.

**Natural cases**, labelled by hand from the survey:
- **tracking:** the real failures above, site-wide;
- **broad:** a real change (beyond normal variation) with no distinct segment, in a calendar week;
- **quiet:** in weeks from 7 Dec (not 18 Jan), a change within normal variation (\|z\| < 1.5), with no
  distinct segment and no health finding that touches the metric. Expected `no_change`; `broad` is accepted.

The natural cases were seen while the engine was being built, so they are reported separately from planted
cases, whose parameters were drawn after the engine was written.

## Scoring

| Outcome | When |
| --- | --- |
| right | the verdict is acceptable and, for an incident, type, segment and start date match |
| wrong | a confident incident with the wrong type or segment, or a wrong start date |
| missed | `no_change` on a clear incident, or on a broad change |
| abstained | `cant_tell` |
| false_alarm | an `incident` on a quiet or broad case |

**Segment match is by the sessions it covers, in the question week**, so what counts is the traffic named,
not the label. For example, "os = iOS & browser = Safari" matches a planted "os = iOS": it covers 89% of the
iOS sessions in the week of 7 Dec. Names that sound alike don't count unless the sessions overlap; in this
obfuscated data "os = Web" and "device = desktop" overlap only 46%. Write A for the sessions in the
answer's segment and P for the planted segment's sessions. The answer matches if:
- the two overlap almost entirely (|A ∩ P| / |A ∪ P| >= 0.8), or
- the answer is more specific but still covers most of the planted segment (|A ∩ P| / |A| >= 0.9 and
  |A ∩ P| / |P| >= 0.5).

A site-wide answer (`{}`) matches only a site-wide case.

**Release gate** (held-out, run once):
- wrong ≤ 5% of cases;
- right ≥ 75% of incident cases;
- false alarms ≤ 10% of quiet and broad cases;
- 100% of numbers in answers traced to the engine.

## Changes after a run

None yet.
