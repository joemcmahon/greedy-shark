import pytest
from unittest.mock import patch, Mock
from notifier import FileNotifier, DiscordNotifier


def test_file_notifier_send_message(tmp_path):
    n = FileNotifier(tmp_path / "out.jsonl")
    n.send_message("Greedy Shark is active")
    records = n.get_records()
    assert records == [{"type": "message", "content": "Greedy Shark is active"}]


def test_file_notifier_send_alert_minimal(tmp_path):
    n = FileNotifier(tmp_path / "out.jsonl")
    n.send_alert("Stream silent for 2 minutes")
    records = n.get_records()
    assert len(records) == 1
    assert records[0]["type"] == "alert"
    assert records[0]["reason"] == "Stream silent for 2 minutes"
    assert records[0]["rms"] is None
    assert records[0]["variance"] is None
    assert records[0]["stderr"] == ""


def test_file_notifier_send_alert_with_rms(tmp_path):
    n = FileNotifier(tmp_path / "out.jsonl")
    n.send_alert("Silent", rms=0.0, variance=0.0)
    records = n.get_records()
    assert records[0]["rms"] == 0.0
    assert records[0]["variance"] == 0.0


def test_file_notifier_no_cooldown(tmp_path):
    n = FileNotifier(tmp_path / "out.jsonl")
    n.send_alert("first alert")
    n.send_alert("second alert")
    records = n.get_records()
    assert len(records) == 2


def test_file_notifier_multiple_types(tmp_path):
    n = FileNotifier(tmp_path / "out.jsonl")
    n.send_message("hello")
    n.send_alert("problem")
    records = n.get_records()
    assert records[0]["type"] == "message"
    assert records[1]["type"] == "alert"


def test_file_notifier_empty_returns_empty_list(tmp_path):
    n = FileNotifier(tmp_path / "out.jsonl")
    assert n.get_records() == []



@patch("notifier.requests.post")
def test_discord_notifier_sends_every_alert(mock_post):
    """Alert pacing is the monitor's job; the notifier must never drop an alert."""
    mock_post.return_value = Mock(status_code=204)
    n = DiscordNotifier("http://hook", "42")
    n.send_alert("first")
    n.send_alert("second")
    n.send_alert("third")
    assert mock_post.call_count == 3
