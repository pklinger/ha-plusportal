"""Command line interface for reading a PlusPortal account.

Exists so the extraction layer can be exercised, debugged and verified on its
own — no Home Assistant, no config entries, just credentials and a terminal.

Credentials are read from the environment (optionally via a ``.env`` file) and
never accepted as arguments: command lines end up in shell history, process
listings and CI logs.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import difflib
import json
import os
import re
import sys
import textwrap
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, NoReturn, TextIO

from .client import PlusPortalClient
from .const import PATH_OVERVIEW, PATH_SESSION, PATH_USER_ITEM_LIST, PORTAL_TZ
from .cost import Projection, Tariff, billing_year_bounds, project_billing_year
from .exceptions import AuthenticationError, PlusPortalError, PortalUnavailableError
from .json import json_dumps
from .models import MeterPoint, Overview, Reading

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3

ENV_TENANT = "PLUSPORTAL_TENANT"
ENV_BASE_URL = "PLUSPORTAL_BASE_URL"
ENV_USERNAME = "PLUSPORTAL_USERNAME"
ENV_PASSWORD = "PLUSPORTAL_PASSWORD"
ENV_ENERGY_PRICE = "PLUSPORTAL_ENERGY_PRICE_CT"
ENV_BASE_PRICE = "PLUSPORTAL_BASE_PRICE_EUR"
ENV_ADVANCE = "PLUSPORTAL_MONTHLY_ADVANCE_EUR"
ENV_BILLING_YEAR_START = "PLUSPORTAL_BILLING_YEAR_START"
ENV_NO_DOTENV = "PLUSPORTAL_NO_DOTENV"

#: Keys whose values identify a person, a contract or a meter.
_SENSITIVE_KEYS = frozenset(
    {
        "sessionid",
        "username",
        "firstname",
        "surname",
        "bez1",
        "bez2",
        "custno",
        "kundnr",
        "zpbez",
        "malo",
        "meterid",
        "deveui",
    }
)

#: Values that name a physical meter, wherever they appear.
_METER_NUMBER_RE = re.compile(r"^\d?[A-Z]{3}\d{10}\*?$")

_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


# --------------------------------------------------------------------- env


def parse_env_file(path: Path) -> dict[str, str]:
    """Read a ``.env`` file into a mapping, ignoring comments and junk lines."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _ENV_LINE_RE.match(line)
        if match is None:
            continue
        key, raw = match.groups()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        values[key] = raw
    return values


def load_environment(cwd: Path | None = None) -> dict[str, str]:
    """Merge ``.env`` with the real environment; the environment wins."""
    if os.environ.get(ENV_NO_DOTENV):
        return dict(os.environ)
    env = parse_env_file((cwd or Path.cwd()) / ".env")
    env.update(os.environ)
    return env


def _cli_version() -> str:
    """Return the installed package version, or "unknown" outside an install."""
    try:
        return version("pyplusportal")
    except PackageNotFoundError:
        return "unknown"


# --------------------------------------------------------------- redaction


def redact(payload: Any) -> Any:
    """Return a copy of a portal response with identifying values replaced.

    Used by ``probe`` so recorded responses can be committed as fixtures
    without leaking who the account belongs to or which meter it reads.
    """
    if isinstance(payload, dict):
        return {
            key: "REDACTED"
            if key.lower() in _SENSITIVE_KEYS and isinstance(value, str)
            else redact(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact(item) for item in payload]
    if isinstance(payload, str) and _METER_NUMBER_RE.match(payload):
        return "1ABC0000000000*"
    return payload


# ----------------------------------------------------------------- output


def _print_table(rows: list[list[str]], headers: list[str], stream: TextIO) -> None:
    """Print a left-aligned table wide enough for its widest cell.

    Blank headers mean this is a label/value listing rather than a real table,
    so the header rule is left out instead of printing a bare rule.
    """
    widths = [
        max(len(headers[column]), *(len(row[column]) for row in rows)) if rows else len(header)
        for column, header in enumerate(headers)
    ]
    if any(headers):
        line = "  ".join(header.ljust(widths[i]) for i, header in enumerate(headers))
        print(line.rstrip(), file=stream)
        print("  ".join("-" * width for width in widths), file=stream)
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip(), file=stream)


def _render_meters(points: list[tuple[MeterPoint, list[str]]], stream: TextIO) -> None:
    """Show every metering point with the tariff use case that will be read."""
    rows = [
        [
            str(point.id),
            point.name,
            point.category,
            str(point.primary_taf.number) if point.primary_taf else "-",
            point.primary_taf.label if point.primary_taf else "no active tariff use case",
            ", ".join(obis) or "-",
        ]
        for point, obis in points
    ]
    _print_table(rows, ["id", "meter", "category", "taf", "taf name", "channels"], stream)


def _render_overview(overviews: list[Overview], stream: TextIO) -> None:
    """Show the portal's own dashboard aggregates."""
    rows = [
        [
            str(ov.meter_point_id),
            ov.label,
            ov.obis,
            _fmt(ov.this_month_sum),
            _fmt(ov.prev_month_sum),
            ov.unit,
            ov.first_value_at.date().isoformat(),
            ov.last_value_at.isoformat(timespec="minutes"),
        ]
        for ov in overviews
    ]
    _print_table(
        rows,
        ["id", "channel", "obis", "this month", "prev month", "unit", "data from", "last value"],
        stream,
    )


def _fmt(value: Decimal | None) -> str:
    """Format an optional decimal without ever losing a digit."""
    return "-" if value is None else f"{value:f}"


def _render_readings(readings: list[Reading], stream: TextIO) -> None:
    """Show daily values plus the total that would actually be invoiced."""
    rows = [
        [r.day.isoformat(), f"{r.value:f}", r.unit, r.state.value, "yes" if r.billable else "no"]
        for r in readings
    ]
    _print_table(rows, ["date", "value", "unit", "quality", "billable"], stream)

    billable = sum((r.value for r in readings if r.billable), Decimal(0))
    total = sum((r.value for r in readings), Decimal(0))
    print(f"\n{len(readings)} readings, billable total {billable:f}", file=stream)
    if billable != total:
        print(f"(including non-billable values the total would be {total:f})", file=stream)


def _readings_as_csv(readings: list[Reading], stream: TextIO) -> None:
    """Emit readings as CSV for spreadsheets and further processing."""
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["date", "value", "unit", "obis", "quality", "billable"])
    for r in readings:
        writer.writerow(
            [
                r.day.isoformat(),
                f"{r.value:f}",
                r.unit,
                r.obis,
                r.state.value,
                str(r.billable).lower(),
            ]
        )


def _readings_as_json(readings: list[Reading], stream: TextIO) -> None:
    """Emit readings as JSON, with values as strings to keep every digit."""
    json.dump(
        [
            {
                "date": r.day.isoformat(),
                "start": r.start.isoformat(),
                "value": f"{r.value:f}",
                "unit": r.unit,
                "obis": r.obis,
                "quality": r.state.value,
                "billable": r.billable,
            }
            for r in readings
        ],
        stream,
        indent=2,
    )
    print(file=stream)


def tariff_from_env(env: dict[str, str]) -> Tariff:
    """Build a tariff from the environment, naming anything that is missing."""
    price = env.get(ENV_ENERGY_PRICE)
    if not price:
        raise ValueError(
            f"missing tariff configuration: {ENV_ENERGY_PRICE} "
            f"(optional: {ENV_BASE_PRICE}, {ENV_ADVANCE}, {ENV_BILLING_YEAR_START})"
        )

    advance = env.get(ENV_ADVANCE)
    month, _, day = (env.get(ENV_BILLING_YEAR_START) or "01-01").partition("-")

    try:
        return Tariff(
            energy_price_ct_per_kwh=Decimal(price),
            base_price_eur_per_year=Decimal(env.get(ENV_BASE_PRICE) or "0"),
            monthly_advance_eur=Decimal(advance) if advance else None,
            billing_year_start=(int(month), int(day)),
        )
    except (InvalidOperation, ValueError) as err:
        raise ValueError(f"invalid tariff configuration: {err}") from err


def _render_projection(projection: Projection, stream: TextIO) -> None:
    """Show where the billing year is heading, and what it would settle at."""
    start, end = projection.billing_year
    observed = projection.observed
    rows = [
        ["billing year", f"{start.isoformat()} .. {end.isoformat()}"],
        ["data coverage", f"{projection.coverage * 100:.1f} %"],
        ["", ""],
        ["billable so far", f"{observed.energy_kwh:f} kWh"],
        ["cost so far", f"{observed.total_eur:f} EUR"],
        ["  of which energy", f"{observed.energy_eur:f} EUR"],
        ["  of which standing charge", f"{observed.base_eur:f} EUR"],
        ["", ""],
        ["projected for the year", f"{projection.projected_kwh:.1f} kWh"],
        ["projected cost", f"{projection.projected_eur:f} EUR"],
    ]
    if projection.advances_due_eur is not None and projection.settlement_eur is not None:
        settlement = projection.settlement_eur
        label = "expected additional payment" if settlement >= 0 else "expected refund"
        rows += [
            ["", ""],
            ["advances paid so far", f"{projection.advances_paid_eur:f} EUR"],
            ["advances due for the year", f"{projection.advances_due_eur:f} EUR"],
            [label, f"{abs(settlement):f} EUR"],
        ]

    if observed.excluded_kwh:
        rows += [["", ""], ["not yet billable", f"{observed.excluded_kwh:f} kWh"]]

    _print_table(rows, ["", ""], stream)


# --------------------------------------------------------------- commands


async def _cmd_meters(client: PlusPortalClient, args: argparse.Namespace) -> int:
    """List metering points and the channels available on each."""
    points = await client.get_meter_points()
    rows = [(point, [c.obis for c in await client.get_channels(point)]) for point in points]
    _render_meters(rows, args.stdout)
    return EXIT_OK


async def _cmd_overview(client: PlusPortalClient, args: argparse.Namespace) -> int:
    """Show the portal's dashboard aggregates."""
    _render_overview(await client.get_overview(), args.stdout)
    return EXIT_OK


async def _cmd_readings(client: PlusPortalClient, args: argparse.Namespace) -> int:
    """Fetch daily consumption for the selected metering point."""
    end = args.to or datetime.now(tz=PORTAL_TZ).date()
    start = getattr(args, "from") or end - timedelta(days=30)

    points = await client.get_meter_points()
    if args.meter is not None:
        points = [point for point in points if point.id == args.meter]
        if not points:
            # An empty table would read as "no consumption" rather than
            # "you asked for a meter that does not exist".
            print(f"no metering point with id {args.meter}", file=args.stderr)
            return EXIT_USAGE

    readings: list[Reading] = []
    for point in points:
        for channel in await client.get_channels(point):
            readings.extend(await client.get_daily_readings(channel, start, end))

    renderer = {
        "table": _render_readings,
        "csv": _readings_as_csv,
        "json": _readings_as_json,
    }[args.format]
    renderer(readings, args.stdout)
    return EXIT_OK


async def _cmd_probe(client: PlusPortalClient, args: argparse.Namespace) -> int:
    """Record redacted responses so they can be committed as fixtures."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    endpoints: list[tuple[str, str, dict[str, Any] | None]] = [
        ("session.json", PATH_SESSION, None),
        ("user_item_list.json", PATH_USER_ITEM_LIST, {"page": 0}),
        ("overview.json", PATH_OVERVIEW, None),
    ]

    for name, path, params in endpoints:
        payload = await client.fetch_raw(path, params)
        (out / name).write_text(json_dumps(redact(payload), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out / name}", file=args.stdout)
    return EXIT_OK


async def _cmd_cost(client: PlusPortalClient, args: argparse.Namespace) -> int:
    """Project the billing year from the data recorded so far."""
    tariff = tariff_from_env(args.env)
    today = args.today or datetime.now(tz=PORTAL_TZ).date()
    start, _ = billing_year_bounds(today, tariff)

    readings: list[Reading] = []
    for point in await client.get_meter_points():
        for channel in await client.get_channels(point):
            readings.extend(await client.get_interval_readings(channel, start, today))

    _render_projection(project_billing_year(readings, tariff, today=today), args.stdout)
    return EXIT_OK


# ------------------------------------------------------------------- entry


class _ParserError(Exception):
    """A parser failure, carrying the parser so ``main`` can format it.

    Raised by :class:`_ArgumentParser` instead of letting ``argparse`` print
    the message itself and call ``sys.exit`` — that would make usage errors
    (including the built-in "invalid choice" for an unknown sub-command)
    bypass ``main``'s return value, which the tests rely on.
    """

    def __init__(self, parser: argparse.ArgumentParser, message: str) -> None:
        """Remember which parser failed alongside argparse's message."""
        super().__init__(message)
        self.parser = parser


class _ArgumentParser(argparse.ArgumentParser):
    """An ``ArgumentParser`` that raises on error instead of exiting."""

    def error(self, message: str) -> NoReturn:
        """Raise instead of printing to stderr and calling ``sys.exit``."""
        raise _ParserError(self, message)


#: Global options that take a value, so the command scan below knows to
#: skip the value along with the option itself instead of mistaking it for
#: the sub-command.
_GLOBAL_VALUE_OPTS = {"--tenant", "--base-url"}


def _command_position(tokens: Sequence[str]) -> int | None:
    """Return the index of the first token that is the sub-command.

    Skips global options and their values (both ``--tenant 123456`` and
    ``--tenant=123456``) so a sub-command after a global option, such as in
    every example in the CLI's own epilog, is still found.
    """
    it = enumerate(tokens)
    for index, token in it:
        if token in _GLOBAL_VALUE_OPTS:
            next(it, None)
        elif not token.startswith("-"):
            return index
    return None


def _command_token(tokens: Sequence[str]) -> str | None:
    """Return the first token that is the sub-command, skipping global options."""
    index = _command_position(tokens)
    return None if index is None else tokens[index]


def _unknown_command_message(token: str, commands: Sequence[str]) -> str:
    """Describe an unrecognized command, suggesting the closest match if any."""
    matches = difflib.get_close_matches(token, commands)
    if matches:
        suggestions = " or ".join(repr(match) for match in matches)
        return f"unknown command {token!r} (did you mean {suggestions}?)"
    return f"unknown command {token!r}; valid commands: {', '.join(commands)}"


def _fail(parser: argparse.ArgumentParser, message: str, stream: TextIO) -> int:
    """Print a usage error the way ``argparse`` would and return its exit code."""
    parser.print_usage(stream)
    print(f"{parser.prog}: error: {message}", file=stream)
    return EXIT_USAGE


def _main_epilog() -> str:
    """Build the epilog: worked examples, every env var, then exit codes."""
    env_entries = (
        (ENV_TENANT, "tenant number, e.g. 123456 (or pass --tenant)"),
        (ENV_BASE_URL, "full portal URL; overrides the tenant/--tenant"),
        (ENV_USERNAME, "portal login"),
        (ENV_PASSWORD, "portal password"),
        (ENV_ENERGY_PRICE, "energy price in ct/kWh; required by 'cost'"),
        (ENV_BASE_PRICE, "annual standing charge in EUR; used by 'cost'"),
        (ENV_ADVANCE, "monthly advance payment in EUR; used by 'cost'"),
        (ENV_BILLING_YEAR_START, "billing year start as MM-DD; default 01-01"),
        (ENV_NO_DOTENV, "set (to any value) to skip reading .env entirely"),
    )
    # +2 for a gap between the longest name and its description, so a name
    # as long as PLUSPORTAL_MONTHLY_ADVANCE_EUR never runs into its text.
    column = max(len(name) for name, _ in env_entries) + 2
    env_lines = "\n".join(f"  {name:<{column}}{purpose}" for name, purpose in env_entries)
    return (
        "examples:\n"
        "  pyplusportal --tenant 123456 meters\n"
        "  pyplusportal --tenant 123456 overview\n"
        "  pyplusportal --tenant 123456 readings --from 2026-06-18 --to 2026-07-24\n"
        "  pyplusportal --tenant 123456 readings --meter 1000 --format csv\n"
        "  pyplusportal --tenant 123456 cost --today 2026-07-24\n"
        "  pyplusportal --tenant 123456 probe --out tests/fixtures/recorded\n"
        "\n"
        "environment variables:\n"
        f"{env_lines}\n"
        "\n"
        "  A .env file in the working directory is read for any of the above\n"
        "  that are not already set; the real environment always takes\n"
        "  precedence over the file.\n"
        "\n"
        "exit codes:\n"
        "  0  success\n"
        "  2  usage or authentication problem\n"
        "  3  portal unreachable\n"
        "\n"
        + textwrap.fill(
            "Run 'pyplusportal help' or 'pyplusportal ?' to see this text again, "
            "or 'pyplusportal help <command>' for a command's own options. "
            "Note: '?' is a glob character in many shells and may need quoting, "
            'e.g. pyplusportal "?".',
            width=79,
        )
    )


def _build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """Assemble the argument parser and a lookup of its sub-command parsers."""
    parser = _ArgumentParser(
        prog="pyplusportal",
        description=textwrap.fill(
            "Read consumption data from a PlusPortal instance. "
            f"Credentials come from ${ENV_USERNAME} and ${ENV_PASSWORD}, "
            "optionally via a .env file in the working directory.",
            width=79,
        ),
        epilog=_main_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {_cli_version()}",
        help="show the installed version and exit",
    )
    parser.add_argument("--tenant", help="tenant number, e.g. 123456")
    parser.add_argument("--base-url", help="full portal URL, overrides --tenant")

    sub = parser.add_subparsers(dest="command", required=True)

    meters = sub.add_parser(
        "meters",
        help="list metering points and their channels",
        description=(
            "List metering points and the OBIS channels available on each, "
            "together with the tariff use case (TAF) readings will be read against."
        ),
    )

    overview = sub.add_parser(
        "overview",
        help="show the portal's dashboard aggregates",
        description=(
            "Show the portal's own dashboard aggregates: this and last "
            "month's totals per channel, as the portal itself computes them."
        ),
    )

    readings = sub.add_parser(
        "readings",
        help="fetch daily consumption values",
        description="Fetch daily consumption values for a date range and render them.",
        epilog=(
            "example:\n"
            "  pyplusportal --tenant 123456 readings "
            "--from 2026-06-18 --to 2026-07-24 --format csv\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    readings.add_argument(
        "--from",
        type=date.fromisoformat,
        help="first day to include, as YYYY-MM-DD (default: 30 days ago)",
    )
    readings.add_argument(
        "--to",
        type=date.fromisoformat,
        help="last day to include, as YYYY-MM-DD (default: today)",
    )
    readings.add_argument("--meter", type=int, help="restrict to one metering point id, e.g. 1000")
    readings.add_argument(
        "--format",
        choices=("table", "csv", "json"),
        default="table",
        help="output format: table (default), csv, or json",
    )

    cost = sub.add_parser(
        "cost",
        help="project this billing year's cost and settlement",
        description=textwrap.fill(
            "Project this billing year's cost and expected settlement. The portal "
            "exposes no tariff or price data itself, so the energy price, standing "
            f"charge and advances must come from the environment (see {ENV_ENERGY_PRICE} "
            "and friends below).",
            width=79,
        ),
        epilog="example:\n  pyplusportal --tenant 123456 cost --today 2026-07-24\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cost.add_argument(
        "--today",
        type=date.fromisoformat,
        help="pretend it is this date, as YYYY-MM-DD (mainly for testing)",
    )

    probe = sub.add_parser(
        "probe",
        help="record redacted responses as test fixtures",
        description=(
            "Record redacted API responses under --out so they can be "
            "committed as test fixtures without leaking account details."
        ),
        epilog=("example:\n  pyplusportal --tenant 123456 probe --out tests/fixtures/recorded\n"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    probe.add_argument(
        "--out",
        default="tests/fixtures/recorded",
        help="output directory (default: tests/fixtures/recorded)",
    )

    sub_parsers = [
        ("meters", meters),
        ("overview", overview),
        ("readings", readings),
        ("cost", cost),
        ("probe", probe),
    ]
    commands: dict[str, argparse.ArgumentParser] = dict(sub_parsers)

    return parser, commands


def _handle_help(
    parser: argparse.ArgumentParser,
    commands: dict[str, argparse.ArgumentParser],
    topic_args: Sequence[str],
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Print full or per-command help for the ``help``/``?`` pseudo-commands."""
    if not topic_args:
        parser.print_help(stdout)
        return EXIT_OK

    topic = topic_args[0]
    subparser = commands.get(topic)
    if subparser is None:
        return _fail(parser, _unknown_command_message(topic, tuple(commands)), stderr)

    subparser.print_help(stdout)
    return EXIT_OK


async def _run(args: argparse.Namespace, env: dict[str, str]) -> int:
    """Resolve configuration, open a client and dispatch the sub-command."""
    target = args.base_url or env.get(ENV_BASE_URL) or args.tenant or env.get(ENV_TENANT)
    username = env.get(ENV_USERNAME)
    password = env.get(ENV_PASSWORD)

    required = {
        f"{ENV_TENANT} (or --tenant)": target,
        ENV_USERNAME: username,
        ENV_PASSWORD: password,
    }
    missing = [name for name, value in required.items() if not value]
    if missing or not (target and username and password):
        print(f"missing configuration: {', '.join(missing)}", file=args.stderr)
        return EXIT_USAGE

    args.env = env
    handler = {
        "meters": _cmd_meters,
        "overview": _cmd_overview,
        "readings": _cmd_readings,
        "cost": _cmd_cost,
        "probe": _cmd_probe,
    }[args.command]

    async with PlusPortalClient(target, username, password) as client:
        return await handler(client, args)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    stdout, stderr = sys.stdout, sys.stderr
    tokens = list(sys.argv[1:] if argv is None else argv)

    parser, commands = _build_parser()

    if not tokens:
        # A bare invocation is a request for orientation, not a usage error.
        parser.print_help(stdout)
        return EXIT_OK

    index = _command_position(tokens)
    token = tokens[index] if index is not None else None

    if token in ("help", "?"):
        # Intercepted before parse_args so neither appears as a real
        # sub-command in the usage line (which would read as an operation
        # on the portal, alongside meters/overview/readings/cost/probe).
        # index is not None whenever token is (token comes from tokens[index]).
        assert index is not None
        return _handle_help(parser, commands, tokens[index + 1 :], stdout, stderr)

    if token is not None and token not in commands:
        # Caught here, ahead of parse_args, so the message can name the
        # closest match — argparse's own "invalid choice" wording can't.
        return _fail(parser, _unknown_command_message(token, tuple(commands)), stderr)

    try:
        args = parser.parse_args(tokens)
    except _ParserError as err:
        return _fail(err.parser, str(err), stderr)

    args.stdout = stdout
    args.stderr = stderr

    try:
        return asyncio.run(_run(args, load_environment()))
    except AuthenticationError as err:
        print(f"could not sign in — check your credentials: {err}", file=args.stderr)
        return EXIT_USAGE
    except PortalUnavailableError as err:
        print(f"the portal is not reachable right now: {err}", file=args.stderr)
        return EXIT_UNAVAILABLE
    except ValueError as err:
        print(str(err), file=args.stderr)
        return EXIT_USAGE
    except PlusPortalError as err:
        print(f"unexpected response from the portal: {err}", file=args.stderr)
        return EXIT_UNAVAILABLE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
