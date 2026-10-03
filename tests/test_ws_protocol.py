"""The WebSocket protocol paths that a happy-path suite never reaches.

These are the branches most likely to hang or mis-correlate against a live
instance: frames arriving out of band, a greeting that is not what the protocol
promises, and every way the socket can fail mid-exchange.
"""

from __future__ import annotations

import pytest

from conftest import FAKE_TOKEN
from hass_axi.config import load
from hass_axi.errors import AuthFailed, ConnectionFailed
from hass_axi.ws import MAX_FRAME_BYTES, WsClient


def client_for(ws_server):
    return WsClient(load({"HA_URL": f"http://127.0.0.1:{ws_server.port}", "HA_TOKEN": FAKE_TOKEN}))


def test_interleaved_event_frames_are_skipped_not_mistaken_for_the_result(ws_server):
    """The `continue` in send_command: a result must be matched by id, not by arrival."""
    ws_server.interleave = [
        {"id": 99, "type": "event", "event": {"event_type": "state_changed"}},
        {"id": 1, "type": "pong"},
        {"type": "event", "event": {"event_type": "noise"}},
    ]
    with client_for(ws_server) as client:
        areas = client.run("area.list")
    assert [a["area_id"] for a in areas] == ["example_room", "example_hall"]


def test_a_result_for_another_id_does_not_satisfy_this_command(ws_server):
    ws_server.interleave = [{"id": 42, "type": "result", "success": True, "result": ["wrong"]}]
    with client_for(ws_server) as client:
        areas = client.run("area.list")
    assert areas != ["wrong"]


def test_command_ids_increment_across_one_connection(ws_server):
    with client_for(ws_server) as client:
        client.run("area.list")
        client.run("entity.list")
    assert [c["id"] for c in ws_server.received] == [1, 2]


def test_an_unexpected_greeting_is_reported_not_hung_on(ws_server):
    ws_server.greeting = {"type": "hello", "ha_version": "2026.1.0"}
    with pytest.raises(ConnectionFailed) as caught:
        client_for(ws_server).connect()
    assert caught.value.code == "WS_PROTOCOL"
    assert "unexpected greeting" in caught.value.message


def test_an_unexpected_message_during_authentication_is_reported(ws_server):
    ws_server.mid_auth = {"type": "result", "success": True}
    with pytest.raises(ConnectionFailed) as caught:
        client_for(ws_server).connect()
    assert caught.value.code == "WS_PROTOCOL"
    assert "during authentication" in caught.value.message


def test_a_rejected_token_raises_auth_failed_and_never_echoes_it(ws_server):
    config = load({"HA_URL": f"http://127.0.0.1:{ws_server.port}", "HA_TOKEN": "wrong-token-value"})
    with pytest.raises(AuthFailed) as caught:
        WsClient(config).connect()
    assert "wrong-token-value" not in caught.value.message
    assert caught.value.code == "UNAUTHORIZED"


def test_the_socket_is_closed_when_authentication_fails(ws_server):
    """__exit__ never runs when __enter__ raises, so connect must clean up."""
    config = load({"HA_URL": f"http://127.0.0.1:{ws_server.port}", "HA_TOKEN": "wrong-token-value"})
    client = WsClient(config)
    with pytest.raises(AuthFailed):
        client.connect()
    assert client._socket is None


def test_a_non_json_frame_is_reported_as_a_protocol_error(ws_server):
    ws_server.send_garbage = True
    with client_for(ws_server) as client, pytest.raises(ConnectionFailed) as caught:
        client.run("area.list")
    assert caught.value.code == "WS_PROTOCOL"
    assert "not JSON" in caught.value.message


def test_a_socket_closed_mid_command_is_reported_as_closed(ws_server):
    ws_server.close_on_command = True
    with client_for(ws_server) as client, pytest.raises(ConnectionFailed) as caught:
        client.run("area.list")
    assert caught.value.code == "WS_CLOSED"


def test_sending_on_a_closed_socket_is_translated_not_raised_raw(ws_server):
    """The send side of the boundary: websockets' ConnectionClosed is not an OSError."""
    client = client_for(ws_server)
    client.connect()
    client._socket.close()
    with pytest.raises(ConnectionFailed) as caught:
        client.send_command("config/area_registry/list")
    assert caught.value.code == "WS_CLOSED"


def test_a_closed_socket_surfaces_through_the_cli_without_a_traceback(run_cli, ws_env, ws_server):
    ws_server.close_on_command = True
    code, out = run_cli(["entity", "list"], ws_env)
    assert code == 1
    assert "error: " in out
    assert "Traceback" not in out


def test_a_registry_larger_than_the_library_default_frame_still_arrives(ws_server):
    """MAX_FRAME_BYTES exists because real registries exceed 1 MiB; prove it works."""
    ws_server.entities = [
        {
            "entity_id": f"light.example_{index}",
            "name": f"Example {index}",
            "original_name": None,
            "area_id": None,
            "device_id": None,
            "platform": "demo",
            "unique_id": f"unique-{index}",
            "icon": None,
            "disabled_by": None,
            "hidden_by": None,
            "entity_category": None,
        }
        for index in range(6000)
    ]
    with client_for(ws_server) as client:
        entries = client.run("entity.list")
    assert len(entries) == 6000
    assert MAX_FRAME_BYTES > 1024 * 1024


def test_a_command_sent_without_connecting_first_connects_implicitly(ws_server):
    client = client_for(ws_server)
    try:
        assert client.send_command("config/area_registry/list") is not None
    finally:
        client.close()


def test_closing_twice_is_harmless(ws_server):
    client = client_for(ws_server)
    client.connect()
    client.close()
    client.close()
    assert client._socket is None


# ---------------------------------------------------------------------------
# The `websockets` API span. See `WsClient`'s docstring for the whole rule; the
# short version is that `connect()` must be *entered*, never assigned, because
# that is the only form correct across the declared `websockets>=13.0` range.
# 17.1 deprecated assigning it (and 17.1 wheels are Python >=3.11, which is why
# only the 3.11 and 3.12 legs of the matrix ever saw it), and a later release
# turns the deprecation into a different return type altogether.
# ---------------------------------------------------------------------------


def test_the_connection_is_entered_and_not_merely_assigned(ws_server, monkeypatch):
    """The defect itself, stated without reference to any `websockets` version.

    A release where `connect()` returns a context manager that is *not* the
    connection is exactly what 17.1 announced. Assigning the return value keeps
    hold of the wrapper, so this pins that the client keeps hold of what
    entering it yielded.
    """
    import websockets.sync.client as sync_client

    real_connect = sync_client.connect
    entered = []

    class Wrapper:
        """A context manager that is deliberately not its own connection."""

        def __init__(self, connection):
            self._connection = connection

        def __enter__(self):
            # Delegates, the way the real future `reconnect.__enter__` does:
            # the point is that the wrapper is not the connection, not that
            # entering it skips the library's own setup.
            connection = self._connection.__enter__()
            entered.append(connection)
            return connection

        def __exit__(self, *exc_info):
            return self._connection.__exit__(*exc_info)

    monkeypatch.setattr(
        sync_client, "connect", lambda *args, **kwargs: Wrapper(real_connect(*args, **kwargs))
    )

    client = client_for(ws_server)
    try:
        assert client.run("area.list") is not None
        assert entered, "connect()'s return value was never entered"
        assert client._socket is entered[0]
        assert not isinstance(client._socket, Wrapper)
    finally:
        client.close()
    assert client._socket is None


def test_the_websockets_client_is_driven_without_deprecation(ws_server):
    """No `DeprecationWarning` reaches a caller from an ordinary command.

    This is the failure as the matrix met it: 17.1 warns on the first `send` or
    `recv` of a connection that was never entered, and this project promotes
    that warning to an error, so every WebSocket test reported `WS_CLOSED`.
    """
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        with client_for(ws_server) as client:
            assert client.run("area.list") is not None


def test_the_client_works_against_the_future_websockets_api(ws_server, monkeypatch):
    """Not a mock: the real library, asked for the behaviour it is moving to.

    17.1 ships the post-deprecation behaviour behind `legacy=False`, where
    `connect()` returns a `reconnect` whose `__enter__` performs the connection.
    Running the real client against it now is what makes the forward-compatible
    claim a measurement rather than a promise. Older releases have no such
    parameter and nothing to prove, so they skip.
    """
    import inspect

    import websockets.sync.client as sync_client

    real_connect = sync_client.connect
    if "legacy" not in inspect.signature(real_connect).parameters:
        pytest.skip("this websockets release predates the `legacy` flag")

    monkeypatch.setattr(
        sync_client, "connect", lambda *args, **kwargs: real_connect(*args, legacy=False, **kwargs)
    )

    with client_for(ws_server) as client:
        areas = client.run("area.list")
    assert [a["area_id"] for a in areas] == ["example_room", "example_hall"]


def test_the_socket_is_closed_when_authentication_fails_against_the_future_api(
    ws_server, monkeypatch
):
    """The lifetime property, held on the return type this project has not met yet.

    `__exit__` never runs when `__enter__` raises, so a failure *after* the
    socket is open has to close it explicitly. That is what the `ExitStack`
    holds, and it has to hold on both return types rather than only the one
    installed here.
    """
    import inspect

    import websockets.sync.client as sync_client

    real_connect = sync_client.connect
    if "legacy" not in inspect.signature(real_connect).parameters:
        pytest.skip("this websockets release predates the `legacy` flag")

    monkeypatch.setattr(
        sync_client, "connect", lambda *args, **kwargs: real_connect(*args, legacy=False, **kwargs)
    )

    config = load({"HA_URL": f"http://127.0.0.1:{ws_server.port}", "HA_TOKEN": "wrong-token-value"})
    client = WsClient(config)
    with pytest.raises(AuthFailed):
        client.connect()
    assert client._socket is None
    assert client._open is None
