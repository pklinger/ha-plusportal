# PP-HA — Home Assistant integration

### PP-HA-001 — Consumption reaches the Energy dashboard as external statistics
Not through a `total_increasing` sensor.

*Why:* portal values arrive backdated by a day or more. A state-based sensor would book all
of it at the moment of import, so the dashboard would spike whenever polling happened.

### PP-HA-002 — Quarter-hourly readings are summed into hourly buckets
Keyed by the start of the hour.

*Why:* Home Assistant's long-term statistics are hourly. Bucket keys that are not
hour-aligned are rejected or misfiled.

### PP-HA-003 — The running sum continues from the statistic *preceding* a re-import
Not from the newest row, and not from zero.

*Why:* the correction window overlaps stored statistics. The newest row lies inside the
window and would be counted twice; starting at zero would discard all history before it.
Both produce a plausible-looking, wrong graph.

### PP-HA-004 — Corrections replace rather than duplicate
Re-importing a changed value for an hour already stored updates it.

*Why:* provisional values become final ones. Without replacement the dashboard would
accumulate both.

### PP-HA-005 — Each refresh re-reads a rolling correction window
Three weeks back, after a full backfill on the first refresh.

*Why:* it is how corrections are picked up at all. A full re-import every time would be
wasteful; only new data would miss the corrections.

### PP-HA-006 — Provisional values are not written to statistics
Only billable readings are imported.

*Why:* same as PP-COST-001, and here the wrong number would also drive cost display.

### PP-HA-007 — A missing recorder is not an error
Statistics are skipped with a log line; the sensors still work.

*Why:* the recorder can be disabled in Home Assistant. Crashing would take down consumption
tracking with it.

### PP-HA-008 — Absent data leaves sensors unknown, never zero
`native_value` returns `None`.

*Why:* 0 kWh is indistinguishable from a real reading of no consumption.

### PP-HA-009 — Setup is refused for an account with no metering points
The config flow shows an error instead of creating an entry.

*Why:* the integration would install with no entities and no explanation.

### PP-HA-010 — Entities carry stable unique ids and one device per meter
Keyed on the config entry's unique id and the metering point id.

*Why:* without them, renaming a meter orphans its history.

### PP-HA-011 — Cost entities exist only when a tariff is configured
No tariff, no cost sensors.

*Why:* they would display zeros indistinguishable from a free supply.

### PP-HA-012 — A rejected password starts a reauth flow, not a new entry
The existing entry is updated in place.

*Why:* a second entry would duplicate every entity and split the history.

### PP-HA-013 — Diagnostics never contain credentials
Password and username are redacted.

*Why:* users paste diagnostics into public bug reports.

### PP-HA-014 — Cost is broken down, not just totalled
The Arbeitspreis component and the accrued Grundpreis are reported as their own
sensors, and their sum equals the reported total.

*Why:* a single blended figure cannot be checked against anything. Seeing the standing
charge separately is what makes the total explicable — and it is the part that accrues
whether or not any energy is used.

### PP-HA-015 — The energy price is exposed as an entity
In EUR/kWh, present only when a tariff is configured.

*Why:* the Energy dashboard can attach a price entity to a consumption source. Without
one, users have to retype the price they already entered.

### PP-HA-016 — The settlement figure carries what it is measured against
Advances paid so far, advances due for the year, the billing year's bounds and how much of
it is backed by data, as attributes.

*Why:* "you will get 475 EUR back" is not actionable without knowing it assumes twelve
payments and rests on five weeks of data.

### PP-HA-017 — Statistic ids are scoped to the account
`plusportal:<account>_<meter point>_<series>`, not the meter point alone.

*Why:* a meter point id is only unique inside one portal account. Two configured accounts
can both have meter point 5821, and a shared id would sum two households into one series —
visible only as a graph that looks too high.

### PP-HA-018 — The HTTP client comes from Home Assistant
`get_async_client(hass)`, not one constructed by the integration.

It is built with an explicit timeout and is never closed by the integration.

*Why:* three separate traps, all found by running against a real instance rather than by
any unit test. Creating an `httpx.AsyncClient` loads the CA bundle from disk, which stalls
the event loop. Home Assistant sets no timeout, leaving httpx's default of five seconds —
less than a month of quarter-hourly data takes to arrive, so the initial backfill fails and
the entry never becomes ready. And closing a client Home Assistant created is itself
flagged as a bug; it closes them on shutdown.
