# Changelog

All notable changes to CaloGraph are documented in this file. The project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.2] - 2026-07-27

### Added

- Added a dashboard screenshot to the README.
- Added an explicit 60-day YAZIO history backfill to the micronutrient
  analysis.
- Added an optional, versioned maintenance-calorie estimate to budget settings.

### Changed

- Standardized public project documentation on English while keeping the
  application interface German.
- Renamed the Compose files to the conventional `docker-compose.yml` and
  `docker-compose.dev.yml` filenames.
- Documented the work-in-progress status and planned per-account language
  selection.
- Added a canonical `CALOGRAPH_PUBLIC_URL` for externally shared links,
  including user invitations.
- Restricted Uvicorn forwarded-header handling to configured proxy networks
  instead of trusting every sender.
- Moved the micronutrient explanation below the values and made the EU
  reference bars and coverage requirements explicit.
- Changed the calendar to calendar-month navigation with budget-based
  green/orange/red classifications and clearer summary metrics.
- Added week and custom date ranges to weekday analysis and moved the calendar
  directly below weekday analysis in the sidebar.
- Reworked the public entry screen into a minimal sign-in method selection;
  credentials appear only after choosing password sign-in.

### Fixed

- Corrected YAZIO micronutrient values from their gram source unit to canonical
  milligrams or micrograms, including a migration for existing samples.
- Corrected calendar average calculations for decimal values returned by the
  API.
- Corrected the daily calorie average for decimal values returned by the API.

## [0.1.1] - 2026-07-23

### Added

- Personal user accounts with invitations and strictly isolated nutrition data.
- Manual and scheduled YAZIO sync with encrypted credentials, a six-hour
  interval, and randomized scheduling.
- Micronutrient analysis for vitamins and minerals with data coverage and a
  neutral EU NRV comparison.
- Versioned calorie and macronutrient budgets with accurate daily and weekly
  calculations.
- Operations, backup, restore, and update documentation with supporting
  scripts.

### Changed

- Redesigned the nutrition overview, weekly view, calendar, trends, and data
  quality screens.
- Data status now reports whether nutrition data exists and no longer treats
  low values as incomplete.
- Removed activity, hydration, and weight data from import, analysis, and the
  interface.
- Replaced placeholder branding and application icons with the CaloGraph
  assets.

### Security

- YAZIO credentials are stored encrypted.
- Imports, targets, and analytics are consistently scoped to their user.

[Unreleased]: https://github.com/tarantila/CaloGraph/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/tarantila/CaloGraph/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/tarantila/CaloGraph/compare/b4ca2cf...v0.1.1
