import json
import os
import time
import pytest
from unittest.mock import Mock, AsyncMock
from grace_period_bot import (
    handle_shark,
    handle_unshark,
    handle_sharked,
    handle_streamers,
    handle_retrack,
    handle_shark_status,
    handle_shark_help,
    handle_working_on_it,
    handle_cancel_grace,
    handle_grace_status,
    GRACE_PERIOD_MINUTES,
    with_station,
    tagged,
    with_streamer_station,
    streamer_tools,
)
from types import SimpleNamespace
import grace_period_bot
from grace_period_bot import working_on_it, cancel_grace, shark_status, streamers
from station import Station
from monitor_stream import load_auto_suspended_streamers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def capture():
    """Returns (send_fn, messages_list). send_fn is awaitable."""
    messages = []
    async def send_fn(msg):
        messages.append(msg)
    return send_fn, messages


def streamer(id=42, name="TestDJ", active=True):
    return {"id": id, "display_name": name, "is_active": active}


def mock_client(streamers=None, suspend_ok=True, reactivate_ok=True,
                is_live=False, live_sid=None, disconnect_ok=True):
    c = Mock()
    c.get_all_streamers.return_value = streamers if streamers is not None else [streamer()]
    c.suspend_streamer.return_value = suspend_ok
    c.reactivate_streamer.return_value = reactivate_ok
    c.check_streamer_connected.return_value = (is_live, "TestDJ" if is_live else None, live_sid)
    c.disconnect_streamer.return_value = disconnect_ok
    return c


# ---------------------------------------------------------------------------
# handle_shark_help
# ---------------------------------------------------------------------------

class TestHandleSharkHelp:
    async def test_contains_key_commands(self):
        send_fn, msgs = await capture()
        await handle_shark_help(send_fn)
        assert len(msgs) == 1
        assert "!shark" in msgs[0]
        assert "!unshark" in msgs[0]
        assert "!streamers" in msgs[0]

    async def test_includes_grace_minutes(self):
        send_fn, msgs = await capture()
        await handle_shark_help(send_fn, grace_minutes=30)
        assert "30" in msgs[0]


# ---------------------------------------------------------------------------
# handle_shark
# ---------------------------------------------------------------------------

class TestHandleShark:
    async def test_missing_id_sends_error(self):
        send_fn, msgs = await capture()
        await handle_shark(None, send_fn, mock_client())
        assert "provide a streamer ID" in msgs[0]

    async def test_non_numeric_id_sends_error(self):
        send_fn, msgs = await capture()
        await handle_shark("abc", send_fn, mock_client())
        assert "not a valid ID" in msgs[0]

    async def test_api_failure_sends_error(self):
        send_fn, msgs = await capture()
        client = mock_client()
        client.get_all_streamers.return_value = None
        await handle_shark("42", send_fn, client)
        assert "Failed to fetch" in msgs[0]

    async def test_unknown_id_sends_error(self):
        send_fn, msgs = await capture()
        client = mock_client(streamers=[streamer(id=99)])
        await handle_shark("42", send_fn, client)
        assert "No streamer found" in msgs[0]

    async def test_already_suspended_sends_info(self):
        send_fn, msgs = await capture()
        client = mock_client(streamers=[streamer(active=False)])
        await handle_shark("42", send_fn, client)
        assert "already suspended" in msgs[0]

    async def test_happy_path_suspends_and_tracks(self):
        send_fn, msgs = await capture()
        track_fn = Mock()
        client = mock_client()
        await handle_shark("42", send_fn, client, track_fn=track_fn, author_name="Admin")
        assert "TestDJ" in msgs[0]
        assert "suspended" in msgs[0]
        assert "Admin" in msgs[0]
        client.suspend_streamer.assert_called_once_with(42)
        track_fn.assert_called_once_with(42, "TestDJ", reason="staff action via !shark")

    async def test_api_suspend_failure_sends_error(self):
        send_fn, msgs = await capture()
        client = mock_client(suspend_ok=False)
        await handle_shark("42", send_fn, client)
        assert "Failed to suspend" in msgs[0]

    async def test_disconnects_live_streamer_after_suspend(self):
        send_fn, msgs = await capture()
        client = mock_client(is_live=True, live_sid=42)
        await handle_shark("42", send_fn, client, track_fn=Mock())
        client.disconnect_streamer.assert_called_once_with(42)
        assert "disconnected" in msgs[0]

    async def test_no_disconnect_when_streamer_not_live(self):
        send_fn, msgs = await capture()
        client = mock_client(is_live=False)
        await handle_shark("42", send_fn, client, track_fn=Mock())
        client.disconnect_streamer.assert_not_called()

    async def test_no_disconnect_when_different_streamer_is_live(self):
        send_fn, msgs = await capture()
        client = mock_client(is_live=True, live_sid=99)
        await handle_shark("42", send_fn, client, track_fn=Mock())
        client.disconnect_streamer.assert_not_called()

    async def test_disconnect_failure_notes_in_message(self):
        send_fn, msgs = await capture()
        client = mock_client(is_live=True, live_sid=42, disconnect_ok=False)
        await handle_shark("42", send_fn, client, track_fn=Mock())
        assert "suspended" in msgs[0]
        assert "disconnect" in msgs[0].lower()


# ---------------------------------------------------------------------------
# handle_unshark
# ---------------------------------------------------------------------------

class TestHandleUnshark:
    async def test_missing_id_sends_error(self):
        send_fn, msgs = await capture()
        await handle_unshark(None, send_fn, mock_client())
        assert "provide a streamer ID" in msgs[0]

    async def test_non_numeric_id_sends_error(self):
        send_fn, msgs = await capture()
        await handle_unshark("abc", send_fn, mock_client())
        assert "not a valid ID" in msgs[0]

    async def test_api_failure_sends_error(self):
        send_fn, msgs = await capture()
        client = mock_client()
        client.get_all_streamers.return_value = None
        await handle_unshark("42", send_fn, client)
        assert "Failed to fetch" in msgs[0]

    async def test_unknown_id_sends_error(self):
        send_fn, msgs = await capture()
        client = mock_client(streamers=[streamer(id=99)])
        await handle_unshark("42", send_fn, client)
        assert "No streamer found" in msgs[0]

    async def test_already_active_sends_info(self):
        send_fn, msgs = await capture()
        client = mock_client(streamers=[streamer(active=True)])
        await handle_unshark("42", send_fn, client)
        assert "not currently suspended" in msgs[0]

    async def test_happy_path_reactivates_and_untracks(self):
        send_fn, msgs = await capture()
        untrack_fn = Mock()
        client = mock_client(streamers=[streamer(active=False)])
        await handle_unshark("42", send_fn, client, untrack_fn=untrack_fn)
        assert "re-enabled" in msgs[0]
        assert "TestDJ" in msgs[0]
        client.reactivate_streamer.assert_called_once_with(42)
        untrack_fn.assert_called_once_with(42)

    async def test_api_reactivate_failure_sends_error(self):
        send_fn, msgs = await capture()
        client = mock_client(streamers=[streamer(active=False)], reactivate_ok=False)
        await handle_unshark("42", send_fn, client)
        assert "Failed to re-enable" in msgs[0]


# ---------------------------------------------------------------------------
# handle_retrack
# ---------------------------------------------------------------------------

class TestHandleRetrack:
    async def test_missing_id_sends_error(self):
        send_fn, msgs = await capture()
        await handle_retrack(None, send_fn, mock_client())
        assert "provide a streamer ID" in msgs[0]

    async def test_non_numeric_id_sends_error(self):
        send_fn, msgs = await capture()
        await handle_retrack("abc", send_fn, mock_client())
        assert "not a valid ID" in msgs[0]

    async def test_api_failure_sends_error(self):
        send_fn, msgs = await capture()
        client = mock_client()
        client.get_all_streamers.return_value = None
        await handle_retrack("42", send_fn, client)
        assert "Failed to fetch" in msgs[0]

    async def test_unknown_id_sends_error(self):
        send_fn, msgs = await capture()
        client = mock_client(streamers=[streamer(id=99)])
        await handle_retrack("42", send_fn, client)
        assert "No streamer found" in msgs[0]

    async def test_active_streamer_sends_error(self):
        send_fn, msgs = await capture()
        client = mock_client(streamers=[streamer(active=True)])
        await handle_retrack("42", send_fn, client)
        assert "not suspended" in msgs[0]
        assert "!shark" in msgs[0]

    async def test_happy_path_tracks_suspended_streamer(self):
        send_fn, msgs = await capture()
        track_fn = Mock()
        client = mock_client(streamers=[streamer(active=False)])
        await handle_retrack("42", send_fn, client, track_fn=track_fn, author_name="Admin")
        track_fn.assert_called_once_with(42, "TestDJ", reason="manual retrack via !retrack")
        assert "TestDJ" in msgs[0]
        assert "tracked" in msgs[0].lower()
        assert "Admin" in msgs[0]

    async def test_exception_sends_error(self):
        send_fn, msgs = await capture()
        client = mock_client()
        client.get_all_streamers.side_effect = RuntimeError("boom")
        await handle_retrack("42", send_fn, client)
        assert "Error" in msgs[0]


# ---------------------------------------------------------------------------
# handle_sharked
# ---------------------------------------------------------------------------

class TestHandleSharked:
    async def test_no_suspended_streamers(self):
        send_fn, msgs = await capture()
        await handle_sharked(send_fn, load_fn=lambda: {})
        assert "All clear" in msgs[0]

    async def test_lists_suspended_streamers(self):
        send_fn, msgs = await capture()
        data = {"42": {"name": "TestDJ", "suspended_at": "2026-06-10T12:00:00",
                        "reason": "staff action via !shark"}}
        await handle_sharked(send_fn, load_fn=lambda: data)
        assert "TestDJ" in msgs[0]
        assert "42" in msgs[0]
        assert "!unshark" in msgs[0]


# ---------------------------------------------------------------------------
# handle_streamers
# ---------------------------------------------------------------------------

class TestHandleStreamers:
    async def test_api_failure(self):
        send_fn, msgs = await capture()
        client = mock_client()
        client.get_all_streamers.return_value = None
        await handle_streamers(send_fn, client)
        assert "Failed to fetch" in msgs[0]

    async def test_empty_list(self):
        send_fn, msgs = await capture()
        client = mock_client(streamers=[])
        await handle_streamers(send_fn, client)
        assert "No streamers found" in msgs[0]

    async def test_lists_streamers_with_status(self):
        send_fn, msgs = await capture()
        client = mock_client(streamers=[
            streamer(id=1, name="AlphaDJ", active=True),
            streamer(id=2, name="BetaDJ", active=False),
        ])
        await handle_streamers(send_fn, client)
        assert "AlphaDJ" in msgs[0]
        assert "BetaDJ" in msgs[0]
        assert "active" in msgs[0]
        assert "suspended" in msgs[0]


# ---------------------------------------------------------------------------
# handle_working_on_it
# ---------------------------------------------------------------------------

class TestHandleWorkingOnIt:
    async def test_writes_grace_file_and_confirms(self, tmp_path):
        send_fn, msgs = await capture()
        grace_file = str(tmp_path / "grace")
        await handle_working_on_it(send_fn, grace_file=grace_file, grace_minutes=15)
        assert "Grace period activated" in msgs[0]
        assert os.path.exists(grace_file)
        ts = float(open(grace_file).read().strip())
        assert ts > time.time()

    async def test_includes_configured_duration(self, tmp_path):
        send_fn, msgs = await capture()
        grace_file = str(tmp_path / "grace")
        await handle_working_on_it(send_fn, grace_file=grace_file, grace_minutes=30)
        assert "30" in msgs[0]


# ---------------------------------------------------------------------------
# handle_cancel_grace
# ---------------------------------------------------------------------------

class TestHandleCancelGrace:
    async def test_cancels_active_grace_period(self, tmp_path):
        send_fn, msgs = await capture()
        grace_file = str(tmp_path / "grace")
        open(grace_file, 'w').write("9999999999.0")
        await handle_cancel_grace(send_fn, grace_file=grace_file)
        assert "cancelled" in msgs[0]
        assert not os.path.exists(grace_file)

    async def test_no_grace_period_to_cancel(self, tmp_path):
        send_fn, msgs = await capture()
        grace_file = str(tmp_path / "grace")
        await handle_cancel_grace(send_fn, grace_file=grace_file)
        assert "No active grace period" in msgs[0]


# ---------------------------------------------------------------------------
# handle_grace_status
# ---------------------------------------------------------------------------

class TestHandleGraceStatus:
    async def test_no_file_means_inactive(self, tmp_path):
        send_fn, msgs = await capture()
        await handle_grace_status(send_fn, grace_file=str(tmp_path / "grace"))
        assert "No active grace period" in msgs[0]

    async def test_empty_file_means_inactive(self, tmp_path):
        send_fn, msgs = await capture()
        grace_file = str(tmp_path / "grace")
        open(grace_file, 'w').close()
        await handle_grace_status(send_fn, grace_file=grace_file)
        assert "No active grace period" in msgs[0]

    async def test_future_timestamp_shows_remaining_time(self, tmp_path):
        send_fn, msgs = await capture()
        grace_file = str(tmp_path / "grace")
        future = time.time() + 600  # 10 minutes from now
        open(grace_file, 'w').write(str(future))
        await handle_grace_status(send_fn, grace_file=grace_file)
        assert "Grace period active" in msgs[0]
        assert "Expires in" in msgs[0]

    async def test_past_timestamp_shows_expired(self, tmp_path):
        send_fn, msgs = await capture()
        grace_file = str(tmp_path / "grace")
        open(grace_file, 'w').write("1.0")
        await handle_grace_status(send_fn, grace_file=grace_file)
        assert "expired" in msgs[0]


# ---------------------------------------------------------------------------
# handle_shark_status
# ---------------------------------------------------------------------------

class TestHandleSharkStatus:
    async def test_no_state_file(self, tmp_path):
        send_fn, msgs = await capture()
        await handle_shark_status(
            send_fn, mock_client(),
            load_fn=lambda: {},
            state_file=str(tmp_path / "state"),
            grace_file=str(tmp_path / "grace"),
        )
        assert "Monitor state not available" in msgs[0]

    async def test_no_streamer_state_no_silence(self, tmp_path):
        send_fn, msgs = await capture()
        state_file = str(tmp_path / "state")
        json.dump({"state": "no_streamer", "consecutive_silent_checks": 0,
                   "streamer_name": ""}, open(state_file, 'w'))
        await handle_shark_status(
            send_fn, mock_client(),
            load_fn=lambda: {},
            state_file=state_file,
            grace_file=str(tmp_path / "grace"),
        )
        assert "No silence detected" in msgs[0]

    async def test_streamer_active_with_silence(self, tmp_path):
        send_fn, msgs = await capture()
        state_file = str(tmp_path / "state")
        json.dump({"state": "streamer_active", "consecutive_silent_checks": 5,
                   "streamer_name": "TestDJ"}, open(state_file, 'w'))
        await handle_shark_status(
            send_fn, mock_client(),
            load_fn=lambda: {},
            state_file=state_file,
            grace_file=str(tmp_path / "grace"),
        )
        assert "TestDJ" in msgs[0]
        assert "5" in msgs[0]

    async def test_grace_with_streamer_wording_unchanged(self, tmp_path):
        send_fn, msgs = await capture()
        state_file = str(tmp_path / "state")
        grace_file = str(tmp_path / "grace")
        json.dump({"state": "grace_period", "consecutive_silent_checks": 0,
                   "streamer_name": "TestDJ"}, open(state_file, 'w'))
        open(grace_file, 'w').write(str(time.time() + 600))
        await handle_shark_status(send_fn, mock_client(), load_fn=lambda: {},
                                  state_file=state_file, grace_file=grace_file)
        assert "**TestDJ** streaming, 9 minutes into grace period" in msgs[0]

    async def test_grace_without_streamer_reports_grace(self, tmp_path):
        send_fn, msgs = await capture()
        state_file = str(tmp_path / "state")
        grace_file = str(tmp_path / "grace")
        json.dump({"state": "grace_period", "consecutive_silent_checks": 3,
                   "streamer_name": ""}, open(state_file, 'w'))
        open(grace_file, 'w').write(str(time.time() + 600))
        await handle_shark_status(send_fn, mock_client(), load_fn=lambda: {},
                                  state_file=state_file, grace_file=grace_file)
        assert "Grace period active, 9 minutes remaining" in msgs[0]
        assert "unknown" not in msgs[0]

    async def test_suspended_users_listed(self, tmp_path):
        send_fn, msgs = await capture()
        state_file = str(tmp_path / "state")
        json.dump({"state": "no_streamer", "consecutive_silent_checks": 0,
                   "streamer_name": ""}, open(state_file, 'w'))
        suspended = {"42": {"name": "TestDJ", "suspended_at": "2026-06-10T12:00:00",
                             "reason": "staff action"}}
        await handle_shark_status(
            send_fn, mock_client(),
            load_fn=lambda: suspended,
            state_file=state_file,
            grace_file=str(tmp_path / "grace"),
        )
        assert "TestDJ" in msgs[0]
        assert "1 user suspended" in msgs[0]


# ---------------------------------------------------------------------------
# Multi-station: with_station, tagging, per-station files
# ---------------------------------------------------------------------------

def two_stations(tmp_path):
    a = Station("pulse", str(tmp_path / "pulse"), "1")
    b = Station("inner", str(tmp_path / "inner"), "2")
    for st in (a, b):
        os.makedirs(st.state_dir)
    return a, b


class TestWithStation:
    async def test_with_station_refuses_none_and_lists_names(self, tmp_path):
        stations = [Station("radiospiral", str(tmp_path), "1"),
                    Station("pulse", str(tmp_path), "2")]
        send_fn, msgs = await capture()
        action = AsyncMock()
        await with_station(None, send_fn, stations, action)
        assert len(msgs) == 1
        assert "radiospiral, pulse" in msgs[0]
        assert "!woi pulse" in msgs[0]
        action.assert_not_awaited()

    async def test_with_station_refusal_example_uses_typed_command(self, tmp_path):
        stations = [Station("radiospiral", str(tmp_path), "1"),
                    Station("pulse", str(tmp_path), "2")]
        send_fn, msgs = await capture()
        await with_station(None, send_fn, stations, AsyncMock(), command="cg")
        assert "`!cg pulse`" in msgs[0]
        assert "!woi" not in msgs[0]

    async def test_with_station_refuses_unknown_naming_it(self, tmp_path):
        stations = [Station("radiospiral", str(tmp_path), "1"),
                    Station("pulse", str(tmp_path), "2")]
        send_fn, msgs = await capture()
        action = AsyncMock()
        await with_station("pulze", send_fn, stations, action)
        assert len(msgs) == 1
        assert "pulze" in msgs[0]
        assert "radiospiral, pulse" in msgs[0]
        action.assert_not_awaited()

    async def test_with_station_is_case_insensitive(self, tmp_path):
        pulse = Station("pulse", str(tmp_path), "2")
        stations = [Station("radiospiral", str(tmp_path), "1"), pulse]
        send_fn, msgs = await capture()
        action = AsyncMock()
        await with_station("PULSE", send_fn, stations, action)
        action.assert_awaited_once_with(pulse)
        assert msgs == []


class TestPerStationGrace:
    async def test_woi_on_one_station_leaves_others_alone(self, tmp_path):
        a, b = two_stations(tmp_path)
        send_fn, msgs = await capture()
        await handle_working_on_it(send_fn, grace_file=a.paths.grace,
                                   station_name="pulse")
        assert os.path.exists(a.paths.grace)
        assert not os.path.exists(b.paths.grace)

        send_fn, msgs = await capture()
        await handle_grace_status(send_fn, grace_file=b.paths.grace,
                                  station_name="inner")
        assert "No active grace period" in msgs[0]

        send_fn, msgs = await capture()
        await handle_cancel_grace(send_fn, grace_file=b.paths.grace,
                                  station_name="inner")
        assert "No active grace period to cancel" in msgs[0]
        assert os.path.exists(a.paths.grace)

    async def test_handlers_tag_messages_with_station_name(self, tmp_path):
        grace = str(tmp_path / "grace")
        send_fn, msgs = await capture()
        await handle_working_on_it(send_fn, grace_file=grace, station_name="pulse")
        assert msgs[0].startswith("[pulse] ")
        send_fn, msgs = await capture()
        await handle_grace_status(send_fn, grace_file=grace, station_name="pulse")
        assert msgs[0].startswith("[pulse] ")
        send_fn, msgs = await capture()
        await handle_cancel_grace(send_fn, grace_file=grace, station_name="pulse")
        assert msgs[0].startswith("[pulse] ")
        send_fn, msgs = await capture()
        await handle_shark_status(send_fn, mock_client(),
                                  load_fn=lambda: {},
                                  state_file=str(tmp_path / "nostate"),
                                  grace_file=grace, station_name="pulse")
        assert msgs[0].startswith("[pulse] ")

    async def test_untagged_by_default(self):
        assert tagged("hi") == "hi"
        assert tagged("hi", "pulse") == "[pulse] hi"


class TestCommandWrappers:
    """Call the real command callbacks with a fake ctx."""

    @pytest.fixture
    def wired(self, tmp_path, monkeypatch):
        a, b = two_stations(tmp_path)
        monkeypatch.setattr(grace_period_bot, "STATIONS", [a, b])
        monkeypatch.setattr(grace_period_bot, "DISCORD_CHANNEL_ID", 99)
        msgs = []

        async def send(m):
            msgs.append(m)
        ctx = SimpleNamespace(send=send, channel=SimpleNamespace(id=99),
                              invoked_with="woi",
                              author=SimpleNamespace(display_name="Joe"))
        return a, b, ctx, msgs

    async def test_woi_writes_only_the_named_station(self, wired):
        a, b, ctx, msgs = wired
        await working_on_it.callback(ctx, "pulse")
        assert os.path.exists(a.paths.grace)
        assert not os.path.exists(b.paths.grace)
        assert msgs[0].startswith("[pulse] ")

    async def test_woi_unknown_or_missing_station_writes_nothing(self, wired):
        a, b, ctx, msgs = wired
        await working_on_it.callback(ctx, "nope")
        await working_on_it.callback(ctx, None)
        assert not os.path.exists(a.paths.grace)
        assert not os.path.exists(b.paths.grace)
        assert len(msgs) == 2

    async def test_refusal_example_matches_typed_alias(self, wired):
        a, b, ctx, msgs = wired
        ctx.invoked_with = "cg"
        await cancel_grace.callback(ctx, None)
        assert "`!cg inner`" in msgs[0]
        assert "!woi" not in msgs[0]

    async def test_wrong_channel_is_ignored(self, wired):
        a, b, ctx, msgs = wired
        ctx.channel.id = 1
        await working_on_it.callback(ctx, "pulse")
        assert msgs == []
        assert not os.path.exists(a.paths.grace)

    async def test_streamer_wrapper_routes_to_streamers_station(self, wired, monkeypatch):
        a, b, ctx, msgs = wired
        c = Station("inner", b.state_dir, "2", streamers_enabled=True)
        monkeypatch.setattr(grace_period_bot, "STATIONS", [a, c])
        seen = []

        async def fake_handle(send_fn, client):
            seen.append(client.station_id)
        monkeypatch.setattr(grace_period_bot, "handle_streamers", fake_handle)
        await streamers.callback(ctx)
        assert seen == ["2"]

    async def test_streamer_wrapper_refuses_without_streamers_station(self, wired):
        a, b, ctx, msgs = wired
        await streamers.callback(ctx)
        assert "streamers" in msgs[0]


# ---------------------------------------------------------------------------
# Multi-station: streamer commands route to the streamers station; help
# ---------------------------------------------------------------------------

class TestStreamerRouting:
    async def test_with_streamer_station_none_configured(self, tmp_path):
        a, b = two_stations(tmp_path)
        send_fn, msgs = await capture()
        action = AsyncMock()
        await with_streamer_station(send_fn, [a, b], action)
        assert len(msgs) == 1
        assert "streamers" in msgs[0]
        action.assert_not_awaited()

    async def test_with_streamer_station_routes_to_streamer_station(self, tmp_path):
        a, _ = two_stations(tmp_path)
        b = Station("inner", str(tmp_path / "inner"), "2", streamers_enabled=True)
        send_fn, msgs = await capture()
        action = AsyncMock()
        await with_streamer_station(send_fn, [a, b], action)
        action.assert_awaited_once_with(b)
        assert msgs == []

    def test_streamer_tools_write_only_to_that_stations_dir(self, tmp_path):
        a, b = two_stations(tmp_path)
        t = streamer_tools(a, "http://x", "k")
        t.track_fn(7, "DJ", reason="r")
        assert "7" in t.load_fn()
        assert load_auto_suspended_streamers(path=b.paths.suspended) == {}
        t.untrack_fn(7)
        assert t.load_fn() == {}

    def test_streamer_tools_client_uses_station_id(self, tmp_path):
        a, _ = two_stations(tmp_path)
        t = streamer_tools(a, "http://x", "k")
        assert t.client.station_id == a.azuracast_station_id

    async def test_help_lists_stations_and_station_argument(self):
        send_fn, msgs = await capture()
        await handle_shark_help(send_fn, station_names="radiospiral, pulse")
        assert "radiospiral, pulse" in msgs[0]
        assert "!woi <station>" in msgs[0]
