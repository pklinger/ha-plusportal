"""Async HTTP client for a PlusPortal instance."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from types import TracebackType
from typing import Any, Self

import httpx

from .const import (
    BASE_URL_TEMPLATE,
    DEFAULT_METERING_INTERVAL,
    DEFAULT_TIMEOUT,
    DEVICE_TYPE_GWA,
    DEVICE_TYPE_TAF,
    DIAGRAM_KENZ_GROUP,
    DIAGRAM_STATISTIC_TYPE,
    DIAGRAM_SUB_TYPE,
    PATH_DIAGRAM_CONFIG,
    PATH_DIAGRAM_RESULT,
    PATH_LOGIN,
    PATH_LOGOUT,
    PATH_OVERVIEW,
    PATH_SESSION,
    PATH_USER_ITEM_LIST,
    PORTAL_TZ,
)
from .exceptions import AuthenticationError, ParseError, PortalUnavailableError
from .json import json_loads
from .models import Channel, MeterPoint, Overview, Reading, Session

_LOGGER = logging.getLogger(__name__)

_TENANT_RE = re.compile(r"^\d{4,8}$")
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

#: Sent with every request. Identifies the project to the portal operator and
#: says plainly that it is not theirs, so an admin seeing it in a log knows
#: what it is and where to complain.
USER_AGENT = "pyplusportal (unofficial; +https://github.com/pklinger/ha-plusportal)"

#: How often a request is attempted before the portal is declared unavailable.
_MAX_ATTEMPTS = 3


def resolve_base_url(tenant_or_url: str) -> str:
    """Turn a tenant number, hostname or URL into the portal's base URL.

    Always yields an ``https`` URL: the portal is reached with credentials, so
    downgrading to plaintext is never the right answer.
    """
    value = (tenant_or_url or "").strip().rstrip("/")

    if _TENANT_RE.match(value):
        return BASE_URL_TEMPLATE.format(tenant=value)

    without_scheme = re.sub(r"^https?://", "", value, flags=re.IGNORECASE)
    if _HOSTNAME_RE.match(without_scheme):
        return f"https://{without_scheme}"

    raise ValueError(
        f"{tenant_or_url!r} is neither a tenant number (e.g. '123456') nor a portal URL"
    )


class PlusPortalClient:
    """Reads metering data out of one PlusPortal instance.

    Sessions are established lazily: the first call that needs authentication
    logs in, and a call rejected by the portal triggers exactly one re-login
    and retry. That keeps callers free of session bookkeeping without risking
    a login loop against someone else's server.

    Use as an async context manager, or call :meth:`aclose` when done.
    """

    def __init__(
        self,
        tenant_or_url: str,
        username: str,
        password: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        retry_backoff: float = 1.0,
    ) -> None:
        """Configure the client; no network traffic happens here."""
        self.base_url = resolve_base_url(tenant_or_url)
        self._username = username
        self._password = password
        self._retry_backoff = retry_backoff
        self._session: Session | None = None
        self._login_lock = asyncio.Lock()

        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)

    def __repr__(self) -> str:
        """Describe the client without ever revealing the password."""
        return f"<PlusPortalClient base_url={self.base_url!r} username={self._username!r}>"

    @property
    def session(self) -> Session | None:
        """The current session, or ``None`` while not logged in."""
        return self._session

    async def __aenter__(self) -> Self:
        """Enter the async context; the session is established on demand."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Leave the async context, releasing anything we own."""
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client, unless the caller supplied it."""
        if self._owns_client:
            await self._client.aclose()

    # ----------------------------------------------------------- transport

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send one request, retrying server-side failures with backoff."""
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = await self._client.request(
                    method, url, headers={"User-Agent": USER_AGENT}, **kwargs
                )
            except httpx.HTTPError as err:
                last_error = err
            else:
                if response.status_code < 500:
                    return response
                last_error = PortalUnavailableError(
                    f"{method} {path} failed with HTTP {response.status_code}"
                )

            if attempt < _MAX_ATTEMPTS - 1:
                await asyncio.sleep(self._retry_backoff * (2**attempt))

        raise PortalUnavailableError(f"{method} {path} failed: {last_error}") from last_error

    @staticmethod
    def _decode(response: httpx.Response, *, path: str) -> Any:
        """Decode a JSON body, treating anything else as an outage.

        A reverse proxy in front of the portal answers with an HTML error page
        on 200; parsing that as "no data" would silently zero out consumption.
        """
        try:
            return json_loads(response.content)
        except ValueError as err:
            raise PortalUnavailableError(
                f"{path} returned a non-JSON body (content-type: "
                f"{response.headers.get('content-type', 'unknown')})"
            ) from err

    async def _authenticated_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET an endpoint that requires a session, logging in when needed."""
        await self._ensure_session()

        response = await self._send("GET", path, params=params)
        if response.status_code in (401, 403):
            _LOGGER.debug("Session rejected on %s, logging in again", path)
            await self._login(force=True)
            response = await self._send("GET", path, params=params)

        if response.status_code in (401, 403):
            self._session = None
            raise AuthenticationError(f"portal kept rejecting the session on {path}")
        if response.status_code >= 400:
            raise PortalUnavailableError(f"GET {path} failed with HTTP {response.status_code}")

        return self._decode(response, path=path)

    # ------------------------------------------------------------- session

    async def _ensure_session(self) -> None:
        """Log in if there is no usable session."""
        if self._session is None or self._session.is_expired():
            await self._login()

    async def _login(self, *, force: bool = False) -> Session:
        """Perform the login handshake, guarding against concurrent attempts."""
        async with self._login_lock:
            if not force and self._session is not None and not self._session.is_expired():
                return self._session

            response = await self._send(
                "POST",
                PATH_LOGIN,
                json={
                    "username": self._username,
                    "password": self._password,
                    "stayLoggedIn": False,
                },
            )
            if response.status_code >= 400:
                raise AuthenticationError(
                    f"portal rejected the credentials (HTTP {response.status_code})"
                )

            session = await self._read_session()
            if session is None:
                raise AuthenticationError("login succeeded but no session was established")
            if not session.has_energy_data:
                raise AuthenticationError("this account has no access to energy data in the portal")

            self._session = session
            return session

    async def _read_session(self) -> Session | None:
        """Fetch the current session, or ``None`` if the portal sees none."""
        response = await self._send("GET", PATH_SESSION)
        if response.status_code in (401, 403):
            return None
        if response.status_code >= 400:
            raise PortalUnavailableError(
                f"GET {PATH_SESSION} failed with HTTP {response.status_code}"
            )
        return Session.from_api(self._decode(response, path=PATH_SESSION))

    async def login(self) -> Session:
        """Log in and return the established session."""
        return await self._login(force=True)

    async def get_session(self) -> Session | None:
        """Ask the portal about the current session without logging in."""
        return await self._read_session()

    async def logout(self) -> None:
        """End the portal session, if there is one."""
        if self._session is None:
            return
        try:
            await self._send("GET", PATH_LOGOUT)
        except PortalUnavailableError:
            _LOGGER.debug("Logout call failed; dropping the session anyway", exc_info=True)
        finally:
            self._session = None

    # ---------------------------------------------------------------- data

    async def fetch_raw(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET any portal endpoint and return the decoded payload untouched.

        The escape hatch for exploring endpoints this library does not model
        yet, and what ``pyplusportal probe`` records its fixtures from.
        """
        return await self._authenticated_get(path, params)

    async def get_meter_points(self) -> list[MeterPoint]:
        """List the metering points assigned to this account."""
        raw = await self._authenticated_get(PATH_USER_ITEM_LIST, {"page": 0})
        return MeterPoint.list_from_api(raw)

    async def get_overview(self) -> list[Overview]:
        """Fetch the dashboard aggregates for every metering point."""
        return Overview.list_from_api(await self._authenticated_get(PATH_OVERVIEW))

    async def get_channels(self, meter_point: MeterPoint) -> list[Channel]:
        """Discover the OBIS channels of a metering point's primary tariff use case."""
        taf = meter_point.primary_taf
        if taf is None:
            return []

        path = PATH_DIAGRAM_CONFIG.format(
            device_type=DEVICE_TYPE_TAF, device_id=taf.number, group=taf.number
        )
        raw = await self._authenticated_get(path, {"diagramSubType": meter_point.id, "sublocId": 0})
        return Channel.list_from_api(raw, meter_point_id=meter_point.id, taf_number=taf.number)

    async def get_daily_readings(self, channel: Channel, start: date, end: date) -> list[Reading]:
        """Fetch daily consumption for a channel over an inclusive date range.

        The portal only breaks a series down into daily buckets when asked for a
        ``month`` period, and answers with the whole calendar month regardless of
        the range given, so the request is split per month and the surplus days
        are dropped here. Days seen twice keep their last value, which is what
        makes re-fetching a corrected window idempotent.
        """
        if start > end:
            raise ValueError("start date must not be after the end date")

        by_day: dict[date, Reading] = {}
        for window_start, window_end in _month_windows(start, end):
            for reading in await self._fetch_month(channel, window_start, window_end):
                if start <= reading.day <= end:
                    by_day[reading.day] = reading

        return [by_day[day] for day in sorted(by_day)]

    async def get_interval_readings(
        self, channel: Channel, start: date, end: date
    ) -> list[Reading]:
        """Fetch metering-interval energy for a channel over an inclusive range.

        This is the finest data the portal offers — typically 96 values per day.
        It comes from the ``power`` series as average kW per interval and is
        converted to kWh here, which has been checked against the portal's own
        daily totals to the last digit.
        """
        if start > end:
            raise ValueError("start date must not be after the end date")

        by_start: dict[datetime, Reading] = {}
        for window_start, window_end in _month_windows(start, end):
            raw = await self._fetch_diagram(channel, window_start, window_end, "power")
            for reading in _interval_readings_from_diagram(raw, channel):
                if start <= reading.start.date() <= end:
                    by_start[reading.start] = reading

        return [by_start[moment] for moment in sorted(by_start)]

    async def _fetch_month(self, channel: Channel, start: date, end: date) -> list[Reading]:
        """Fetch the daily values the portal reports for one calendar month."""
        raw = await self._fetch_diagram(channel, start, end, "consumption")
        return _readings_from_diagram(raw, channel)

    async def _fetch_diagram(
        self, channel: Channel, start: date, end: date, diagram_type: str
    ) -> Any:
        """Request one diagram series for a date range.

        ``period=month`` is not a range limit — the portal happily spans several
        months — but it is the only period for which timestamps come back on
        local midnight rather than UTC midnight, so it is always used.
        """
        start_ms = _epoch_ms(datetime.combine(start, time.min, tzinfo=PORTAL_TZ))
        end_ms = _epoch_ms(datetime.combine(end, time.max, tzinfo=PORTAL_TZ))

        path = PATH_DIAGRAM_RESULT.format(
            device_type=DEVICE_TYPE_GWA, device_id=channel.taf_number, end_ms=end_ms
        )
        return await self._authenticated_get(
            path,
            {
                "startDate": start_ms,
                "obis": channel.obis,
                "period": "month",
                "diagramType": diagram_type,
                # Required: without it the portal answers 404.
                "diagramSubType": DIAGRAM_SUB_TYPE,
                "kenzGrNr": DIAGRAM_KENZ_GROUP,
                "statisticType": DIAGRAM_STATISTIC_TYPE,
                "sublocId": 0,
                "lpspNr": 0,
                # The power series returns 500 unless all four are present.
                "allData": "false",
                "avgOnly": "false",
                "maxOnly": "false",
                "rawValues": "false",
                "doCompare": "false",
                "useGasDay": "false",
            },
        )


def _month_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Split an inclusive date range into per-calendar-month windows."""
    windows: list[tuple[date, date]] = []
    cursor = start.replace(day=1)
    while cursor <= end:
        # Stepping 32 days from the first of a month always lands in the next
        # one, whatever its length — no month-arithmetic special cases needed.
        next_month = (cursor + timedelta(days=32)).replace(day=1)
        windows.append((max(cursor, start), min(next_month - timedelta(days=1), end)))
        cursor = next_month
    return windows


def _series(raw: Any) -> Iterator[tuple[dict[str, Any], list[Any]]]:
    """Walk the series of a ``getDiagramResultList`` response."""
    if not isinstance(raw, list):
        raise ParseError("diagram result is not a list", field="response")
    for result in raw:
        for entry in result.get("data") or []:
            yield entry, list(entry.get("values") or [])


def _readings_from_diagram(raw: Any, channel: Channel) -> list[Reading]:
    """Turn a ``consumption`` response into daily readings."""
    readings: list[Reading] = []
    for entry, values in _series(raw):
        obis = str(entry.get("obis") or channel.obis)
        unit = str(entry.get("unit") or channel.unit)
        readings.extend(Reading.from_daily_api(point, obis=obis, unit=unit) for point in values)
    return readings


def _detect_interval(values: list[Any]) -> timedelta:
    """Infer the metering interval from the spacing of consecutive points.

    Electricity in Germany is metered quarter-hourly, but gas and older meters
    use other intervals, so the length is read off the data rather than assumed.
    """
    gaps = sorted(
        int(later["date"]) - int(earlier["date"])
        for earlier, later in pairwise(values)
        if isinstance(earlier.get("date"), int) and isinstance(later.get("date"), int)
    )
    if not gaps:
        return DEFAULT_METERING_INTERVAL
    median_ms = gaps[len(gaps) // 2]
    return timedelta(milliseconds=median_ms) if median_ms > 0 else DEFAULT_METERING_INTERVAL


def _interval_readings_from_diagram(raw: Any, channel: Channel) -> list[Reading]:
    """Turn a ``power`` response into interval readings, converted to energy."""
    readings: list[Reading] = []
    for entry, values in _series(raw):
        obis = str(entry.get("obis") or channel.obis)
        interval = _detect_interval(values)
        readings.extend(
            Reading.from_interval_api(point, obis=obis, interval=interval) for point in values
        )
    return readings


def _epoch_ms(moment: datetime) -> int:
    """Express an aware datetime as epoch milliseconds."""
    return int(moment.timestamp() * 1000)
