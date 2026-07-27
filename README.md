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

## Installing it in Home Assistant

Requires Home Assistant **2026.2.0** or newer — the integration uses statistics APIs that
changed in that release.

The interface is available in English and German and follows whatever language Home
Assistant is set to; there is nothing to choose. Field and entity names in this document are
the English ones, so a German instance will show their German equivalents instead
(*Energy price* → *Arbeitspreis*, *Standing charge, accrued* → *Grundpreis kumuliert*).
Entity ids stay English in both, so templates and automations keep working if the language
changes.

### Through HACS

This repository is not in the default HACS catalogue, so it has to be added once as a
custom repository — searching HACS for "plusportal" finds nothing until you do.

1. **HACS** in the sidebar, then the **⋮** menu at the top right.
2. **Custom repositories**.
3. Repository: `https://github.com/pklinger/ha-plusportal`, type **Integration**. **Add**.
4. **PlusPortal** now appears in the list. Open it and choose **Download**.
5. Restart Home Assistant.
5. **Settings → Devices & Services → Add Integration → PlusPortal**, then enter:

   | Field | Where to find it |
   |---|---|
   | Tenant number | the six digits at the start of your portal address |
   | Username | your portal login — often the customer number, not an e-mail |
   | Password | your portal password |

Home Assistant installs the `pyplusportal` library from PyPI on first setup. If that fails,
the entry will report "not ready" — check the log rather than retrying.

### Without HACS

Copy `custom_components/plusportal/` into your Home Assistant `config/custom_components/`
directory and restart. Nothing else differs; HACS only automates the copying and the
update notification.

### Tariff, and what appears

Setup asks for the tariff in a second, optional step. Leave it empty to track consumption
only; you can fill it in later under **Settings → Devices & Services → PlusPortal →
Configure**, and change it there whenever your contract does.

| Option | Meaning |
|---|---|
| Energy price (ct/kWh) | required for any cost figure |
| Standing charge (EUR/year) | apportioned across the billing year |
| Monthly advance payment (EUR) | needed for the settlement forecast |
| Billing year starts (MM-DD) | defaults to 01-01 |
| Update interval (hours) | defaults to 6; the portal publishes once a day |

Per metering point you get consumption for the last day, the current and previous month,
when the portal last had a value, and what share of the data is final. With a tariff, also
the accrued energy cost and standing charge, the forecast for the billing year and the
expected settlement.

### The Energy dashboard

Consumption is written to long-term statistics rather than exposed as a
`total_increasing` sensor, because portal values arrive backdated by a day or more — a
state-based sensor would book all of it at the moment of import.

Under **Settings → Dashboards → Energy → Grid consumption**, pick the statistic named after
your meter with `plusportal` underneath it (a chart icon, not a lightning bolt). For cost,
choose *Use an entity with the current price* and select **Energy price**.

Quarter-hourly readings are aggregated into hourly statistics. Provisional values are left
out and substitute values are included, which is the rule the supplier bills by, and each
refresh re-reads a rolling three-week window so corrections land.

## Using the client on its own

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

## Legal

Unofficial and independent, as stated at the top. It uses your own credentials against your
own account, over the same API the official web interface uses, and stores nothing outside
your Home Assistant instance. No trademark, logo or branding of Thüga SmartService GmbH or
any utility is used or distributed with this project. Use at your own risk.

## License

MIT
