# PP-EXT — Extraction

Getting consumption out of a PlusPortal instance and into trustworthy Python objects.

### PP-EXT-001 — A tenant number, hostname or URL all resolve to one HTTPS base URL

`resolve_base_url` accepts `123456`, `123456.plusportal.de` or a full URL, and always
returns `https://…`. Anything else raises.

*Why:* credentials go over this connection. Silently honouring `http://` would send them in
the clear, so plain HTTP is upgraded rather than accepted.

### PP-EXT-002 — The backend lives under `/msw/api`

Requests go to `/msw/api/…`, not `/api/…`.

*Why:* `/api/` returns the single-page app's HTML shell with status 200. Parsed as JSON it
looks like a transport error; parsed leniently it would look like no data.

### PP-EXT-003 — The finest series is `power`, in kW, and is converted to energy

Quarter-hourly data comes from `diagramType=power` as average power. `Reading.value` is
always energy in kWh: `kW × interval length in hours`.

*Why:* the `consumption` series only resolves to whole days. Summing kW values as if they
were kWh overstates consumption fourfold.

### PP-EXT-004 — Interval timestamps mark the end of their interval

A reading's `start` is the portal's timestamp minus the interval length; `end` is the
timestamp itself.

*Why:* requesting a single day returns values from `00:15` through `00:00` the next day.
Treating those as start times shifts the entire load profile by a quarter hour, which no
total would ever reveal.

### PP-EXT-005 — The interval length is derived from the data

Spacing is read off consecutive timestamps, falling back to 15 minutes only when a single
value makes that impossible.

*Why:* electricity in Germany is metered quarter-hourly, but gas and older meters are not.
A hard-coded 15 minutes would silently misconvert them.

### PP-EXT-006 — Daily readings are labelled by the start of their day

The `consumption` series carries energy already, timestamped at local midnight.

*Why:* the two series disagree about what a timestamp means. Normalising both to `start`
keeps that difference from leaking to callers.

### PP-EXT-007 — Requests always use `period=month`

Regardless of the range asked for.

*Why:* it is the only period whose timestamps come back on local midnight. `day`, `week`
and `year` return UTC midnight — an hour or two off in Germany, twice over across a DST
boundary. `period` does not limit the range, so nothing is lost.

### PP-EXT-008 — Every reading carries the operator's quality flag

`W` (final), `E` (substitute) and `V` (provisional) are preserved; `billable` is true for
`W` and `E`. An unrecognised flag becomes `UNKNOWN` and is not billable.

*Why:* only `W` and `E` reach an invoice. Treating an unknown future flag as billable would
guess in the direction that costs the user money.

### PP-EXT-009 — Numbers are decoded as `Decimal`

JSON is parsed with `parse_float=Decimal` and stays exact through the client.

*Why:* a year is 35 000 quarter-hourly values. Binary floating point drifts enough over
that to move the last cents of an annual bill.

### PP-EXT-010 — Timestamps are timezone-aware, in the portal's zone

Every datetime the client returns is aware and expressed in `Europe/Berlin`.

*Why:* naive datetimes would silently adopt the machine's zone. A Home Assistant instance
in another timezone would file consumption under the wrong hour.

### PP-EXT-011 — Long ranges are split into monthly requests

`get_interval_readings` and `get_daily_readings` chunk internally and reassemble.

*Why:* a single call can span months, but a year of quarter-hourly data is ~35 000 values
in one response. Chunking bounds the response size; it is not needed for correctness.

### PP-EXT-012 — A rejected session triggers exactly one re-login and retry

401 and 403 cause one fresh login and one retry. A second rejection raises
`AuthenticationError`.

*Why:* sessions expire after about an hour, so a long-lived client must recover. Retrying
without a limit would hammer someone else's login endpoint with bad credentials.

### PP-EXT-013 — Server errors are retried with backoff, then surface as unavailable

5xx and network failures are retried with exponential backoff before raising
`PortalUnavailableError`.

*Why:* the portal proxies to an upstream that returns intermittent 500s. A transient blip
should not fail a scheduled refresh.

### PP-EXT-014 — A non-JSON body is an outage, not empty data

A 200 response that does not parse as JSON raises `PortalUnavailableError`.

*Why:* a reverse proxy in front of the portal serves HTML error pages with status 200.
Treating that as "no readings" would silently zero out a day's consumption.

### PP-EXT-015 — An account without the energy feature is rejected at login

Login fails if the session reports no `energydataview` feature.

*Why:* every later call would return empty. Failing at login says why.

### PP-EXT-016 — The highest-resolution tariff use case is preferred

Among active TAFs, the one recording a meter reading series wins over a data-minimising one.

*Why:* a meter often has several. Picking arbitrarily would give some users quarter-hourly
data and others only daily, with no way to tell why.

### PP-EXT-017 — Malformed payloads fail loudly, naming the field

Missing required fields raise `ParseError` carrying the field name.

*Why:* the portal returns `null` in places its own schema says it will not. Guessing a
default would put a wrong number in the Energy dashboard; the field name makes a bug report
actionable without pasting a response full of account data.
