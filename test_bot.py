import json
import os
import time
import pytest
from unittest.mock import Mock
from grace_period_bot import (
    handle_shark,
    handle_unshark,
    handle_sharked,
    handle_streamers,
    handle_shark_status,
    handle_shark_help,
    handle_working_on_it,
    handle_cancel_grace,
    handle_grace_status,
    GRACE_PERIOD_MINUTES,
)


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


def mock_client(streamers=None, suspend_ok=True, reactivate_ok=True):
    c = Mock()
    c.get_all_streamers.return_value = streamers if streamers is not None else [streamer()]
    c.suspend_streamer.return_value = suspend_ok
    c.reactivate_streamer.return_value = reactivate_ok
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
