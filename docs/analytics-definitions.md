# Analytics definitions

## Days and weeks

A sample is assigned to the local date of its start time in the user's time
zone. Weeks start on Monday by default. A weekly budget is the sum of the
budget version valid on each day; deviation is intake minus budget.


## Optional activity credit

Each target version may either ignore activity energy or credit all
`active_energy_kcal` values from exactly one selected source. On a credited
day, `effective_budget_kcal = target_kcal + active_energy_kcal` and, when a
maintenance estimate exists, `effective_maintenance_kcal =
maintenance_kcal + active_energy_kcal`. The effective deviation uses the
effective budget. Protein and macro targets never change.

Activity values from other sources are not summed, and a missing selected-source
value contributes zero rather than an estimate. Activity availability is shown
separately and does not change nutrition-data completeness. The selected source
and mode are part of each historical target version, so later configuration
changes do not revalue past days.

The Account data-source preference for `activity_energy` is a current
configuration, not a retroactive rewrite. If credited activity is enabled and
the selected provider changes, CaloGraph closes the currently effective target
at the change date and creates a new target version with the new source. Past
days retain their original source and target values. A same-day target may be
updated in place because it has no earlier effective day.

## Weight history

Weight is read as a scalar daily history from the ordered providers selected
for the `weight` data area. CaloGraph never sums providers: each local day
uses the first configured provider with an actual sample. An unavailable
provider is skipped only within that persisted priority chain; if no configured
provider can resolve a day, that day remains without a value and a completely
unavailable selection fails closed. Multiple samples on one local day resolve
to the latest sample by timestamp.

## Activity presentation

The effective budget and credited activity are shown together where they
explain a user's calorie budget: the overview, target history, daily view,
calendar, weekly detail, and trends. Trends uses `activity_credit_kcal` as a
separate stacked bar segment and keeps the 7-, 14-, and 28-day averages based
only on calorie intake.

The weekly chart keeps its budget lines and presents the aggregate activity
credit in the weekly detail table. Weekday analysis intentionally does not add
an activity metric because its weekday aggregates do not preserve the
day-specific target/source history. Data-status and import pages describe
coverage and import operations, so they do not duplicate budget credit there.

## All-time budget balance

The Trends budget balance summarizes all tracked nutrition days from the first
tracked day through today, independently of the chart's selected range. It
counts `tracked_days`, `within_budget_days`, `over_budget_days`, and
`over_maintenance_days` using each day's historical `effective_budget_kcal`
and `effective_maintenance_kcal`. Days without a valid historical budget
remain tracked but are reported as `unclassified_budget_days` rather than
being assigned a current or synthetic target. Processing uses bounded chunks
of tracked dates, so empty calendar days are not materialized and query
parameter size remains bounded.

## Missing values

Days without measurements remain `null`. They are not interpreted as zero
calories or automatically treated as complete fasting days. Rolling 7-, 14-,
and 28-calendar-day averages use only recorded days with a status of `complete`
or `probably_complete` by default.
Analysis Nutrition trends may serialize macro measurements as JSON numbers or
decimal strings. `null` remains missing data; numeric zero is a present value.


A user without a nutrition target still receives ordinary analytics data, but
target, maintenance, deviation, and budget values remain `null`; calendar
entries use the neutral `no_target` classification until that user saves a
target. CaloGraph never substitutes another account's target or a synthetic
calorie/protein default.

## Data availability

A day is recorded as soon as a calorie value has been imported. The value's
size, the calorie budget, number of meals, and availability of macronutrients
do not affect this status. Nutrition data without a calorie value is reported
separately; a day without any nutrition sample has the status `no_data`. A
manual override takes precedence.

The default data-status range starts with the first day that actually contains
nutrition data. Calendar days before the user began tracking are therefore not
reported as gaps. Days without nutrition data after this starting point remain
visible. Every imported calorie value contributes to trend averages, even when
it is substantially below the budget or the user's personal average.

## Micronutrients

For each nutrient, micronutrient analysis divides the selected range total by
the nutrition days from the same source. Missing nutrient values on a nutrition
day contribute zero to the daily average. The proportion of days that
explicitly supplied a value is therefore displayed separately as data
coverage.

At 70 percent coverage or above, the average is compared with the adult
nutrient reference value in Annex XIII of Regulation (EU) No 1169/2011. Values
below 80 percent are neutrally labeled "below reference." This status is not
evidence of a deficiency and is not a supplement recommendation. Choline is
shown without a percentage comparison because the EU table used here does not
define an NRV for it.

Canonical requests selected through a nutrition preference support inclusive
bounded ranges of up to 3661 days. Provider reads remain bounded internally to
contiguous chunks of at most 31 days; the public totals, averages, coverage,
recorded days, and status are calculated once for the complete requested range.
`period=all` remains unsupported for canonical preference requests and returns
HTTP 422. Explicit legacy `source` requests retain their existing range and
aggregation semantics.

The existing `GET /api/v1/analytics/micronutrients` endpoint keeps its established
legacy-source selection, aggregation, coverage, and reference-status behavior.
The Analyse workspace uses the separate canonical-only
`GET /api/v1/analytics/nutrition/micronutrients` endpoint instead.

### Canonical Analyse series

The Analyse endpoint requires an authenticated account with a readable,
user-scoped nutrition-provider selection. A missing, unavailable, or unreadable
selection returns HTTP 503; it is not represented as an empty catalog. A
readable provider chain with no nutrient evidence returns HTTP 200 with the
catalog entries and null totals/series values.

Ranges use 1–365 inclusive account-local dates. Provider selection is resolved
once per request; internal provider reads remain at most 31 days. All required
chunks finish before totals, coverage, contributor counts, and chronological
daily series are constructed for one complete response. Any required-chunk
failure or unsafe conflict fails the full request with the existing HTTP 503
provider problem; no partial aggregate is returned. This does not establish a
cross-chunk MVCC snapshot guarantee.

The top-level `selected_provider` is the head of the configured preference
chain, not a claim that it supplied every date. Each detailed series point names
its actual daily winner in `provider_key`. Metric totals, `days_with_data`, and
contributor counts derive from those same winning matrices. Explicit zero is
evidence and contributes to totals and coverage; missing or unusable values
remain `null`. The optional EU reference value is secondary context only; this
endpoint does not calculate percentages or assign a health-status label.

### Canonical Analyse nutrition days and meals

`GET /api/v1/analytics/nutrition/analysis` is authenticated and reads one
inclusive account-local range of 1–365 days. Internally, each provider period
is read in chunks of at most 31 days; chunk boundaries do not change day
selection. Invalid or reversed ranges return HTTP 422. Missing, unavailable
or unreadable configured sources return a provider problem (HTTP 503), not
empty evidence.

The configured `selected_provider` describes the head of the ordered source
chain. For each date independently, the first source whose four core daily
nutrition metrics—energy, protein, carbohydrates and fat—are complete and
resolved, and which has at least one supplied core value (including explicit
zero), wins the **whole day**. Each metric is exposed only when its own
candidate is complete and resolved and has a supplied value or explicit zero;
unavailable or unresolved secondary metrics remain `null`. Daily totals and
named meals come only from that winning provider/source instance; no individual
metric or meal is filled from another provider. A date without such a winner
returns HTTP 200 with `provider_key: null`, seven null metrics and `meals: []`.
`null` means absent or unusable evidence; numeric zero is a present value,
never a missing-day substitute. Unsafe conflicts or duplicate candidates fail
closed.

Meals are current, non-tombstoned consumption events from the winning
account/provider/source only, with real persisted food-snapshot names or
allowlisted provider names (Google Health `food_display_name`, Apple Health
`food_name`). Unnamed entries are omitted, not labeled with invented food
names. Their seven optional nutrient values come solely from safe event
fields, not a division of the day's totals. Reads never import, project
or change persisted evidence.


## Analysis registry and sparse weight summary

The authenticated `GET /api/v1/analytics/registry` response lists canonical
analysis keys, domain/category identifiers, availability, and reader
capabilities; UI labels, icons, and routes are frontend presentation metadata.
The authenticated `GET /api/v1/settings/analysis-preferences` returns
`active_card_ids`, the canonical mixed ordered sequence of active Analysis Hub
cards. Built-in domain cards and available standalone metric cards share this
sequence and can be reordered together. The canonical `PUT` replaces this
sequence with `active_card_ids`. The response also includes `analysis_keys` and
`summary_order` as compatibility projections. Legacy `PUT` requests using
either or both legacy fields remain accepted and are translated into the
canonical sequence; they are not the current canonical write contract. A card
is active only when its ID is present in `active_card_ids`.

`GET /api/v1/analytics/weight` retains its sparse daily `points` and adds a
summary over those same selected daily readings. `measurement_count` counts
returned points; `latest` is the last actual point; `change_kg` is the newest
minus oldest actual value and is null with fewer than two readings. Average,
minimum, and maximum use actual points only. No value is interpolated for an
unmeasured day.

### Request period capabilities

Every successful response from trends, general micronutrients, weight, activity
verification, Analyse nutrition, and Analyse micronutrients explicitly includes
`period_capabilities: {supports_all_data: false, all_data_start_date: null}`.
These readers do not expose a reliable earliest date for their complete
reader-specific data. Neither the first returned point nor another reader's
metadata establishes that boundary.

Existing inclusive range limits remain unchanged: 3661 days for trends, general
micronutrients, weight, and activity verification; 365 days for Analyse nutrition
and Analyse micronutrients. Those maxima are not all-data ranges.
The registry's static `min_days`, `max_days`, and `custom_range` describe bounded
requests, not dynamic permission to select all data.

This negative capability does not change Legacy API acceptance of `period=all`,
its historical start boundary, or its existing achievement effects, including
`the_big_picture`. Preference-selected trends and general micronutrients retain
their existing HTTP 422 all-period gates. Daily and weekdays responses remain
unchanged, including their existing `period=all` acceptance.

## Micronutrient provider metadata

The micronutrient response keeps the legacy `source`, `available_sources`, and
`last_updated_at` fields unchanged. When public canonical provider metadata is
enabled, it additionally returns `providers`, sorted by `provider_key`:

```json
[
  {
    "provider_key": "yazio",
    "latest_evidence_observed_at": "2026-01-01T12:00:00+00:00"
  }
]
```

`provider_key` identifies the canonical provider family. It is not a source
selection, `SourcePriority`, or a `source_instance_id`. The
`latest_evidence_observed_at` timestamp is derived from canonical evidence and
is intentionally distinct from the legacy `last_updated_at`, which remains
based on `HealthSample.updated_at`. Providers are user-scoped and range-scoped;
providers without qualifying evidence are omitted.

## Calendar

The calendar opens on the current month and can navigate to previous calendar
months. The current month is month-to-date; completed months include every day
from the first through the last day.

Each day uses the target version valid on that date. The effective calorie
budget is the primary threshold: it is the unchanged base budget unless that
version enables activity credit for its selected source. Intake at or below the
effective budget is green. Intake above it is orange unless it is also above
the effective maintenance estimate, in which case it is red. Thus an intake
above the effective maintenance estimate but still within a higher effective
budget remains green. Without a maintenance estimate, a day above the effective
budget remains orange instead of being assigned an arbitrary red threshold.
Missing calorie values retain a separate neutral status. Text supplements every
color.

The calendar's generic "over budget" count includes both orange and red days.
The Trends budget balance uses mutually exclusive classes: `over_budget_days`
contains only days above the effective budget that do not exceed effective
maintenance, while `over_maintenance_days` contains the red subset separately.
calorie values only; missing days are excluded rather than interpreted as zero.
