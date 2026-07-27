# Changelog

Notable changes to `pyplusportal` and the PlusPortal Home Assistant integration. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org), with the exception in CLAUDE.md that below 1.0.0
a major-equivalent change moves the minor instead.

`scripts/check_release_notes.py` refuses to let a tag publish without an entry here, and a
major-equivalent version move must carry a `### Breaking` subsection — see PP-SEC-007 and
PP-SEC-008 in [docs/specs/safety.md](docs/specs/safety.md).

## [Unreleased]

### Added
- Per-channel statistics: import and export are now tracked as separate statistic series.
- The energy price is entered in EUR/kWh instead of ct/kWh.
- The tariff (energy price, base price, monthly advance) can be set during initial setup,
  not only afterwards through options.

### Breaking
- Statistic ids changed from `plusportal:<meter>_<kind>` to
  `plusportal:<account>_<meter>_<channel>_<kind>`, splitting import and export into separate
  series. Existing installations get new entities for the same meter; the old, single-series
  ones become orphaned and can be removed with `scripts/ha_prune.py`.
- The energy price option is now read as EUR/kWh. A value previously entered in ct/kWh must
  be divided by 100 when re-entering it after updating.

## [0.1.0] - 2026-07-26

### Added
- Initial release: read metered electricity consumption from a PlusPortal account and
  project the billing-year cost against a user-configured tariff.
