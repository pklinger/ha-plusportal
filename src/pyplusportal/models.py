"""Domain model for the data a PlusPortal instance exposes.

Every ``*_from_api`` constructor takes a decoded portal payload and is the only
place that knows the backend's field names. Portal responses are inconsistent —
fields go missing, the same key means different things at different nesting
levels, and ``bez`` appears twice in one object — so parsing is deliberately
defensive and fails loudly with :class:`~pyplusportal.exceptions.ParseError`
rather than silently producing wrong numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Self

from .const import (
    PORTAL_TZ,
    SESSION_EXPIRY_MARGIN_SECONDS,
    TAF_PREFERENCE,
    TAF_STATUS_ACTIVE,
)
from .exceptions import ParseError

#: ``"07: Zählerstandsgangmessung"`` — the portal prefixes the TAF number.
_TAF_LABEL_RE = re.compile(r"^\s*(\d+)\s*:\s*(.+?)\s*$")

__all__ = [
    "Channel",
    "MeterPoint",
    "Overview",
    "Reading",
    "Resolution",
    "Session",
    "Taf",
    "ValueState",
]


class ValueState(StrEnum):
    """Quality flag the metering operator attaches to every measured value.

    Only true and substitute values end up on the invoice; preliminary values
    are placeholders that get replaced once the real reading arrives.
    """

    TRUE_VALUE = "W"
    """Wahrer Wert — final, and the basis for billing."""

    SUBSTITUTE = "E"
    """Ersatzwert — a substitute value, still billable."""

    PRELIMINARY = "V"
    """Vorläufiger Wert — preliminary, not billable."""

    UNKNOWN = "?"
    """The portal sent a flag this library does not know."""

    @classmethod
    def parse(cls, raw: object) -> ValueState:
        """Map a raw flag onto a state, tolerating unknown and missing codes."""
        if isinstance(raw, str):
            try:
                return cls(raw)
            except ValueError:
                pass
        return cls.UNKNOWN

    @property
    def billable(self) -> bool:
        """Whether a value carrying this flag counts towards the invoice."""
        return self in (ValueState.TRUE_VALUE, ValueState.SUBSTITUTE)


def _to_datetime(raw: object, *, field_name: str) -> datetime:
    """Convert epoch milliseconds into an aware datetime in portal local time."""
    if not isinstance(raw, int | float | Decimal) or isinstance(raw, bool):
        raise ParseError("expected an epoch timestamp in milliseconds", field=field_name)
    return datetime.fromtimestamp(int(raw) / 1000, tz=PORTAL_TZ)


def _to_decimal(raw: object, *, field_name: str) -> Decimal:
    """Convert a numeric payload value into an exact ``Decimal``."""
    if isinstance(raw, bool) or raw is None:
        raise ParseError("expected a number", field=field_name)
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError) as err:
        raise ParseError("expected a number", field=field_name) from err


def _optional_decimal(raw: object, *, field_name: str) -> Decimal | None:
    """Like :func:`_to_decimal`, but pass ``None`` through unchanged."""
    return None if raw is None else _to_decimal(raw, field_name=field_name)


def _require(mapping: Any, key: str, *, context: str) -> Any:
    """Fetch a required key, or fail with a message naming it."""
    if not isinstance(mapping, dict) or key not in mapping or mapping[key] is None:
        raise ParseError(f"{context} is missing a required field", field=key)
    return mapping[key]


class Resolution(StrEnum):
    """How finely a reading resolves time."""

    QUARTER_HOUR = "quarter_hour"
    """A metering interval, typically 15 minutes."""

    DAY = "day"
    """One calendar day."""


def _raw_value(raw: Any) -> Any:
    """Pull the measured number out of a diagram point."""
    if not isinstance(raw, dict):
        raise ParseError("reading is not an object", field="value")
    value = raw.get("value")
    if value is None:
        value = raw.get("valueA")
    if value is None:
        raise ParseError("reading carries no value", field="value")
    return value


@dataclass(frozen=True, slots=True)
class Reading:
    """Energy consumed on one OBIS channel over one interval.

    ``value`` is always energy, never power: the portal reports its fine-grained
    series as average power in kW, and that is converted on the way in so that
    readings can simply be summed.
    """

    start: datetime
    """Start of the interval, in portal local time."""

    duration: timedelta
    value: Decimal
    unit: str
    obis: str
    state: ValueState
    resolution: Resolution = Resolution.QUARTER_HOUR

    @property
    def end(self) -> datetime:
        """Exclusive end of the interval."""
        return self.start + self.duration

    @property
    def billable(self) -> bool:
        """Whether this value would be used to calculate the invoice."""
        return self.state.billable

    @property
    def day(self) -> date:
        """Calendar day this reading belongs to, in portal local time."""
        return self.start.date()

    @classmethod
    def from_daily_api(cls, raw: Any, *, obis: str, unit: str | None = None) -> Self:
        """Build a daily reading from the ``consumption`` series.

        Points in that series are already energy and are labelled with the
        start of their day.
        """
        return cls(
            start=_to_datetime(_require(raw, "date", context="reading"), field_name="date"),
            duration=timedelta(days=1),
            value=_to_decimal(_raw_value(raw), field_name="value"),
            unit=raw.get("unitA") or unit or "",
            obis=obis,
            state=ValueState.parse(raw.get("state")),
            resolution=Resolution.DAY,
        )

    @classmethod
    def from_interval_api(cls, raw: Any, *, obis: str, interval: timedelta) -> Self:
        """Build an interval reading from the ``power`` series.

        Two conversions happen here, both verified against the portal's own
        daily totals: the timestamp marks the *end* of the interval, so the
        start is shifted back by its length; and the value is average power in
        kW, so it is multiplied by the interval length in hours to give kWh.
        """
        end = _to_datetime(_require(raw, "date", context="reading"), field_name="date")
        kilowatts = _to_decimal(_raw_value(raw), field_name="value")
        hours = Decimal(interval.total_seconds()) / Decimal(3600)

        return cls(
            start=end - interval,
            duration=interval,
            value=kilowatts * hours,
            unit="kWh",
            obis=obis,
            state=ValueState.parse(raw.get("state")),
            resolution=Resolution.QUARTER_HOUR,
        )


@dataclass(frozen=True, slots=True)
class Taf:
    """A tariff use case (Tarifanwendungsfall) configured on a meter."""

    number: int
    type: int
    label: str
    obis: list[str]
    active: bool

    @property
    def title(self) -> str:
        """Readable name, with the standardised TAF number spelled out.

        The portal labels these ``"07: Zählerstandsgangmessung"``. The leading
        number is the tariff use case from the German smart meter gateway
        specification, but on its own it just reads as noise in a device name.
        """
        match = _TAF_LABEL_RE.match(self.label)
        if match:
            return f"{match.group(2)} (TAF {int(match.group(1))})"
        return self.label or f"TAF {self.type}"

    @classmethod
    def from_api(cls, raw: Any) -> Self:
        """Build a tariff use case from one ``tafs`` entry."""
        return cls(
            number=int(_require(raw, "gwatafnr", context="tariff use case")),
            type=int(raw.get("gwataftype") or 0),
            label=str(raw.get("gwatafbez") or ""),
            obis=[str(code) for code in raw.get("kennzahl") or []],
            active=raw.get("status") == TAF_STATUS_ACTIVE,
        )


@dataclass(frozen=True, slots=True)
class MeterPoint:
    """One metering point (Zählpunkt) assigned to the logged-in account."""

    id: int
    name: str
    category: str
    device_type: str
    source_type: str
    tafs: tuple[Taf, ...] = field(default_factory=tuple)

    @property
    def active_tafs(self) -> tuple[Taf, ...]:
        """The tariff use cases currently delivering data."""
        return tuple(taf for taf in self.tafs if taf.active)

    @property
    def primary_taf(self) -> Taf | None:
        """The active tariff use case with the most detailed data, if any."""
        candidates = self.active_tafs
        if not candidates:
            return None

        def rank(taf: Taf) -> tuple[int, int]:
            try:
                preference = TAF_PREFERENCE.index(taf.type)
            except ValueError:
                preference = len(TAF_PREFERENCE)
            return (preference, taf.number)

        return min(candidates, key=rank)

    @classmethod
    def list_from_api(cls, raw: Any) -> list[Self]:
        """Flatten the grouped ``getUserItemList`` response into meter points."""
        points: list[Self] = []
        for group in raw or []:
            device_type = str(group.get("type") or "")
            for item in group.get("userItems") or []:
                points.append(
                    cls(
                        id=int(_require(item, "id", context="meter point")),
                        name=str(item.get("bez") or ""),
                        category=str(item.get("category") or ""),
                        device_type=device_type,
                        source_type=str(item.get("sourceType") or ""),
                        tafs=tuple(Taf.from_api(taf) for taf in item.get("tafs") or []),
                    )
                )
        return points


@dataclass(frozen=True, slots=True)
class Overview:
    """Aggregated figures the portal shows on its dashboard tile."""

    meter_point_id: int
    obis: str
    label: str
    unit: str
    this_month_sum: Decimal | None
    prev_month_sum: Decimal | None
    first_value_at: datetime
    last_value_at: datetime

    @classmethod
    def list_from_api(cls, raw: Any) -> list[Self]:
        """Build one overview per OBIS channel out of ``getOverview``."""
        overviews: list[Self] = []
        for entry in raw or []:
            meter_point_id = int(_require(entry, "id", context="overview"))
            for data in entry.get("data") or []:
                kz = data.get("kz") or {}
                overviews.append(
                    cls(
                        meter_point_id=meter_point_id,
                        obis=str(kz.get("kennzahl") or ""),
                        label=str(kz.get("portalBez") or kz.get("bez") or ""),
                        unit=str(data.get("unit") or ""),
                        this_month_sum=_optional_decimal(
                            data.get("thisMonthSum"), field_name="thisMonthSum"
                        ),
                        prev_month_sum=_optional_decimal(
                            data.get("prevMonthSum"), field_name="prevMonthSum"
                        ),
                        first_value_at=_to_datetime(
                            _require(data, "dtFirstValue", context="overview"),
                            field_name="dtFirstValue",
                        ),
                        last_value_at=_to_datetime(
                            _require(data, "dtLastValue", context="overview"),
                            field_name="dtLastValue",
                        ),
                    )
                )
        return overviews


@dataclass(frozen=True, slots=True)
class Channel:
    """One OBIS data series of a meter point, together with its data range."""

    meter_point_id: int
    taf_number: int
    obis: str
    label: str
    unit: str
    periods: tuple[str, ...]
    available_from: datetime
    available_to: datetime

    @classmethod
    def list_from_api(cls, raw: Any, *, meter_point_id: int, taf_number: int) -> list[Self]:
        """Build channels from ``getDiagramConfigById``.

        The portal repeats the same configuration once per configured line, so
        identical OBIS codes are collapsed into a single channel.
        """
        channels: dict[str, Self] = {}
        for config in raw or []:
            available_from = _to_datetime(
                _require(config, "begin", context="diagram config"), field_name="begin"
            )
            available_to = _to_datetime(
                _require(config, "end", context="diagram config"), field_name="end"
            )
            periods = tuple(str(period) for period in config.get("periodType") or ())
            for kz in config.get("kz") or []:
                obis = str(kz.get("obis") or "")
                if not obis or obis in channels:
                    continue
                channels[obis] = cls(
                    meter_point_id=meter_point_id,
                    taf_number=taf_number,
                    obis=obis,
                    label=str(kz.get("bez") or ""),
                    unit=str(kz.get("unit") or ""),
                    periods=periods,
                    available_from=available_from,
                    available_to=available_to,
                )
        return list(channels.values())


@dataclass(frozen=True, slots=True)
class Session:
    """An authenticated portal session."""

    user_id: int
    username: str
    valid_from: datetime
    expires_at: datetime
    features: tuple[str, ...]

    @property
    def has_energy_data(self) -> bool:
        """Whether this account may access consumption data at all."""
        return "energydataview" in self.features

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Whether the session is stale, with a margin to avoid racing the portal."""
        now = now or datetime.now(tz=PORTAL_TZ)
        margin = timedelta(seconds=SESSION_EXPIRY_MARGIN_SECONDS)
        return now >= self.expires_at - margin

    @classmethod
    def from_api(cls, raw: Any) -> Self:
        """Build a session from ``/public/session``."""
        return cls(
            user_id=int(_require(raw, "id", context="session")),
            username=str(raw.get("username") or ""),
            valid_from=_to_datetime(
                _require(raw, "loginValidFrom", context="session"), field_name="loginValidFrom"
            ),
            expires_at=_to_datetime(
                _require(raw, "loginValidTo", context="session"), field_name="loginValidTo"
            ),
            features=tuple(
                str(feature.get("featureTag"))
                for feature in raw.get("features") or []
                if feature.get("featureTag")
            ),
        )
