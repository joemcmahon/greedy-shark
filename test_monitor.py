import pytest
import os
import time
from unittest.mock import Mock, patch, mock_open, MagicMock
from notifier import FileNotifier
from monitor_stream import (
    MonitorState,
    MonitorContext,
    determine_next_state,
    handle_state_transition,
    handle_no_streamer_silence,
    handle_streamer_active_silence,
    handle_grace_period_silence,
    handle_silence_by_state,
    check_grace_period_active,
    SILENCE_ALERT_LEVEL,
    STREAMER_WARNING_THRESHOLD,
    STREAMER_SUSPEND_THRESHOLD
)


def make_ctx(notifier=None, azuracast=None, sample_fn=None):
    """Build a MonitorContext with sensible test defaults."""
    return MonitorContext(
        notifier=notifier or Mock(),
        azuracast=azuracast or Mock(),
        sample_fn=sample_fn or Mock(return_value=None),
    )


class TestMonitorContext:
    """Test the MonitorContext class."""

    def test_initial_state(self):
        """Test that context initializes with correct defaults."""
        ctx = make_ctx()
        assert ctx.state == MonitorState.NO_STREAMER
        assert ctx.consecutive_silent_checks == 0
        assert ctx.streamer_id is None
        assert ctx.streamer_name is None
        assert ctx.warning_sent is False

    def test_reset_counters(self):
        """Test that reset_counters clears counters and warning flag."""
        ctx = make_ctx()
        ctx.consecutive_silent_checks = 5
        ctx.warning_sent = True

        ctx.reset_counters()

        assert ctx.consecutive_silent_checks == 0
        assert ctx.warning_sent is False

    def test_clear_streamer_info(self):
        """Test that clear_streamer_info removes streamer data."""
        ctx = make_ctx()
        ctx.streamer_id = 123
        ctx.streamer_name = "TestDJ"

        ctx.clear_streamer_info()

        assert ctx.streamer_id is None
        assert ctx.streamer_name is None


class TestDetermineNextState:
    """Test state transition logic."""

    def test_no_transition_when_no_streamer_and_stays_no_streamer(self):
        """No transition when staying in NO_STREAMER state."""
        ctx = make_ctx()
        ctx.state = MonitorState.NO_STREAMER

        result = determine_next_state(ctx, is_streamer_connected=False, grace_period_active=False)

        assert result is None

    def test_transition_from_no_streamer_to_streamer_active(self):
        """Transition when streamer connects without grace period."""
        ctx = make_ctx()
        ctx.state = MonitorState.NO_STREAMER

        result = determine_next_state(ctx, is_streamer_connected=True, grace_period_active=False)

        assert result == MonitorState.STREAMER_ACTIVE

    def test_transition_from_no_streamer_to_grace_period(self):
        """Transition to grace period when streamer connects with active grace period."""
        ctx = make_ctx()
        ctx.state = MonitorState.NO_STREAMER

        result = determine_next_state(ctx, is_streamer_connected=True, grace_period_active=True)

        assert result == MonitorState.GRACE_PERIOD

    def test_transition_from_streamer_active_to_grace_period(self):
        """Transition from active to grace period when grace period activated."""
        ctx = make_ctx()
        ctx.state = MonitorState.STREAMER_ACTIVE

        result = determine_next_state(ctx, is_streamer_connected=True, grace_period_active=True)

        assert result == MonitorState.GRACE_PERIOD

    def test_transition_from_grace_period_to_streamer_active(self):
        """Transition back to active when grace period expires with streamer still connected."""
        ctx = make_ctx()
        ctx.state = MonitorState.GRACE_PERIOD

        result = determine_next_state(ctx, is_streamer_connected=True, grace_period_active=False)

        assert result == MonitorState.STREAMER_ACTIVE

    def test_transition_from_grace_period_to_no_streamer(self):
        """Transition to no streamer when grace period expires and streamer disconnected."""
        ctx = make_ctx()
        ctx.state = MonitorState.GRACE_PERIOD

        result = determine_next_state(ctx, is_streamer_connected=False, grace_period_active=False)

        assert result == MonitorState.NO_STREAMER

    def test_transition_from_streamer_active_to_no_streamer(self):
        """Transition when streamer disconnects."""
        ctx = make_ctx()
        ctx.state = MonitorState.STREAMER_ACTIVE

        result = determine_next_state(ctx, is_streamer_connected=False, grace_period_active=False)

        assert result == MonitorState.NO_STREAMER


class TestHandleStateTransition:
    """Test state transition execution."""

    def test_transition_to_streamer_active_sets_streamer_info(self, tmp_path):
        """Test that transitioning to STREAMER_ACTIVE sets streamer info."""
        notifier = FileNotifier(tmp_path / "out.jsonl")
        ctx = make_ctx(notifier=notifier)
        ctx.state = MonitorState.NO_STREAMER

        handle_state_transition(ctx, MonitorState.STREAMER_ACTIVE, streamer_name="TestDJ", streamer_id=456)

        assert ctx.state == MonitorState.STREAMER_ACTIVE
        assert ctx.streamer_name == "TestDJ"
        assert ctx.streamer_id == 456
        assert ctx.consecutive_silent_checks == 0
        assert ctx.warning_sent is False
        # No message should be sent for this transition
        records = notifier.get_records()
        assert len(records) == 0

    def test_transition_to_grace_period_sends_message(self, tmp_path):
        """Test that entering grace period sends Discord message."""
        notifier = FileNotifier(tmp_path / "out.jsonl")
        ctx = make_ctx(notifier=notifier)
        ctx.state = MonitorState.STREAMER_ACTIVE
        ctx.streamer_name = "TestDJ"

        handle_state_transition(ctx, MonitorState.GRACE_PERIOD)

        assert ctx.state == MonitorState.GRACE_PERIOD
        records = notifier.get_records()
        assert len(records) == 1
        assert records[0]["type"] == "message"
        assert "Grace period activated" in records[0]["content"]

    def test_transition_to_no_streamer_clears_info(self, tmp_path):
        """Test that transitioning to NO_STREAMER clears streamer info."""
        notifier = FileNotifier(tmp_path / "out.jsonl")
        ctx = make_ctx(notifier=notifier)
        ctx.state = MonitorState.STREAMER_ACTIVE
        ctx.streamer_name = "TestDJ"
        ctx.streamer_id = 789

        handle_state_transition(ctx, MonitorState.NO_STREAMER)

        assert ctx.state == MonitorState.NO_STREAMER
        assert ctx.streamer_name is None
        assert ctx.streamer_id is None


class TestSilenceHandlers:
    """Test silence handling functions."""

    def test_no_streamer_silence_at_threshold(self, tmp_path):
        """Test that alert is sent at 2-minute threshold."""
        notifier = FileNotifier(tmp_path / "out.jsonl")
        ctx = make_ctx(notifier=notifier)
        ctx.state = MonitorState.NO_STREAMER
        ctx.consecutive_silent_checks = SILENCE_ALERT_LEVEL

        handle_no_streamer_silence(ctx)

        records = notifier.get_records()
        assert len(records) == 1
        assert records[0]["type"] == "alert"
        assert "2 minutes" in records[0]["reason"]
        assert ctx.consecutive_silent_checks == 0

    def test_no_streamer_silence_below_threshold(self):
        """Test that no alert is sent below threshold."""
        mock_notifier = Mock()
        ctx = make_ctx(notifier=mock_notifier)
        ctx.state = MonitorState.NO_STREAMER
        ctx.consecutive_silent_checks = SILENCE_ALERT_LEVEL - 1

        handle_no_streamer_silence(ctx)

        mock_notifier.send_alert.assert_not_called()

    def test_streamer_active_warning_at_4_minutes(self, tmp_path):
        """Test that warning is sent at 4-minute threshold."""
        notifier = FileNotifier(tmp_path / "out.jsonl")
        ctx = make_ctx(notifier=notifier)
        ctx.state = MonitorState.STREAMER_ACTIVE
        ctx.streamer_name = "TestDJ"
        ctx.consecutive_silent_checks = STREAMER_WARNING_THRESHOLD
        ctx.warning_sent = False

        handle_streamer_active_silence(ctx)

        records = notifier.get_records()
        assert len(records) == 1
        assert records[0]["type"] == "alert"
        assert "Action may be needed soon" in records[0]["reason"]
        assert ctx.warning_sent is True

    def test_streamer_active_warning_only_sent_once(self):
        """Test that warning is only sent once."""
        mock_notifier = Mock()
        ctx = make_ctx(notifier=mock_notifier)
        ctx.state = MonitorState.STREAMER_ACTIVE
        ctx.streamer_name = "TestDJ"
        ctx.consecutive_silent_checks = STREAMER_WARNING_THRESHOLD
        ctx.warning_sent = True

        handle_streamer_active_silence(ctx)

        mock_notifier.send_alert.assert_not_called()

    def test_no_auto_suspend_at_10_minutes(self, tmp_path):
        """Test that streamer is NOT auto-suspended at 10-minute threshold."""
        notifier = FileNotifier(tmp_path / "out.jsonl")
        ctx = make_ctx(notifier=notifier)
        ctx.state = MonitorState.STREAMER_ACTIVE
        ctx.streamer_name = "TestDJ"
        ctx.streamer_id = 123
        ctx.consecutive_silent_checks = STREAMER_SUSPEND_THRESHOLD

        handle_streamer_active_silence(ctx)

        # State and streamer_id should be unchanged (no suspension)
        assert ctx.state == MonitorState.STREAMER_ACTIVE
        assert ctx.streamer_id == 123

    def test_urgent_alert_sent_at_10_minutes(self, tmp_path):
        """Test that an urgent alert is sent at 10-minute threshold."""
        notifier = FileNotifier(tmp_path / "out.jsonl")
        ctx = make_ctx(notifier=notifier)
        ctx.state = MonitorState.STREAMER_ACTIVE
        ctx.streamer_name = "TestDJ"
        ctx.streamer_id = 123
        ctx.consecutive_silent_checks = STREAMER_SUSPEND_THRESHOLD

        handle_streamer_active_silence(ctx)

        records = notifier.get_records()
        assert len(records) == 1
        assert records[0]["type"] == "alert"
        assert "TestDJ" in records[0]["reason"]

    def test_urgent_alert_repeats_after_10_minutes(self, tmp_path):
        """Test that urgent alert repeats on every check after 10 minutes."""
        notifier = FileNotifier(tmp_path / "out.jsonl")
        ctx = make_ctx(notifier=notifier)
        ctx.state = MonitorState.STREAMER_ACTIVE
        ctx.streamer_name = "TestDJ"
        ctx.streamer_id = 123
        ctx.consecutive_silent_checks = STREAMER_SUSPEND_THRESHOLD + 3

        handle_streamer_active_silence(ctx)

        records = notifier.get_records()
        assert len(records) == 1
        assert records[0]["type"] == "alert"
        assert "TestDJ" in records[0]["reason"]


class TestHandleSilenceByState:
    """Test the silence handler router."""

    @patch('monitor_stream.handle_no_streamer_silence')
    def test_routes_to_no_streamer_handler(self, mock_handler):
        """Test that NO_STREAMER state routes correctly."""
        ctx = make_ctx()
        ctx.state = MonitorState.NO_STREAMER

        handle_silence_by_state(ctx)

        mock_handler.assert_called_once_with(ctx)

    @patch('monitor_stream.handle_streamer_active_silence')
    def test_routes_to_streamer_active_handler(self, mock_handler):
        """Test that STREAMER_ACTIVE state routes correctly."""
        ctx = make_ctx()
        ctx.state = MonitorState.STREAMER_ACTIVE

        handle_silence_by_state(ctx)

        mock_handler.assert_called_once_with(ctx)

    @patch('monitor_stream.handle_grace_period_silence')
    def test_routes_to_grace_period_handler(self, mock_handler):
        """Test that GRACE_PERIOD state routes correctly."""
        ctx = make_ctx()
        ctx.state = MonitorState.GRACE_PERIOD

        handle_silence_by_state(ctx)

        mock_handler.assert_called_once_with(ctx)


class TestGracePeriod:
    """Test grace period file handling."""

    @patch('os.path.exists')
    def test_grace_period_not_active_when_file_missing(self, mock_exists):
        """Test that grace period is not active when file doesn't exist."""
        mock_exists.return_value = False

        result = check_grace_period_active()

        assert result is False

    @patch('builtins.open', new_callable=mock_open, read_data='9999999999.0')
    @patch('os.path.exists')
    @patch('time.time')
    def test_grace_period_active_when_not_expired(self, mock_time, mock_exists, mock_file):
        """Test that grace period is active when timestamp is in future."""
        mock_exists.return_value = True
        mock_time.return_value = 1000.0

        result = check_grace_period_active()

        assert result is True

    @patch('builtins.open', new_callable=mock_open, read_data='100.0')
    @patch('os.path.exists')
    @patch('os.remove')
    @patch('time.time')
    def test_grace_period_expired_file_removed(self, mock_time, mock_remove, mock_exists, mock_file):
        """Test that expired grace period file is cleaned up."""
        mock_exists.return_value = True
        mock_time.return_value = 9999999999.0

        result = check_grace_period_active()

        assert result is False
        mock_remove.assert_called_once()

    @patch('builtins.open', new_callable=mock_open, read_data='')
    @patch('os.path.exists')
    def test_grace_period_not_active_when_file_empty(self, mock_exists, mock_file):
        """Test that empty grace period file is treated as inactive."""
        mock_exists.return_value = True

        result = check_grace_period_active()

        assert result is False


class TestStateTransitionScenarios:
    """Integration-style tests for common state transition scenarios."""

    def test_full_streamer_lifecycle(self, tmp_path):
        """Test complete streamer connection -> disconnection cycle."""
        notifier = FileNotifier(tmp_path / "out.jsonl")
        ctx = make_ctx(notifier=notifier)

        # Initial state
        assert ctx.state == MonitorState.NO_STREAMER

        # Streamer connects
        new_state = determine_next_state(ctx, is_streamer_connected=True, grace_period_active=False)
        handle_state_transition(ctx, new_state, streamer_name="TestDJ", streamer_id=123)
        assert ctx.state == MonitorState.STREAMER_ACTIVE
        assert ctx.streamer_name == "TestDJ"

        # Streamer disconnects
        new_state = determine_next_state(ctx, is_streamer_connected=False, grace_period_active=False)
        handle_state_transition(ctx, new_state)
        assert ctx.state == MonitorState.NO_STREAMER
        assert ctx.streamer_name is None

    def test_grace_period_activation_and_expiration(self, tmp_path):
        """Test grace period activation and expiration."""
        notifier = FileNotifier(tmp_path / "out.jsonl")
        ctx = make_ctx(notifier=notifier)
        ctx.state = MonitorState.STREAMER_ACTIVE
        ctx.streamer_name = "TestDJ"

        # Grace period activated
        new_state = determine_next_state(ctx, is_streamer_connected=True, grace_period_active=True)
        handle_state_transition(ctx, new_state)
        assert ctx.state == MonitorState.GRACE_PERIOD

        # Grace period expires, streamer still connected
        new_state = determine_next_state(ctx, is_streamer_connected=True, grace_period_active=False)
        handle_state_transition(ctx, new_state)
        assert ctx.state == MonitorState.STREAMER_ACTIVE


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
