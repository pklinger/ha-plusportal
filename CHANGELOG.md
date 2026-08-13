# Changelog

Notable changes to `pyplusportal` and the PlusPortal Home Assistant integration. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org), with the exception in CLAUDE.md that below 1.0.0
a major-equivalent change moves the minor instead.

`scripts/check_release_notes.py` refuses to let a tag publish without an entry here, and a
major-equivalent version move must carry a `### Breaking` subsection — see PP-SEC-007 and
PP-SEC-008 in [docs/specs/safety.md](docs/specs/safety.md).

## [0.3.3] - 2026-08-13

### Changed
- Documentation only, and released because documentation is not visible otherwise: HACS
  renders a repository's README from its latest release, not from the default branch. This
  publishes the rewritten front page — what the integration produces, stated before the
  legal notice — and its disclosure that most of the code, tests and prose here were
  written by an AI agent under human direction, next to the guard rails that make that
  checkable. The release runbook is corrected too. Neither the integration nor the library
  changed; updating gains you nothing and costs you nothing.

## [0.3.2] - 2026-08-12

### Changed
- `hacs.json` declares Germany as the country the integration serves, which HACS asks of
  repositories with a limited audience and reads from the released file — hence a release
  of its own. Neither the integration nor the library changed; updating gains you nothing
  and costs you nothing.

## [0.3.1] - 2026-07-27

### Added
- Per-channel statistics: import and export are now tracked as separate statistic series.
- The energy price is entered in EUR/kWh instead of ct/kWh.
- The tariff (energy price, base price, monthly advance) can be set during initial setup,
  not only afterwards through options.

### Fixed
- An entry with a tariff already configured under the old ct/kWh option key kept its price
  after upgrading, instead of every cost sensor silently going unknown.
- A metering point that the portal reports under a new id on a later poll now gets entities
  of its own automatically, instead of the existing ones going unknown for good until the
  integration is reloaded.

### Breaking
- Statistic ids changed from `plusportal:<meter>_<kind>` to
  `plusportal:<account>_<meter>_<channel>_<kind>`, splitting import and export into separate
  series. Existing installations get new entities for the same meter; the old, single-series
  ones become orphaned and can be removed with `scripts/ha_prune.py`.
- The energy price option is now read as EUR/kWh. An entry with a price already configured is
  migrated automatically; nothing to do by hand.

## [0.1.0] - 2026-07-26

### Added
- Initial release: read metered electricity consumption from a PlusPortal account and
  project the billing-year cost against a user-configured tariff.
