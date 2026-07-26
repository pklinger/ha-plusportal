# ha-plusportal — Home Assistant integration for PlusPortal

> **Unofficial.** This is an independent, community-built project. It is not affiliated
> with, endorsed by, or supported by **Thüga SmartService GmbH** — who develop and operate
> PlusPortal — nor by any utility running a PlusPortal instance. "PlusPortal" is their
> product name and is used here only to describe what this software connects to. For
> problems with this integration, open an issue here; do not contact your utility.

Read your **metered** electricity consumption out of a PlusPortal energy customer portal
and get it into Home Assistant — including a cent-accurate bill projection.

PlusPortal is a white-label customer portal operated by many German utilities, each under
its own subdomain of the form `https://<tenant>.plusportal.de`. This project uses your own
account and your own data, through the same API the official web interface uses, so it
works for **any** tenant.

The repository contains two independent layers:

| Layer | Path | Depends on Home Assistant? | Status |
|---|---|---|---|
| `pyplusportal` — async API client + CLI | `src/pyplusportal/` | no | implemented |
| `plusportal` — HACS custom integration | `custom_components/plusportal/` | yes | implemented |

The client is deliberately usable and testable on its own, without Home Assistant.

## Quick start (client only)

```bash
uv sync
cp .env.example .env      # fill in tenant, username, password
uv run pyplusportal meters
```

`pyplusportal` is a single CLI with five sub-commands:

| Command | Purpose |
|---|---|
| `meters` | list metering points and their channels |
| `overview` | show the portal's dashboard aggregates |
| `readings` | fetch daily consumption values |
| `cost` | project this billing year's cost and settlement |
| `probe` | record redacted responses as test fixtures |

Tenant, username and password are picked up from `.env` (or the real environment), so
once `.env` is set up the commands need no other flags:

```bash
uv run pyplusportal readings --from 2026-06-18 --to 2026-07-24 --meter 1000 --format csv
```

`cost` additionally needs your tariff, since the portal itself exposes no price data:
`PLUSPORTAL_ENERGY_PRICE_CT` (required), plus `PLUSPORTAL_BASE_PRICE_EUR`,
`PLUSPORTAL_MONTHLY_ADVANCE_EUR` and `PLUSPORTAL_BILLING_YEAR_START` (optional, see
`.env.example`):

```bash
PLUSPORTAL_ENERGY_PRICE_CT=32.5 uv run pyplusportal cost
```

Run `uv run pyplusportal help` for the full option and environment-variable reference,
or `uv run pyplusportal help <command>` for one command's own options.

### Verifying against your own portal

The test suite runs offline against recorded fixtures. A second, opt-in suite talks to a
real portal and reconciles what this client computes against what the portal itself
reports — the quarter-hourly series, the daily series and the portal's month total must
agree to the last digit:

```bash
uv run pytest -m live          # skips cleanly unless .env is filled in
```

It reads tenant, username and password from the environment only, so no account detail
ever ends up in the repository.

### Running the integration in a real Home Assistant

Neither test suite catches what only a running instance shows — a blocking call in the
event loop, a timeout that is too short for the first backfill, an attribute Home Assistant
cannot serialise. All three of those were real, and all three were found this way.

```bash
scripts/ha-dev.sh up        # starts Home Assistant on :8124 with this integration
scripts/ha-dev.sh state     # what the entities report
scripts/ha-dev.sh stats     # what reached the Energy dashboard
scripts/ha-dev.sh logs      # follow the integration
scripts/ha-dev.sh sync      # after a change: rebuild, reinstall, restart
scripts/ha-dev.sh reset     # throw the instance away
```

The integration is bind-mounted, so editing and restarting is the whole loop. The library
is installed into the container from a locally built wheel, which is what lets this work
before `pyplusportal` exists on PyPI — Home Assistant skips the pin in `manifest.json` once
the requirement is already satisfied.

On first start, open <http://localhost:8124>, create a throwaway owner account and add the
**PlusPortal** integration with the same values as your `.env`. Home Assistant's state
lives in `.dev/`, which is gitignored: once configured it holds your portal password.

## Home Assistant

Install via HACS as a custom repository, then add the **PlusPortal** integration and enter
your tenant number, username and password. The tariff is optional and can be set — or
changed — at any time under the integration's options.

Quarter-hourly meter readings are aggregated into Home Assistant's hourly long-term
statistics, so consumption appears in the Energy dashboard under the timestamps it was
actually measured at, not backdated to the moment of import. Add
`plusportal:<meter id>_energy` as a consumption source there; with a tariff configured,
`plusportal:<meter id>_cost` gives you euros alongside it.

Per metering point you also get sensors for the last day, the current and previous month,
when the portal last had a value, what share of the data is final, and — with a tariff —
cost so far, projected annual cost and the expected settlement.

Metered values arrive provisional and are corrected days later, so every refresh re-reads
a rolling three-week window and lets the corrections replace what was stored.

## Legal

Unofficial and independent, as stated at the top. It uses your own credentials against your
own account, over the same API the official web interface uses, and stores nothing outside
your Home Assistant instance. No trademark, logo or branding of Thüga SmartService GmbH or
any utility is used or distributed with this project. Use at your own risk.

## License

MIT
