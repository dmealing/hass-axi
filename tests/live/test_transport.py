"""P6: transport and configuration faults, produced on loopback with a synthetic token.

Nothing here reaches the real installation: every fault is a stub server or an
address nothing listens on, and the token is one built at run time that no
server would accept. The installation never sees an invalid request.
"""

from __future__ import annotations

import time

import pytest

from . import stubs
from .harness import contract

COMMANDS = (["ping"], ["state", "list"], ["entity", "list"], ["doctor"])


def run(nowhere, url, *argv, **env):
    return nowhere.run(*argv, "--json", env={"HA_URL": url, **env})


@pytest.fixture
def stub():
    servers = []

    def make(behaviour, **options):
        server = stubs.Stub(behaviour, **options)
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.stop()


def test_nothing_configured_is_a_configuration_fault(nowhere):
    for unset, code in (
        (("HA_URL", "HA_TOKEN"), "NOT_CONFIGURED"),
        (("HA_TOKEN",), "NOT_CONFIGURED"),
        (("HA_URL",), "NOT_CONFIGURED"),
    ):
        result = nowhere.run("state", "list", "--json", unset=unset)
        document = contract(result, "json", code=code, exit_code=1)
        assert document["class"] == "config"
    home = nowhere.run("--json", unset=("HA_URL", "HA_TOKEN"))
    assert home.code == 0 and '"NOT_CONFIGURED"' in home.out


@pytest.mark.parametrize(
    "url",
    [
        "not a url",
        "http://",
        "ftp://example.invalid",
        "http://exa mple.invalid",
        "http://host:port",
    ],
)
def test_a_malformed_url_is_a_configuration_fault_on_both_transports(nowhere, url):
    for argv in (["ping"], ["entity", "list"]):
        document = contract(run(nowhere, url, *argv), "json", code="BAD_URL", exit_code=1)
        assert document["class"] == "config"


@pytest.mark.parametrize("token", ["has a space", "line\nbreak", "tab\there"])
def test_a_token_that_cannot_be_a_header_is_refused_before_it_is_sent(nowhere, stub, token):
    server = stub(stubs.status, code=200)
    result = nowhere.run("ping", "--json", env={"HA_URL": server.url, "HA_TOKEN": token})
    contract(result, "json", code="BAD_TOKEN", exit_code=1)
    assert server.seen == []


def test_a_closed_port_is_unreachable(nowhere):
    for argv in COMMANDS[:3]:
        document = contract(
            run(nowhere, "http://127.0.0.1:9", *argv), "json", code="UNREACHABLE", exit_code=1
        )
        assert document["class"] == "transport"


def test_a_name_that_does_not_resolve_is_unreachable(nowhere):
    result = run(nowhere, "http://no-such-host.invalid:8123", "ping")
    contract(result, "json", code="UNREACHABLE", exit_code=1)


def test_tls_to_a_plain_port_is_a_tls_fault(nowhere, stub):
    server = stub(stubs.status, code=200)
    result = run(nowhere, server.url.replace("http://", "https://"), "ping")
    contract(result, "json", code="TLS_ERROR", exit_code=1)


def test_a_server_that_never_answers_times_out_on_time(nowhere):
    hole = stubs.Blackhole()
    try:
        started = time.monotonic()
        result = nowhere.run("--timeout", "2", "ping", "--json", env={"HA_URL": hole.url})
        elapsed = time.monotonic() - started
    finally:
        hole.stop()
    contract(result, "json", code="TIMEOUT", exit_code=1)
    assert elapsed < 4, f"a 2 s timeout took {elapsed:.1f} s"


@pytest.mark.parametrize(
    ("status", "code", "fault_class"),
    [
        (401, "UNAUTHORIZED", "auth"),
        (403, "FORBIDDEN", "permission"),
        (404, "NOT_FOUND", "not_found"),
        (500, "SERVER_ERROR", "refused"),
        (502, "UNAVAILABLE", "transport"),
        (503, "UNAVAILABLE", "transport"),
        (504, "UNAVAILABLE", "transport"),
    ],
)
def test_each_status_maps_to_its_documented_code(nowhere, stub, status, code, fault_class):
    server = stub(stubs.status, code=status, body="" if status == 503 else None)
    document = contract(run(nowhere, server.url, "state", "list"), "json", code=code, exit_code=1)
    assert document["class"] == fault_class


@pytest.mark.parametrize(
    "behaviour", [stubs.html, stubs.empty_json, stubs.truncated_json, stubs.other_json]
)
def test_a_200_from_something_else_is_not_a_healthy_installation(nowhere, stub, behaviour):
    """A captive portal, a proxy's error page, another application on the port."""
    server = stub(behaviour)
    for argv in (["ping"], ["state", "list"], ["service", "list"]):
        document = contract(
            run(nowhere, server.url, *argv), "json", code="NOT_HOME_ASSISTANT", exit_code=1
        )
        assert document["class"] == "config"
    doctor = run(nowhere, server.url, "doctor")
    assert doctor.code == 1


def test_a_binary_answer_is_described_and_not_printed(nowhere, stub):
    server = stub(stubs.jpeg)
    result = run(nowhere, server.url, "api", "/camera_proxy/camera.example")
    assert result.code == 0
    assert "image/jpeg" in result.out and "�" not in result.out


def test_a_redirect_to_another_origin_is_refused_and_nothing_follows_it(nowhere, stub):
    elsewhere = stub(stubs.status, code=200)
    server = stub(stubs.redirect, to=elsewhere.url)
    document = contract(
        run(nowhere, server.url, "state", "list"), "json", code="REDIRECT_REFUSED", exit_code=1
    )
    assert document["class"] == "config"
    assert elsewhere.seen == [], "the token was carried to another origin"


def test_the_first_candidate_that_answers_is_used(nowhere, stub):
    server = stub(stubs.status, code=401)
    for urls in (
        f"http://127.0.0.1:9,{server.url}",
        f"{server.url},http://127.0.0.1:9",
        f" http://127.0.0.1:9 , {server.url} ",
    ):
        contract(run(nowhere, urls, "state", "list"), "json", code="UNAUTHORIZED", exit_code=1)
    contract(
        run(nowhere, "http://127.0.0.1:9,http://127.0.0.1:10", "state", "list"),
        "json",
        code="UNREACHABLE",
        exit_code=1,
    )


def test_url_userinfo_is_never_printed(nowhere, stub):
    server = stub(stubs.status, code=401)
    url = server.url.replace("http://", "http://someuser:somepassword1@")
    for argv in (["ping"], [], ["doctor"]):
        result = nowhere.run(*argv, env={"HA_URL": url})
        assert "somepassword1" not in result.out + result.err
        assert "someuser" not in result.out + result.err
