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

### PP-HA-019 — Diagnostics carry the cost breakdown
The split between energy and standing charge, the projection, the advances and how much of
the billing year is backed by data — as strings, so the exact `Decimal` survives JSON.
`None` when no tariff is configured.

*Why:* a report that the bill looks wrong is unanswerable without the figures behind it,
and zeros would be indistinguishable from an unpriced supply.

### PP-HA-020 — A name says what it measures and whether it has happened yet
Consumption sensors say "consumption". Cost sensors say either "accrued" or "forecast". The
configured standing charge is shown per year. The exact period is carried as attributes and
as two date sensors, not in the name.

*Why:* three rounds of getting this wrong. "Aktueller Monat" left the quantity to the unit.
"Kosten im Abrechnungsjahr" read as the year's total when it was the cost so far. "bisher"
did not say since when. And spelling the period out in full — "Erwartete Abrechnung für das
gesamte Abrechnungsjahr" — was truncated by Home Assistant to "…für das gesamte…", losing
exactly the qualifying part.

So the name carries only what cannot be moved elsewhere: the quantity, and whether the
figure has accrued or is an extrapolation. Everything else is a click away and legible.
Names stay under about 26 characters, which is where truncation starts.

### PP-HA-021 — Entity ids are English, independent of the interface language
Derived from the description key, not from the translated name.

*Why:* Home Assistant builds an entity id from the name in whatever language the instance
runs, so a German one produced
`sensor.…_energiekosten_seit_beginn_des_abrechnungsjahres`. Ids are referenced from
templates, automations and dashboards; they must not change with the interface language, and
they must stay typeable. Fixing the id also freed the display name to be as precise as it
needs to be, which is what PP-HA-020 asks for.

### PP-HA-022 — The billing year is shown as two dates
`Billing year from` and `Billing year to`, as date sensors, present only when a tariff is
configured.

*Why:* every cost figure is measured against this period, and it is otherwise only visible
by opening a sensor's attributes one at a time. As dates, Home Assistant formats them in the
user's locale.

### PP-HA-023 — The tariff is offered during setup
A second, entirely optional step after the credentials, writing to the same options the
configure dialog edits and validated by the same rules.

*Why:* prices were only reachable after setup, through a dialog people had to know existed.
Asking once, while the user is already there, is the difference between cost figures
appearing and a tariff being entered and then apparently ignored.

### PP-HA-024 — The first setup step says the tariff is optional and can wait
Before any prices are asked for.

*Why:* half the entities depend on a tariff. Someone who finishes setup without one sees
consumption and nothing to suggest that cost exists, that it is optional, or that it can be
added later — so the absence reads as the integration being incomplete.

### PP-HA-025 — Statistics are named by what they are, not by their meter
`<meter> grid consumption` and `<meter> grid cost`, using the Energy dashboard's own term.

*Why:* the dashboard's source picker lists statistics by name among every entity in the
system. `<meter> energy` sorts under the meter number, far from anything related, and says
nothing in a non-English interface — the statistic was there and still could not be found.

### PP-HA-026 — Each OBIS channel gets its own statistic series
`plusportal:<account>_<meter>_<channel>_<series>`, with `import` and `export` as readable
slugs. Only the billed import channel is priced, and only it feeds the sensors.

*Why:* a meter with feed-in reports two channels. Merging them makes the Energy dashboard
show a grid draw that includes energy the household exported — silently, as a number that
merely looks high. Pricing an export series at the import rate would compound it. The
sensors and every cost figure describe what was drawn from the grid, so they read the
billed channel alone.

### PP-HA-027 — The energy price is entered in EUR/kWh
Not in cents, with free-form decimals.

*Why:* bills quote cents, but Home Assistant works in euro everywhere else and the price
entity already reports EUR/kWh. Asking for cents made the input the only place in the
integration using a different unit, and the conversion an invisible step for anyone checking
a figure by hand. The CLI's `PLUSPORTAL_ENERGY_PRICE_CT` keeps its unit: it is a separate
surface with its own documented name, and renaming it would break anyone's `.env`.
