# Changelog

All notable changes to Hydra Detect are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Repository baseline scaffolding: SECURITY.md, ISSUE_TEMPLATE forms, dependabot config.
- Fixed-wing detection-only platform profile (`[vehicle.fw]`). The pipeline
  now refuses follow / drop / strike / pixel-lock approach commands when the
  FW profile is active, lowers `min_track_frames` to 2 for brief overpasses,
  and restricts `allowed_vehicle_modes` to `AUTO,LOITER,CRUISE`. Refusals are
  logged to `hydra.audit` and surfaced via STATUSTEXT. Closes #70.

### Changed
- `[vehicle.fw]` in `config.ini.factory` and `config.ini` now declares
  `reserved_channels = 1,2,3,4` and `autonomous.allowed_vehicle_modes`. The
  config schema recognises the additional vehicle.fw keys without warnings.

### Fixed
- Bumped pytest to >=9.0.3 to resolve Dependabot CVE alert (medium, test-only).

### Security
- Enabled GitHub Dependabot vulnerability alerts and automated security update PRs.
- Enabled GitHub secret scanning + push protection.

## [2.1.0]

Current production release. See git history for details:
[2.0-rc1...v2.1.0](https://github.com/rmeadomavic/Hydra/compare/v2.0-rc1...v2.1.0)

## [2.0-rc1]

Initial v2 release candidate. See git history for details.

[Unreleased]: https://github.com/rmeadomavic/Hydra/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/rmeadomavic/Hydra/releases/tag/v2.1.0
[2.0-rc1]: https://github.com/rmeadomavic/Hydra/releases/tag/v2.0-rc1
