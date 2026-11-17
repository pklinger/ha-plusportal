# Feed-in Compensation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user enter a feed-in price, and turn metered feed-in into a compensation
statistic for the Energy dashboard plus four entities.

**Architecture:** One new option, read by one function in `tariff.py` and exposed as a
coordinator property next to `tariff`. `statistics.py` prices the export channel with it,
`sensor.py` adds a fourth conditional sensor group. Nothing in `src/pyplusportal/` changes
except the version; the integration reuses `pyplusportal.cost.money` for rounding.

**Tech Stack:** Home Assistant 2026.2 custom integration, `Decimal`, pytest with
`pytest-homeassistant-custom-component`.

**Spec:** `docs/superpowers/specs/2026-11-15-feed-in-compensation-design.md`

## Global Constraints

- Option key `feed_in_price_eur_per_kwh`. Empty means no price; `0` is a price.
- Statistic id `plusportal:<account>_<meter>_export_compensation`, name
  `<meter> feed-in compensation`, unit `EUR`, billable readings only.
- Entity keys `feed_in_price`, `feed_in_compensation_last_day`,
  `feed_in_compensation_this_month`, `feed_in_compensation_previous_month`.
- Entity names at most 26 characters (PP-HA-020), in `strings.json`, `en.json` and `de.json`
  alike (PP-SEC-006).
- Compensation never enters cost, projection or settlement.
- `Decimal(str(value))` for anything read from options; money rounds half-up at the cent.
- `uv run --group ha …` for everything under `custom_components/` and `tests_ha/`.
- Every new requirement id is declared in `docs/specs/integration.md` in the same task that
  first cites it, or `tests/test_traceability.py` fails.
- Version 0.5.0 in `pyproject.toml`, the manifest `version` and the manifest requirements
  pin. No commit without the user's go-ahead.

## Review Focus

1. A stored price that is not a usable number (`"abc"`, `-0.01`, `"nan"`, `"inf"`, a list)
   after a hand-edited `.storage`: setup succeeds and there is simply no compensation.
2. A price of `0`: it is a price, so the entities exist and report `0.00`, not unknown.
3. A price set on a meter with no export channel: no compensation entity, no statistic.
4. A feed-in hour holding only provisional readings: no statistic row, not a zero row.
5. An export channel the portal's month tile omits: the two month amounts are unknown, not
   `0.00`, while the last-day amount still reports.

Each has its test in the task that owns the code, below.

---

### Task 1: The option and how it is read

**Files:**
- Modify: `custom_components/plusportal/const.py`, `tariff.py`, `config_flow.py`,
  `strings.json`, `translations/en.json`, `translations/de.json`
- Modify: `docs/specs/integration.md` (add PP-HA-034)
- Test: `tests_ha/test_tariff.py`, `tests_ha/test_config_flow.py`

**Interfaces:**
- Produces: `CONF_FEED_IN_PRICE: Final = "feed_in_price_eur_per_kwh"` in `const.py`;
  `feed_in_price_from_options(options: Mapping[str, Any]) -> Decimal | None` in `tariff.py`.

- [ ] **Step 1: Declare PP-HA-034** — "The feed-in price is a setting of its own,
  independent of the energy price", with the reason from the design doc.
- [ ] **Step 2: Write the failing tests**

```python
# tests_ha/test_tariff.py
def test_no_feed_in_price_means_no_compensation() -> None:
    assert feed_in_price_from_options({}) is None
    assert feed_in_price_from_options({CONF_ENERGY_PRICE: 0.345}) is None

def test_the_feed_in_price_does_not_need_an_energy_price() -> None:
    options = {CONF_FEED_IN_PRICE: 0.0811}
    assert feed_in_price_from_options(options) == Decimal("0.0811")
    assert tariff_from_options(options) is None

def test_a_feed_in_price_of_zero_is_a_price() -> None:
    assert feed_in_price_from_options({CONF_FEED_IN_PRICE: 0}) == Decimal(0)

@pytest.mark.parametrize("value", ["not a number", "", -0.01, "nan", "inf", [0.08]])
def test_an_unusable_feed_in_price_yields_none_rather_than_raising(value) -> None:
    assert feed_in_price_from_options({CONF_FEED_IN_PRICE: value}) is None

# tests_ha/test_config_flow.py
async def test_the_feed_in_price_can_be_set_without_a_tariff(hass, config_entry) -> None:
    # options flow, input {CONF_FEED_IN_PRICE: 0.0811}
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_FEED_IN_PRICE] == 0.0811
    assert CONF_ENERGY_PRICE not in config_entry.options

async def test_setup_offers_the_feed_in_price_too(hass, client_ok) -> None:
    # tariff step, input {CONF_FEED_IN_PRICE: 0.0811}
    assert result["options"][CONF_FEED_IN_PRICE] == 0.0811

def test_a_negative_feed_in_price_is_rejected() -> None:
    with pytest.raises(ValueError, match=CONF_FEED_IN_PRICE):
        _validate_tariff({CONF_FEED_IN_PRICE: -0.01})
```

- [ ] **Step 3: Run them, expect ImportError on `CONF_FEED_IN_PRICE`**
  `uv run --group ha pytest -c pytest-ha.ini tests_ha/test_tariff.py tests_ha/test_config_flow.py`
- [ ] **Step 4: Implement.** `feed_in_price_from_options` returns `None` for an absent,
  non-finite, negative or unparseable value. The form field sits after the billing year
  start, uses `_price_selector(5.0, step="any")`, and `_validate_tariff` raises
  `ValueError(CONF_FEED_IN_PRICE)` before the early return for a missing energy price. Add
  the field label, a `data_description` and the error `invalid_feed_in_price_eur_per_kwh` to
  all three string files, under `config` and `options`.
- [ ] **Step 5: Run both suites' affected files, expect PASS** (the library suite holds the
  translation parity and traceability tests: `uv run pytest tests/test_packaging.py tests/test_traceability.py`).

### Task 2: The compensation statistic

**Files:**
- Modify: `custom_components/plusportal/const.py`, `statistics.py`, `coordinator.py`
- Modify: `docs/specs/integration.md` (add PP-HA-035, PP-HA-037; amend PP-HA-025, PP-HA-026)
- Test: `tests_ha/test_statistics.py`, `tests_ha/test_sensor.py`

**Interfaces:**
- Consumes: `feed_in_price_from_options` from Task 1.
- Produces: `STATISTIC_COMPENSATION: Final = "compensation"`;
  `async_publish_statistics(hass, entry, data, tariff, feed_in_price: Decimal | None = None)`;
  `PlusPortalCoordinator.feed_in_price -> Decimal | None`.

- [ ] **Step 1: Declare PP-HA-035** (compensation reaches the dashboard as its own
  statistic) **and PP-HA-037** (compensation is never netted against cost); amend PP-HA-025
  and PP-HA-026 so they no longer say only the import channel is priced.
- [ ] **Step 2: Write the failing tests** in `tests_ha/test_statistics.py`, with
  `COMPENSATION_ID = "plusportal:123456_10001_1000_export_compensation"`:

```python
async def test_no_compensation_statistics_without_a_feed_in_price(...):
    # export 4 x 1.00 kWh, no price
    assert await read_sums(hass, COMPENSATION_ID) == []

async def test_compensation_statistics_pay_each_hour_fed_in(...):
    # export 8 x 1.00 kWh, price Decimal("0.0811")
    assert [round(v, 4) for _, v in sums] == [0.3244, 0.6488]

async def test_each_direction_is_priced_at_its_own_rate_only(...):
    # import 4 x 0.25 at 34.5 ct, export 4 x 1.00 at 0.0811
    assert cost == [0.345] and compensation == [0.3244]
    assert await read_sums(hass, "plusportal:123456_10001_1000_import_compensation") == []
    assert await read_sums(hass, "plusportal:123456_10001_1000_export_cost") == []

async def test_provisional_feed_in_is_not_compensated(...):
    # export 4 x 1.00 kWh, all ValueState.PRELIMINARY, price set
    assert await read_sums(hass, COMPENSATION_ID) == []

async def test_the_compensation_series_is_named_and_in_euro(...):
    assert name == "1ABC0000000000* feed-in compensation" and unit == "EUR"
```

  and in `tests_ha/test_sensor.py`, patching
  `custom_components.plusportal.coordinator.async_publish_statistics`:

```python
async def test_the_configured_feed_in_price_reaches_the_statistics(hass, feed_in_entry, feeding_portal):
    assert publish.await_args.args[4] == Decimal("0.0811")
```

  `feed_in_entry` is a `MockConfigEntry` like `config_entry` with
  `options={CONF_FEED_IN_PRICE: 0.0811}`.
- [ ] **Step 3: Run, expect TypeError (unexpected argument) and empty series.**
- [ ] **Step 4: Implement.** Only the `export` channel is compensated, per hour as
  `value * feed_in_price`, the same shape as the cost series.
- [ ] **Step 5: Run `tests_ha/test_statistics.py tests_ha/test_sensor.py`, expect PASS.**

### Task 3: The entities

**Files:**
- Modify: `custom_components/plusportal/sensor.py`, the three string files
- Modify: `docs/specs/integration.md` (add PP-HA-036)
- Test: `tests_ha/test_sensor.py`

**Interfaces:**
- Consumes: `PlusPortalCoordinator.feed_in_price` from Task 2.
- Produces: `FEED_IN_COMPENSATION_SENSORS`, and two description fields
  `feed_in_value_fn: Callable[[MeterData, Decimal], Any] | None` and
  `feed_in_attributes_fn: Callable[[MeterData, Decimal], Mapping[str, Any]] | None`.

- [ ] **Step 1: Declare PP-HA-036** — compensation entities exist only for a meter that
  feeds in and has a price, and each amount is its energy neighbour times the price.
- [ ] **Step 2: Write the failing tests**, entity ids prefixed `sensor.1abc0000000000_`:

```python
async def test_the_feed_in_price_is_exposed_as_an_entity(hass, feed_in_entry, feeding_portal):
    assert state.state == "0.0811" and unit == "EUR/kWh"

async def test_compensation_is_the_energy_neighbour_times_the_price(...):
    # 4.80 kWh, 412.5 kWh, 389.25 kWh at 0.0811
    assert last_day.state == "0.39" and last_day.attributes["date"] == "2026-07-20"
    assert this_month.state == "33.45" and previous_month.state == "31.57"

async def test_compensation_sensors_are_money(...):
    assert device_class == "monetary" and unit == "EUR"

async def test_no_compensation_sensors_without_a_feed_in_price(hass, config_entry, feeding_portal): ...
async def test_no_compensation_sensors_for_a_meter_that_does_not_feed_in(hass, feed_in_entry, portal): ...

async def test_a_feed_in_price_of_zero_still_reports(...):      # options {CONF_FEED_IN_PRICE: 0}
    assert this_month.state == "0.00"

async def test_month_compensation_is_unknown_without_a_month_sum(...):
    # get_overview returns only the import overview
    assert this_month.state == STATE_UNKNOWN and last_day.state == "0.39"

async def test_a_feed_in_price_leaves_every_cost_figure_alone(...):   # PP-HA-037
    # tariff 0.35 / 12 / 80 plus feed-in price; expected = project_billing_year(import readings)
    assert cost_to_date == expected.observed.total_eur
    assert settlement == expected.settlement_eur

async def test_compensation_sensor_names_stay_short_enough_for_the_device_card(...):
    assert len(name) <= 26
```

- [ ] **Step 3: Run, expect the entities to be missing.**
- [ ] **Step 4: Implement.** Amounts go through `pyplusportal.cost.money`; the group is
  added only when `has_feed_in` and `coordinator.feed_in_price is not None`.
- [ ] **Step 5: Mutation check for the test that passes on arrival.**
  `test_a_feed_in_price_leaves_every_cost_figure_alone` cannot fail first. Temporarily feed
  `entry.readings + entry.feed_in_readings` into `project_billing_year` in `coordinator.py`,
  confirm the test fails, revert.
- [ ] **Step 6: Run `tests_ha/test_sensor.py`, expect PASS.**

### Task 4: Documentation, version, gates

**Files:**
- Modify: `README.md` (option table, entity paragraph, both Energy dashboard sentences),
  `CHANGELOG.md` (`## [0.5.0] - 2026-11-15`), `pyproject.toml`,
  `custom_components/plusportal/manifest.json` (version and pin), `uv.lock`

- [ ] **Step 1:** README: for grid consumption the cost option is the `<meter> grid cost`
  statistic; for return to grid it is `<meter> feed-in compensation`. Say why a static price
  or a price entity cannot work on an external statistic.
- [ ] **Step 2:** Changelog entry with `### Added` and `### Fixed` (the README correction).
- [ ] **Step 3:** Version 0.5.0 in all three places; `uv lock` to carry it into `uv.lock`.
- [ ] **Step 4:** Run every gate from `CLAUDE.md` and
  `uv run python scripts/check_version_bump.py origin/main`. All must pass.
