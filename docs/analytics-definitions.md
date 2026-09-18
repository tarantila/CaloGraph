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
