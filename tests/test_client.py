"""Transport behaviour: base URLs, login, session reuse and error mapping."""

from __future__ import annotations

from datetime import UTC, datetime

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

    async with client:
        with pytest.raises(AuthenticationError, match="energy"):
            await client.login()


# -------------------------------------------------------- session reuse


@respx.mock
async def test_a_data_call_logs_in_on_demand(client, session_payload, overview_payload):
    login = respx.post(LOGIN).mock(return_value=httpx.Response(200))
    respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
    respx.get(OVERVIEW).mock(return_value=json_response("overview.json"))

    async with client:
        await client.get_overview()

    assert login.call_count == 1


@respx.mock
async def test_a_valid_session_is_reused_across_calls(client, session_payload, overview_payload):
    login = respx.post(LOGIN).mock(return_value=httpx.Response(200))
    respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
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
    respx.get(OVERVIEW).mock(return_value=httpx.Response(200, html="<html>oops</html>"))

    async with client:
        with pytest.raises(PortalUnavailableError):
            await client.get_overview()


# ------------------------------------------------------------- lifecycle


@respx.mock
async def test_logout_ends_the_session(client, session_payload):
    respx.post(LOGIN).mock(return_value=httpx.Response(200))
    respx.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
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
