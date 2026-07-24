# Changelog

All notable changes to CaloGraph are documented in this file. The project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Added a dashboard screenshot to the README.

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

[Unreleased]: https://github.com/tarantila/CaloGraph/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/tarantila/CaloGraph/compare/b4ca2cf...v0.1.1
