# Analytics definitions

## Days and weeks

A sample is assigned to the local date of its start time in the user's time
zone. Weeks start on Monday by default. A weekly budget is the sum of the
budget version valid on each day; deviation is intake minus budget.

## Missing values

Days without measurements remain `null`. They are not interpreted as zero
calories or automatically treated as complete fasting days. Rolling 7-, 14-,
and 28-calendar-day averages use only recorded days with a status of `complete`
or `probably_complete` by default.

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

## Calendar

Deviations relative to the applicable budget are grouped into below −15%,
−15% to −5%, −5% to +5%, +5% to +15%, and above +15%. Missing and incomplete
days receive their own classes. Text and symbols supplement color.
