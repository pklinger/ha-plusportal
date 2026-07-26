"""The command line interface — the way to exercise extraction without HA."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

import pyplusportal.cli as cli_module
from pyplusportal.cli import main, parse_env_file, redact

from .conftest import json_response

BASE = "https://123456.plusportal.de"
LOGIN = f"{BASE}/msw/api/auth"
SESSION = f"{BASE}/msw/api/public/session"
USER_ITEMS = f"{BASE}/msw/api/account/getUserItemList"
OVERVIEW = f"{BASE}/msw/api/edv/getOverview"
DIAGRAM_CONFIG = f"{BASE}/msw/api/edv/getDiagramConfigById/TAF/55789/55789"
DIAGRAM_RESULT = respx.patterns.M(
    url__startswith=f"{BASE}/msw/api/edv/getDiagramResultList/gwa/55789/"
)


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("PLUSPORTAL_TENANT", "123456")
    monkeypatch.setenv("PLUSPORTAL_USERNAME", "1000000000")
    monkeypatch.setenv("PLUSPORTAL_PASSWORD", "s3cret")
    monkeypatch.setenv("PLUSPORTAL_NO_DOTENV", "1")


@pytest.fixture
def portal(session_payload):
    """Serve every call the CLI makes."""
    now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    session_payload["loginValidFrom"] = now_ms
    session_payload["loginValidTo"] = now_ms + 3_600_000

    with respx.mock(assert_all_called=False) as mock:
        mock.post(LOGIN).mock(return_value=httpx.Response(200))
        mock.get(SESSION).mock(return_value=httpx.Response(200, json=session_payload))
        mock.get(USER_ITEMS).mock(return_value=json_response("user_item_list.json"))
        mock.get(OVERVIEW).mock(return_value=json_response("overview.json"))
        mock.get(DIAGRAM_CONFIG).mock(return_value=json_response("diagram_config.json"))
        mock.route(DIAGRAM_RESULT).mock(return_value=json_response("diagram_result_july2026.json"))
        yield mock


# ------------------------------------------------------------------ meters


def test_meters_lists_the_metering_points(portal, credentials, capsys):
    assert main(["meters"]) == 0

    out = capsys.readouterr().out
    assert "1ABC0000000000*" in out
    assert "1000" in out
    assert "Electricity" in out


def test_meters_shows_the_tariff_use_case_that_will_be_used(portal, credentials, capsys):
    main(["meters"])

    assert "55789" in capsys.readouterr().out


# ---------------------------------------------------------------- overview


def test_overview_reports_the_month_totals(portal, credentials, capsys):
    assert main(["overview"]) == 0

    assert "0.757899" in capsys.readouterr().out


# ---------------------------------------------------------------- readings


def test_readings_render_as_a_table_by_default(portal, credentials, capsys):
    assert main(["readings", "--from", "2026-07-01", "--to", "2026-07-24"]) == 0

    out = capsys.readouterr().out
    assert "2026-07-01" in out
    assert "0.0312" in out
    assert "0.757899" in out, "the table must foot up to the billable total"


def test_readings_as_csv_are_machine_readable(portal, credentials, capsys):
    main(["readings", "--from", "2026-07-01", "--to", "2026-07-24", "--format", "csv"])

    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == "date,value,unit,obis,quality,billable"
    assert lines[1].startswith("2026-07-01,0.0312,kWh,1-0:1.8.0,W,")
    assert len(lines) == 25


def test_readings_as_json_keep_full_precision_as_strings(portal, credentials, capsys):
    main(["readings", "--from", "2026-07-01", "--to", "2026-07-24", "--format", "json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload[21]["value"] == "0.018321"
    assert payload[0]["start"] == "2026-07-01T00:00:00+02:00"
    assert payload[0]["billable"] is True


def test_an_inverted_range_is_reported_without_a_traceback(portal, credentials, capsys):
    assert main(["readings", "--from", "2026-07-24", "--to", "2026-07-01"]) == 2

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert captured.err.strip()


# ------------------------------------------------- configuration & failures


def test_missing_credentials_are_reported_clearly(monkeypatch, capsys):
    for name in ("PLUSPORTAL_USERNAME", "PLUSPORTAL_PASSWORD", "PLUSPORTAL_TENANT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PLUSPORTAL_NO_DOTENV", "1")

    assert main(["meters"]) == 2
    assert "PLUSPORTAL_USERNAME" in capsys.readouterr().err


def test_an_explicit_base_url_overrides_the_environment(portal, credentials, capsys):
    assert main(["--base-url", BASE, "meters"]) == 0
    assert "1ABC0000000000*" in capsys.readouterr().out


@respx.mock
def test_rejected_credentials_exit_with_a_message_not_a_traceback(credentials, capsys):
    respx.post(LOGIN).mock(return_value=httpx.Response(401))

    assert main(["meters"]) == 2

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "credentials" in captured.err.lower()


@respx.mock
def test_an_unreachable_portal_exits_distinctly(credentials, capsys):
    respx.post(LOGIN).mock(side_effect=httpx.ConnectError("no route"))

    assert main(["meters"]) == 3
    assert "Traceback" not in capsys.readouterr().err


def test_the_password_is_never_printed(portal, credentials, capsys):
    main(["meters"])
    main(["overview"])

    captured = capsys.readouterr()
    assert "s3cret" not in captured.out
    assert "s3cret" not in captured.err


# ----------------------------------------------------------------- .env


def test_env_file_parsing_handles_comments_quotes_and_exports(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "PLUSPORTAL_TENANT=123456",
                'PLUSPORTAL_PASSWORD="pa ss#word"',
                "export PLUSPORTAL_USERNAME='someone'",
                "  SPACED = value  ",
                "MALFORMED",
            ]
        )
    )

    assert parse_env_file(env) == {
        "PLUSPORTAL_TENANT": "123456",
        "PLUSPORTAL_PASSWORD": "pa ss#word",
        "PLUSPORTAL_USERNAME": "someone",
        "SPACED": "value",
    }


def test_a_missing_env_file_is_not_an_error(tmp_path):
    assert parse_env_file(tmp_path / "nope.env") == {}


def test_the_environment_wins_over_the_env_file(tmp_path, monkeypatch, portal, capsys):
    (tmp_path / ".env").write_text("PLUSPORTAL_USERNAME=from-file\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PLUSPORTAL_TENANT", "123456")
    monkeypatch.setenv("PLUSPORTAL_USERNAME", "from-env")
    monkeypatch.setenv("PLUSPORTAL_PASSWORD", "pw")

    assert main(["meters"]) == 0
    body = portal.calls[0].request.content.decode()
    assert "from-env" in body


# --------------------------------------------------------------- redaction


def test_redaction_removes_identifying_values():
    payload = {
        "sessionId": "deadbeef",
        "username": "1000000000",
        "userItems": [{"bez": "1XYZ9876543210*", "id": 1000}],
    }

    cleaned = redact(payload)

    assert cleaned["sessionId"] != "deadbeef"
    assert cleaned["username"] != "1000000000"
    assert cleaned["userItems"][0]["bez"] == "1ABC0000000000*"


def test_redaction_keeps_the_data_that_matters_for_fixtures():
    payload = {"values": [{"date": 1782856800000, "value": 0.0312, "state": "W"}]}

    assert redact(payload) == payload


def test_redaction_leaves_the_original_untouched():
    payload = {"sessionId": "deadbeef"}

    redact(payload)

    assert payload["sessionId"] == "deadbeef"


# ----------------------------------------------------- meter selection


def test_readings_can_be_restricted_to_one_metering_point(portal, credentials, capsys):
    args = ["readings", "--from", "2026-07-01", "--to", "2026-07-24", "--meter", "1000"]

    assert main(args) == 0
    assert "2026-07-01" in capsys.readouterr().out


def test_an_unknown_meter_id_is_reported_instead_of_returning_nothing(portal, credentials, capsys):
    """Silently printing an empty table would look like "no consumption"."""
    args = ["readings", "--from", "2026-07-01", "--to", "2026-07-24", "--meter", "999"]

    assert main(args) == 2
    assert "999" in capsys.readouterr().err


# ------------------------------------------------------------------ probe


def test_probe_writes_fixture_files(portal, credentials, tmp_path, capsys):
    assert main(["probe", "--out", str(tmp_path / "recorded")]) == 0

    written = sorted(p.name for p in (tmp_path / "recorded").glob("*.json"))
    assert written == ["overview.json", "session.json", "user_item_list.json"]


def test_probe_output_is_redacted_and_valid_json(portal, credentials, tmp_path):
    main(["probe", "--out", str(tmp_path)])

    session = json.loads((tmp_path / "session.json").read_text())
    meters = json.loads((tmp_path / "user_item_list.json").read_text())
    assert session["sessionId"] == "REDACTED"
    assert session["username"] == "REDACTED"
    assert meters[0]["userItems"][0]["bez"] == "1ABC0000000000*"


def test_probe_preserves_the_numbers_that_make_fixtures_useful(portal, credentials, tmp_path):
    main(["probe", "--out", str(tmp_path)])

    overview = json.loads((tmp_path / "overview.json").read_text())
    assert overview[0]["data"][0]["thisMonthSum"] == 0.757899


# ------------------------------------------------------------------- cost


@pytest.fixture
def tariff_env(monkeypatch):
    monkeypatch.setenv("PLUSPORTAL_ENERGY_PRICE_CT", "34.5")
    monkeypatch.setenv("PLUSPORTAL_BASE_PRICE_EUR", "120")
    monkeypatch.setenv("PLUSPORTAL_MONTHLY_ADVANCE_EUR", "50")


def test_cost_reports_the_billing_year_projection(portal, credentials, tariff_env, capsys):
    assert main(["cost", "--today", "2026-07-24"]) == 0

    out = capsys.readouterr().out
    assert "2026-01-01" in out and "2026-12-31" in out
    assert "kWh" in out
    assert "EUR" in out


def test_cost_shows_the_expected_settlement(portal, credentials, tariff_env, capsys):
    main(["cost", "--today", "2026-07-24"])

    out = capsys.readouterr().out
    assert "600.00" in out, "twelve monthly advances of 50 EUR"


def test_cost_without_a_tariff_explains_what_is_missing(portal, credentials, monkeypatch, capsys):
    monkeypatch.delenv("PLUSPORTAL_ENERGY_PRICE_CT", raising=False)

    assert main(["cost"]) == 2
    assert "PLUSPORTAL_ENERGY_PRICE_CT" in capsys.readouterr().err


def test_cost_reports_how_much_of_the_year_is_backed_by_data(
    portal, credentials, tariff_env, capsys
):
    main(["cost", "--today", "2026-07-24"])

    assert "%" in capsys.readouterr().out


def test_an_implausible_tariff_is_rejected_without_a_traceback(
    portal, credentials, tariff_env, monkeypatch, capsys
):
    monkeypatch.setenv("PLUSPORTAL_ENERGY_PRICE_CT", "-5")

    assert main(["cost"]) == 2
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "negative" in captured.err


def test_the_cost_table_has_no_empty_header_row(portal, credentials, tariff_env, capsys):
    main(["cost", "--today", "2026-07-24"])

    first_line = capsys.readouterr().out.splitlines()[0]
    assert not set(first_line.strip()) <= {"-", " "}, "a label/value table needs no header rule"


# ------------------------------------------------------------ discoverability


def test_bare_invocation_prints_help_and_exits_cleanly(capsys):
    assert main([]) == 0

    out = capsys.readouterr().out
    assert out.startswith("usage: pyplusportal")
    assert "meters" in out and "readings" in out and "cost" in out


def test_help_prints_the_same_full_help_as_a_bare_invocation(capsys):
    assert main([]) == 0
    bare_out = capsys.readouterr().out

    assert main(["help"]) == 0
    out = capsys.readouterr().out

    assert out == bare_out


def test_question_mark_prints_the_same_full_help_as_a_bare_invocation(capsys):
    assert main([]) == 0
    bare_out = capsys.readouterr().out

    assert main(["?"]) == 0
    out = capsys.readouterr().out

    assert out == bare_out


def test_help_with_a_command_shows_that_commands_own_help(capsys):
    assert main(["help", "readings"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("usage: pyplusportal readings")
    assert "--from" in out
    assert "--today" not in out


def test_question_mark_with_a_command_shows_that_commands_own_help(capsys):
    assert main(["?", "cost"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("usage: pyplusportal cost")
    assert "--today" in out


def _error_line(err: str) -> str:
    """Return the ``prog: error: ...`` line, not the usage line above it.

    The usage line already contains ``{meters,overview,readings,cost,probe}``,
    so asserting against the whole stderr stream would pass even if the
    error message itself were empty or wrong.
    """
    return next(line for line in err.splitlines() if ": error:" in line)


def test_help_with_an_unknown_topic_names_the_valid_commands(capsys):
    assert main(["help", "nonsense"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    err_line = _error_line(captured.err)
    assert "valid commands: meters, overview, readings, cost, probe" in err_line
    assert "Traceback" not in captured.err


def test_a_typo_command_suggests_the_closest_match(capsys):
    assert main(["meter"]) == 2

    err = capsys.readouterr().err
    err_line = _error_line(err)
    assert "did you mean 'meters'" in err_line
    assert "Traceback" not in err


def test_an_unrecognized_command_containing_an_apostrophe_gets_the_polished_wording(capsys):
    """Python's %r switches to double quotes when the value has an apostrophe."""
    assert main(["it's"]) == 2

    err = capsys.readouterr().err
    err_line = _error_line(err)
    assert 'unknown command "it\'s"' in err_line
    for name in ("meters", "overview", "readings", "cost", "probe"):
        assert name in err
    assert "Traceback" not in err


def test_an_unrecognized_command_with_no_close_match_lists_all_commands(capsys):
    assert main(["frobnicate"]) == 2

    err = capsys.readouterr().err
    err_line = _error_line(err)
    assert "did you mean" not in err_line
    assert "valid commands: meters, overview, readings, cost, probe" in err_line


def test_help_still_works_after_a_global_option(capsys):
    """A global option before the pseudo-command must not hide it.

    Every worked example in the CLI's own epilog leads with
    ``--tenant 123456``, so this exact form has to work.
    """
    assert main(["--tenant", "123456", "help", "cost"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("usage: pyplusportal cost")
    assert "--today" in out


def test_help_still_works_after_a_fused_global_option(capsys):
    assert main(["--tenant=123456", "help"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("usage: pyplusportal")
    assert "meters" in out and "readings" in out and "cost" in out


def test_a_typo_command_suggests_the_closest_match_after_a_global_option(capsys):
    assert main(["--tenant", "123456", "meter"]) == 2

    err_line = _error_line(capsys.readouterr().err)
    assert "did you mean 'meters'" in err_line


def test_help_and_question_mark_are_not_advertised_as_commands(capsys):
    main([])

    out = capsys.readouterr().out
    choices_lines = [line for line in out.splitlines() if "{" in line]
    assert choices_lines, "expected a line listing the sub-command choices"
    for line in choices_lines:
        assert "help" not in line
        assert "?" not in line


def test_the_help_text_notes_that_the_question_mark_may_need_quoting(capsys):
    main([])

    out = capsys.readouterr().out.lower()
    assert "?" in out and "quot" in out


# --------------------------------------------------------------- --version


def test_version_flag_prints_the_installed_version_and_exits_cleanly(capsys):
    from importlib.metadata import version as pkg_version

    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "pyplusportal" in out
    assert pkg_version("pyplusportal") in out


def test_the_short_version_flag_behaves_the_same(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["-V"])

    assert exc_info.value.code == 0
    assert "pyplusportal" in capsys.readouterr().out


def test_version_falls_back_to_unknown_if_the_package_is_not_installed(monkeypatch, capsys):
    from importlib.metadata import PackageNotFoundError

    def raise_not_found(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(cli_module, "version", raise_not_found)

    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    assert exc_info.value.code == 0
    assert "unknown" in capsys.readouterr().out


# ------------------------------------------------------- informative help


def test_help_mentions_every_documented_environment_variable(capsys):
    """A variable added later without a help entry must fail this test."""
    assert main([]) == 0
    out = capsys.readouterr().out

    env_vars = [value for name, value in vars(cli_module).items() if name.startswith("ENV_")]
    assert len(env_vars) >= 9, "expected the full set of PLUSPORTAL_* constants"
    for var in env_vars:
        assert var in out, f"{var} is missing from --help output"


def test_help_documents_dotenv_precedence(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out.lower()

    assert ".env" in out
    assert "precedence" in out or "wins" in out


def test_help_documents_the_exit_codes(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out.lower()

    assert "exit code" in out
    assert "usage" in out and "authentication" in out
    assert "unreachable" in out


def test_the_epilog_lists_examples_then_environment_variables_then_exit_codes(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out.lower()

    examples_at = out.index("example")
    env_at = out.index("environment variable")
    exit_at = out.index("exit code")

    assert examples_at < env_at < exit_at


def test_the_epilog_gives_a_worked_example_per_subcommand(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out

    for command in ("meters", "overview", "readings", "cost", "probe"):
        assert f"pyplusportal --tenant 123456 {command}" in out


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("meters", "channel"),
        ("overview", "dashboard"),
        ("readings", "consumption"),
        ("cost", "tariff"),
        ("probe", "fixture"),
    ],
)
def test_every_subcommand_help_has_a_useful_description(command, expected, capsys):
    assert main(["help", command]) == 0

    assert expected in capsys.readouterr().out.lower()


def test_cost_help_explains_that_the_portal_has_no_tariff_data(capsys):
    assert main(["help", "cost"]) == 0

    # Whitespace is normalized because the description is hand-wrapped for
    # width, which can put a line break between any two words.
    out = " ".join(capsys.readouterr().out.lower().split())
    assert "no tariff" in out


def test_readings_format_option_is_documented(capsys):
    assert main(["help", "readings"]) == 0
    out = capsys.readouterr().out.lower()

    assert "--format" in out
    assert "output format" in out
