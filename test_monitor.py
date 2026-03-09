import pytest
import os
import time
from unittest.mock import Mock, patch, mock_open, MagicMock
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


class TestMonitorContext:
    """Test the MonitorContext class."""

    def test_initial_state(self):
        """Test that context initializes with correct defaults."""
        ctx = MonitorContext()
        assert ctx.state == MonitorState.NO_STREAMER
        assert ctx.consecutive_silent_checks == 0
        assert ctx.streamer_id is None
        assert ctx.streamer_name is None
        assert ctx.warning_sent is False

    def test_reset_counters(self):
        """Test that reset_counters clears counters and warning flag."""
        ctx = MonitorContext()
        ctx.consecutive_silent_checks = 5
        ctx.warning_sent = True

        ctx.reset_counters()

        assert ctx.consecutive_silent_checks == 0
        assert ctx.warning_sent is False

    def test_clear_streamer_info(self):
        """Test that clear_streamer_info removes streamer data."""
        ctx = MonitorContext()
        ctx.streamer_id = 123
        ctx.streamer_name = "TestDJ"

        ctx.clear_streamer_info()

        assert ctx.streamer_id is None
        assert ctx.streamer_name is None


class TestDetermineNextState:
    """Test state transition logic."""

    def test_no_transition_when_no_streamer_and_stays_no_streamer(self):
        """No transition when staying in NO_STREAMER state."""
        ctx = MonitorContext()
        ctx.state = MonitorState.NO_STREAMER

        result = determine_next_state(ctx, is_streamer_connected=False, grace_period_active=False)

        assert result is None

    def test_transition_from_no_streamer_to_streamer_active(self):
        """Transition when streamer connects without grace period."""
        ctx = MonitorContext()
        ctx.state = MonitorState.NO_STREAMER

        result = determine_next_state(ctx, is_streamer_connected=True, grace_period_active=False)

        assert result == MonitorState.STREAMER_ACTIVE

    def test_transition_from_no_streamer_to_grace_period(self):
        """Transition to grace period when streamer connects with active grace period."""
        ctx = MonitorContext()
        ctx.state = MonitorState.NO_STREAMER

        result = determine_next_state(ctx, is_streamer_connected=True, grace_period_active=True)

        assert result == MonitorState.GRACE_PERIOD

    def test_transition_from_streamer_active_to_grace_period(self):
        """Transition from active to grace period when grace period activated."""
        ctx = MonitorContext()
        ctx.state = MonitorState.STREAMER_ACTIVE

        result = determine_next_state(ctx, is_streamer_connected=True, grace_period_active=True)

        assert result == MonitorState.GRACE_PERIOD

    def test_transition_from_grace_period_to_streamer_active(self):
        """Transition back to active when grace period expires with streamer still connected."""
        ctx = MonitorContext()
        ctx.state = MonitorState.GRACE_PERIOD

        result = determine_next_state(ctx, is_streamer_connected=True, grace_period_active=False)

        assert result == MonitorState.STREAMER_ACTIVE

    def test_transition_from_grace_period_to_no_streamer(self):
        """Transition to no streamer when grace period expires and streamer disconnected."""
        ctx = MonitorContext()
        ctx.state = MonitorState.GRACE_PERIOD

        result = determine_next_state(ctx, is_streamer_connected=False, grace_period_active=False)

        assert result == MonitorState.NO_STREAMER

    def test_transition_from_streamer_active_to_no_streamer(self):
        """Transition when streamer disconnects."""
        ctx = MonitorContext()
        ctx.state = MonitorState.STREAMER_ACTIVE

        result = determine_next_state(ctx, is_streamer_connected=False, grace_period_active=False)

        assert result == MonitorState.NO_STREAMER


class TestHandleStateTransition:
    """Test state transition execution."""

    @patch('monitor_stream.send_discord_message')
    def test_transition_to_streamer_active_sets_streamer_info(self, mock_discord):
        """Test that transitioning to STREAMER_ACTIVE sets streamer info."""
        ctx = MonitorContext()
        ctx.state = MonitorState.NO_STREAMER

        handle_state_transition(ctx, MonitorState.STREAMER_ACTIVE, streamer_name="TestDJ", streamer_id=456)

        assert ctx.state == MonitorState.STREAMER_ACTIVE
        assert ctx.streamer_name == "TestDJ"
        assert ctx.streamer_id == 456
        assert ctx.consecutive_silent_checks == 0
        assert ctx.warning_sent is False

    @patch('monitor_stream.send_discord_message')
    def test_transition_to_grace_period_sends_message(self, mock_discord):
        """Test that entering grace period sends Discord message."""
        ctx = MonitorContext()
        ctx.state = MonitorState.STREAMER_ACTIVE
        ctx.streamer_name = "TestDJ"

        handle_state_transition(ctx, MonitorState.GRACE_PERIOD)

        assert ctx.state == MonitorState.GRACE_PERIOD
        mock_discord.assert_called_once()
        assert "Grace period activated" in mock_discord.call_args[0][0]

    @patch('monitor_stream.send_discord_message')
    def test_transition_to_no_streamer_clears_info(self, mock_discord):
        """Test that transitioning to NO_STREAMER clears streamer info."""
        ctx = MonitorContext()
        ctx.state = MonitorState.STREAMER_ACTIVE
        ctx.streamer_name = "TestDJ"
        ctx.streamer_id = 789

        handle_state_transition(ctx, MonitorState.NO_STREAMER)

        assert ctx.state == MonitorState.NO_STREAMER
        assert ctx.streamer_name is None
        assert ctx.streamer_id is None


class TestSilenceHandlers:
    """Test silence handling functions."""

    @patch('monitor_stream.send_discord_alert')
    def test_no_streamer_silence_at_threshold(self, mock_alert):
        """Test that alert is sent at 2-minute threshold."""
        ctx = MonitorContext()
        ctx.state = MonitorState.NO_STREAMER
        ctx.consecutive_silent_checks = SILENCE_ALERT_LEVEL

        handle_no_streamer_silence(ctx)

        mock_alert.assert_called_once()
        assert "2 minutes" in mock_alert.call_args[0][0]
        assert ctx.consecutive_silent_checks == 0

    @patch('monitor_stream.send_discord_alert')
    def test_no_streamer_silence_below_threshold(self, mock_alert):
        """Test that no alert is sent below threshold."""
        ctx = MonitorContext()
        ctx.state = MonitorState.NO_STREAMER
        ctx.consecutive_silent_checks = SILENCE_ALERT_LEVEL - 1

        handle_no_streamer_silence(ctx)

        mock_alert.assert_not_called()

    @patch('monitor_stream.send_discord_alert')
    def test_streamer_active_warning_at_8_minutes(self, mock_alert):
        """Test that warning is sent at 8-minute threshold."""
        ctx = MonitorContext()
        ctx.state = MonitorState.STREAMER_ACTIVE
        ctx.streamer_name = "TestDJ"
        ctx.consecutive_silent_checks = STREAMER_WARNING_THRESHOLD
        ctx.warning_sent = False

        handle_streamer_active_silence(ctx)

        mock_alert.assert_called_once()
        assert "imminent" in mock_alert.call_args[0][0]
        assert ctx.warning_sent is True

    @patch('monitor_stream.send_discord_alert')
    def test_streamer_active_warning_only_sent_once(self, mock_alert):
        """Test that warning is only sent once."""
        ctx = MonitorContext()
        ctx.state = MonitorState.STREAMER_ACTIVE
        ctx.streamer_name = "TestDJ"
        ctx.consecutive_silent_checks = STREAMER_WARNING_THRESHOLD
        ctx.warning_sent = True

        handle_streamer_active_silence(ctx)

        mock_alert.assert_not_called()

    @patch('monitor_stream.suspend_streamer')
    @patch('monitor_stream.send_discord_alert')
    def test_streamer_suspended_at_10_minutes(self, mock_alert, mock_suspend):
        """Test that streamer is suspended at 10-minute threshold."""
        mock_suspend.return_value = True

        ctx = MonitorContext()
        ctx.state = MonitorState.STREAMER_ACTIVE
        ctx.streamer_name = "TestDJ"
        ctx.streamer_id = 123
        ctx.consecutive_silent_checks = STREAMER_SUSPEND_THRESHOLD

        handle_streamer_active_silence(ctx)

        mock_suspend.assert_called_once_with(123)
        mock_alert.assert_called_once()
        assert "forced off" in mock_alert.call_args[0][0]
        assert ctx.state == MonitorState.NO_STREAMER
        assert ctx.streamer_id is None

    @patch('monitor_stream.suspend_streamer')
    @patch('monitor_stream.send_discord_alert')
    def test_streamer_suspension_failure_handled(self, mock_alert, mock_suspend):
        """Test that suspension failure sends appropriate alert."""
        mock_suspend.return_value = False

        ctx = MonitorContext()
        ctx.state = MonitorState.STREAMER_ACTIVE
        ctx.streamer_name = "TestDJ"
        ctx.streamer_id = 123
        ctx.consecutive_silent_checks = STREAMER_SUSPEND_THRESHOLD

        handle_streamer_active_silence(ctx)

        mock_alert.assert_called_once()
        assert "Failed to suspend" in mock_alert.call_args[0][0]


class TestHandleSilenceByState:
    """Test the silence handler router."""

    @patch('monitor_stream.handle_no_streamer_silence')
    def test_routes_to_no_streamer_handler(self, mock_handler):
        """Test that NO_STREAMER state routes correctly."""
        ctx = MonitorContext()
        ctx.state = MonitorState.NO_STREAMER

        handle_silence_by_state(ctx)

        mock_handler.assert_called_once_with(ctx)

    @patch('monitor_stream.handle_streamer_active_silence')
    def test_routes_to_streamer_active_handler(self, mock_handler):
        """Test that STREAMER_ACTIVE state routes correctly."""
        ctx = MonitorContext()
        ctx.state = MonitorState.STREAMER_ACTIVE

        handle_silence_by_state(ctx)

        mock_handler.assert_called_once_with(ctx)

    @patch('monitor_stream.handle_grace_period_silence')
    def test_routes_to_grace_period_handler(self, mock_handler):
        """Test that GRACE_PERIOD state routes correctly."""
        ctx = MonitorContext()
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

    @patch('monitor_stream.send_discord_message')
    def test_full_streamer_lifecycle(self, mock_discord):
        """Test complete streamer connection -> disconnection cycle."""
        ctx = MonitorContext()

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

    @patch('monitor_stream.send_discord_message')
    def test_grace_period_activation_and_expiration(self, mock_discord):
        """Test grace period activation and expiration."""
        ctx = MonitorContext()
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
