"""Transport behaviour: base URLs, login, session reuse and error mapping."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from pyplusportal.client import PlusPortalClient, resolve_base_url
from pyplusportal.exceptions import AuthenticationError, PortalUnavailableError

from .conftest import json_response

BASE = "https://123456.plusportal.de"
LOGIN = f"{BASE}/msw/api/auth"
SESSION = f"{BASE}/msw/api/public/session"
LOGOUT = f"{BASE}/msw/api/auth/logout"
OVERVIEW = f"{BASE}/msw/api/edv/getOverview"
KD_FOR_SESSION = f"{BASE}/msw/api/auth/getKdForSession"
SWITCH = f"{BASE}/msw/api/auth/loginWithKdNr2"
USER_ITEMS = f"{BASE}/msw/api/account/getUserItemList"


@pytest.fixture
def client():
    return PlusPortalClient(BASE, "user", "s3cret", retry_backoff=0.0)


@pytest.fixture
def session_payload(session_payload):
    """Move the recorded session into the future so tests do not age out."""
    now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    session_payload["loginValidFrom"] = now_ms
    session_payload["loginValidTo"] = now_ms + 3_600_000
    return session_payload


# ------------------------------------------------------------- base URL


def test_tenant_number_expands_to_the_portal_url():
    """PP-EXT-001."""
    assert resolve_base_url("123456") == "https://123456.plusportal.de"


def test_full_url_is_accepted_and_normalised():
    assert resolve_base_url("https://123456.plusportal.de/") == "https://123456.plusportal.de"


def test_bare_hostname_gets_an_https_scheme():
    assert resolve_base_url("123456.plusportal.de") == "https://123456.plusportal.de"


def test_plain_http_is_upgraded_so_credentials_are_never_sent_in_the_clear():
    """PP-EXT-001."""
    assert resolve_base_url("http://123456.plusportal.de") == "https://123456.plusportal.de"


@pytest.mark.parametrize("value", ["", "   ", "not a host", "https://"])
def test_unusable_tenant_values_are_rejected(value):
    with pytest.raises(ValueError, match="tenant"):
        resolve_base_url(value)


# ---------------------------------------------------------------- login


@respx.mock
async def test_login_posts_credentials_and_returns_the_session(client, session_payload):
    login = respx.post(LOGIN).mock(return_value=httpx.Response(200))
    respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
    respx.get(KD_FOR_SESSION).mock(return_value=httpx.Response(200, json=["2000000001"]))

    async with client:
        session = await client.login()

    assert session.user_id == 10001
    assert login.calls.last.request.headers["content-type"].startswith("application/json")
    body = login.calls.last.request.content.decode()
    assert '"username": "user"' in body or '"username":"user"' in body


@respx.mock
async def test_rejected_credentials_raise_an_authentication_error(client):
    respx.post(LOGIN).mock(return_value=httpx.Response(401))

    async with client:
        with pytest.raises(AuthenticationError):
            await client.login()


@respx.mock
async def test_login_that_does_not_establish_a_session_is_an_authentication_error(client):
    """The portal answers 200 but hands out no session cookie."""
    respx.post(LOGIN).mock(return_value=httpx.Response(200))
    respx.get(SESSION).mock(return_value=httpx.Response(403))

    async with client:
        with pytest.raises(AuthenticationError):
            await client.login()


@respx.mock
async def test_an_account_without_the_energy_feature_is_rejected_early(client, session_payload):
    """PP-EXT-015."""
    session_payload["features"] = []
    respx.post(LOGIN).mock(return_value=httpx.Response(200))
    respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
    respx.get(KD_FOR_SESSION).mock(return_value=httpx.Response(200, json=["2000000001"]))

    async with client:
        with pytest.raises(AuthenticationError, match="energy"):
            await client.login()


# -------------------------------------------------------- session reuse


@respx.mock
async def test_a_data_call_logs_in_on_demand(client, session_payload, overview_payload):
    login = respx.post(LOGIN).mock(return_value=httpx.Response(200))
    respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
    respx.get(KD_FOR_SESSION).mock(return_value=httpx.Response(200, json=["2000000001"]))
    respx.get(OVERVIEW).mock(return_value=json_response("overview.json"))

    async with client:
        await client.get_overview()

    assert login.call_count == 1


@respx.mock
async def test_a_valid_session_is_reused_across_calls(client, session_payload, overview_payload):
    login = respx.post(LOGIN).mock(return_value=httpx.Response(200))
    respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
    respx.get(KD_FOR_SESSION).mock(return_value=httpx.Response(200, json=["2000000001"]))
    respx.get(OVERVIEW).mock(return_value=json_response("overview.json"))

    async with client:
        await client.get_overview()
        await client.get_overview()

    assert login.call_count == 1


@respx.mock
async def test_a_rejected_call_triggers_exactly_one_relogin_and_retry(
    client, session_payload, overview_payload
):
    """PP-EXT-012."""
    login = respx.post(LOGIN).mock(return_value=httpx.Response(200))
    respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
    respx.get(KD_FOR_SESSION).mock(return_value=httpx.Response(200, json=["2000000001"]))
    overview = respx.get(OVERVIEW).mock(
        side_effect=[
            httpx.Response(403),
            json_response("overview.json"),
        ]
    )

    async with client:
        result = await client.get_overview()

    assert len(result) == 1
    assert overview.call_count == 2
    assert login.call_count == 2  # once on demand, once after the 403


@respx.mock
async def test_repeated_rejection_gives_up_instead_of_looping(client, session_payload):
    """PP-EXT-012."""
    respx.post(LOGIN).mock(return_value=httpx.Response(200))
    respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
    respx.get(KD_FOR_SESSION).mock(return_value=httpx.Response(200, json=["2000000001"]))
    overview = respx.get(OVERVIEW).mock(return_value=httpx.Response(403))

    async with client:
        with pytest.raises(AuthenticationError):
            await client.get_overview()

    assert overview.call_count == 2


# ------------------------------------------------------------- failures


@respx.mock
async def test_server_errors_surface_as_portal_unavailable(client, session_payload):
    """PP-EXT-013."""
    respx.post(LOGIN).mock(return_value=httpx.Response(200))
    respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
    respx.get(KD_FOR_SESSION).mock(return_value=httpx.Response(200, json=["2000000001"]))
    overview = respx.get(OVERVIEW).mock(return_value=httpx.Response(500))

    async with client:
        with pytest.raises(PortalUnavailableError):
            await client.get_overview()

    assert overview.call_count > 1, "server errors should be retried before giving up"


@respx.mock
async def test_a_transient_server_error_is_retried_successfully(
    client, session_payload, overview_payload
):
    """PP-EXT-013."""
    respx.post(LOGIN).mock(return_value=httpx.Response(200))
    respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
    respx.get(KD_FOR_SESSION).mock(return_value=httpx.Response(200, json=["2000000001"]))
    respx.get(OVERVIEW).mock(side_effect=[httpx.Response(503), json_response("overview.json")])

    async with client:
        assert len(await client.get_overview()) == 1


@respx.mock
async def test_network_failures_surface_as_portal_unavailable(client):
    respx.post(LOGIN).mock(side_effect=httpx.ConnectError("no route to host"))

    async with client:
        with pytest.raises(PortalUnavailableError):
            await client.login()


@respx.mock
async def test_a_non_json_body_surfaces_as_portal_unavailable(client, session_payload):
    """PP-EXT-014: An HTML error page from a reverse proxy must not look like empty data."""
    respx.post(LOGIN).mock(return_value=httpx.Response(200))
    respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
    respx.get(KD_FOR_SESSION).mock(return_value=httpx.Response(200, json=["2000000001"]))
    respx.get(OVERVIEW).mock(return_value=httpx.Response(200, html="<html>oops</html>"))

    async with client:
        with pytest.raises(PortalUnavailableError):
            await client.get_overview()


# ------------------------------------------------------------- lifecycle


@respx.mock
async def test_logout_ends_the_session(client, session_payload):
    respx.post(LOGIN).mock(return_value=httpx.Response(200))
    respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
    respx.get(KD_FOR_SESSION).mock(return_value=httpx.Response(200, json=["2000000001"]))
    logout = respx.get(LOGOUT).mock(return_value=httpx.Response(200))

    async with client:
        await client.login()
        await client.logout()

    assert logout.call_count == 1
    assert client.session is None


@respx.mock
async def test_logout_without_a_session_does_not_call_the_portal(client):
    logout = respx.get(LOGOUT).mock(return_value=httpx.Response(200))

    async with client:
        await client.logout()

    assert logout.call_count == 0


async def test_an_injected_http_client_is_not_closed_by_us():
    transport = httpx.AsyncClient()
    client = PlusPortalClient(BASE, "user", "pw", client=transport)

    async with client:
        pass

    assert not transport.is_closed
    await transport.aclose()


def test_the_password_never_appears_in_the_representation():
    """PP-SEC-002."""
    assert "s3cret" not in repr(PlusPortalClient(BASE, "user", "s3cret"))


# -------------------------------------------------------- customer numbers


def _items(obis: str, taf_number: int) -> list[dict[str, Any]]:
    """One metering point carrying a single OBIS channel.

    Both customer numbers report the *same* metering point id, because that is
    what the portal does: the id identifies the physical connection, the
    customer number decides which direction of it you are shown.
    """
    return [
        {
            "type": "gwa",
            "userItems": [
                {
                    "id": 1000,
                    "bez": "1ABC0000000000*",
                    "category": "Electricity",
                    "sourceType": "ROBOTRON",
                    "directions": [{"kennzahl": {"kennzahl": obis}}],
                    "tafs": [
                        {
                            "gwatafnr": taf_number,
                            "gwataftype": 7,
                            "gwatafbez": "07: Zählerstandsgangmessung",
                            "kennzahl": [obis],
                            "status": 1,
                        }
                    ],
                }
            ],
        }
    ]


BY_CUSTOMER = {
    "2000000001": _items("1-0:1.8.0", 4001),
    "2000000002": _items("1-0:2.8.0", 4002),
}


@pytest.fixture
def selected() -> list[str]:
    """Record which customer number the session was switched to, in order."""
    return []


@pytest.fixture
def two_customers(session_payload, selected):
    """Serve a login bound to two customer numbers, one per direction."""

    def switch(request: httpx.Request) -> httpx.Response:
        selected.append(json.loads(request.content)["custNo"])
        return httpx.Response(200)

    def items(request: httpx.Request) -> httpx.Response:
        current = selected[-1] if selected else "2000000001"
        return httpx.Response(200, json=BY_CUSTOMER[current])

    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).mock(return_value=httpx.Response(200))
        mock.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
        mock.get(KD_FOR_SESSION).mock(
            return_value=httpx.Response(200, json=["2000000001", "2000000002"])
        )
        mock.post(SWITCH).mock(side_effect=switch)
        mock.get(USER_ITEMS).mock(side_effect=items)
        yield mock


async def test_every_customer_number_of_a_login_is_read(client, two_customers, selected):
    """PP-EXT-018: one login, two customer numbers, one direction each.

    The session only ever shows one customer at a time, so a client that logs
    in and reads what it is given sees a single direction — and nothing says
    the other exists.
    """
    points = await client.get_meter_points()

    assert [point.customer for point in points] == ["2000000001", "2000000002"]
    assert {point.id for point in points} == {1000}, "the same meter, seen from both sides"
    assert selected == ["2000000001", "2000000002"]


async def test_a_login_with_one_customer_number_is_not_switched(client, session_payload):
    """PP-EXT-018: the switch is a cost, and most logins have nothing to switch to."""
    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).mock(return_value=httpx.Response(200))
        mock.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
        mock.get(KD_FOR_SESSION).mock(return_value=httpx.Response(200, json=["2000000001"]))
        switch = mock.post(SWITCH).mock(return_value=httpx.Response(200))
        mock.get(USER_ITEMS).mock(return_value=httpx.Response(200, json=_items("1-0:1.8.0", 4001)))

        points = await client.get_meter_points()

    assert len(points) == 1
    assert not switch.called, "nothing to select when there is only one"


async def test_a_portal_without_the_customer_endpoint_still_works(client, session_payload):
    """PP-EXT-018: older portals do not know it, and must not be broken by it."""
    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).mock(return_value=httpx.Response(200))
        mock.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
        mock.get(KD_FOR_SESSION).mock(return_value=httpx.Response(404))
        switch = mock.post(SWITCH).mock(return_value=httpx.Response(200))
        mock.get(USER_ITEMS).mock(return_value=httpx.Response(200, json=_items("1-0:1.8.0", 4001)))

        points = await client.get_meter_points()

    assert [point.id for point in points] == [1000]
    assert not switch.called


async def test_a_re_login_mid_request_re_selects_the_customer(client, session_payload):
    """PP-EXT-018: logging in again resets the session to the default customer.

    PP-EXT-012 retries a rejected request once after logging in again. Without
    re-selecting, the retry reads the *other* direction of the same meter and
    the answer is a plausible series of the wrong energy.
    """
    calls: list[str] = []

    def switch(request: httpx.Request) -> httpx.Response:
        calls.append("switch:" + json.loads(request.content)["custNo"])
        return httpx.Response(200)

    def config(request: httpx.Request) -> httpx.Response:
        calls.append("config")
        if len([c for c in calls if c == "config"]) == 1:
            return httpx.Response(401)
        return json_response("diagram_config.json")

    def login(request: httpx.Request) -> httpx.Response:
        calls.append("login")
        return httpx.Response(200)

    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).mock(side_effect=login)
        mock.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
        mock.get(KD_FOR_SESSION).mock(
            return_value=httpx.Response(200, json=["2000000001", "2000000002"])
        )
        mock.post(SWITCH).mock(side_effect=switch)
        mock.get(USER_ITEMS).mock(return_value=httpx.Response(200, json=BY_CUSTOMER["2000000002"]))
        mock.get(url__startswith=f"{BASE}/msw/api/edv/getDiagramConfigById").mock(
            side_effect=config
        )

        points = await client.get_meter_points()
        feed_in = next(p for p in points if p.customer == "2000000002")
        await client.get_channels(feed_in)

    assert calls[-4:] == ["config", "login", "switch:2000000002", "config"], calls
