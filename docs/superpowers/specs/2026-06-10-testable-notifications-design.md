# Testable Notifications: Dependency Injection Refactor

**Date:** 2026-06-10
**Status:** Approved

## Goal

Refactor `monitor_stream.py` and `grace_period_bot.py` so that Discord notifications and Azuracast API calls are injectable dependencies. In test mode, notifications are captured to a file instead of sent to Discord; API calls are replaced with mocks. This enables pytest tests that verify the exact content of what would be sent to Discord, and the exact sequence of API calls made.

---

## New Modules

### `notifier.py`

Defines the `Notifier` protocol and two implementations.

**Protocol:**
```python
class Notifier(Protocol):
    def send_message(self, message: str) -> None: ...
    def send_alert(self, reason: str, rms=None, variance=None, stderr="") -> None: ...
```

**`DiscordNotifier(webhook_url, staff_role_id)`**
- Implements `Notifier`
- Wraps the current `send_discord_message` / `send_discord_alert` logic
- Holds cooldown state (`last_alert_time`) as instance state — the `last_alert_time` global is removed from `monitor_stream.py`

**`FileNotifier(path)`**
- Implements `Notifier`
- Appends one JSON line per notification to the given file path
- No cooldown — tests always see every notification
- Format:
  ```json
  {"type": "message", "content": "Greedy Shark is active"}
  {"type": "alert", "reason": "Stream silent for 2 minutes", "rms": null, "variance": null, "stderr": ""}
  ```
- Exposes `get_records() -> list[dict]` which reads the file and returns parsed records

---

### `azuracast_client.py`

Wraps all four Azuracast API calls currently scattered as module-level functions in `monitor_stream.py` and duplicated in `grace_period_bot.py`.

```python
class AzuracastClient:
    def __init__(self, base_url, api_key, station_id): ...
    def check_streamer_connected(self) -> tuple[bool, str | None, int | None]: ...
    def suspend_streamer(self, streamer_id: int) -> bool: ...
    def reactivate_streamer(self, streamer_id: int) -> bool: ...
    def get_all_streamers(self) -> list[dict] | None: ...
```

In tests, pass `unittest.mock.Mock()` with canned return values. No stub class needed.

---

## `monitor_stream.py` Changes

### `MonitorContext`

Gains three required constructor parameters:

```python
class MonitorContext:
    def __init__(self, notifier: Notifier, azuracast: AzuracastClient,
                 sample_fn: Callable[[str, int], bytes | None]):
        self.notifier = notifier
        self.azuracast = azuracast
        self.sample_fn = sample_fn
        # ... existing fields unchanged
```

`sample_fn` wraps `grab_audio_sample` — the ffmpeg call against the live stream. In production it is the real function; in tests it is a callable that returns pre-cooked WAV bytes for specific scenarios:

- **Stream active:** return valid WAV bytes with non-zero amplitude
- **Stream silent:** return valid WAV bytes with zero amplitude
- **Stream unreachable:** return `None`

This lets tests drive any audio condition without touching ffmpeg or a network. `grab_audio_sample` itself is unchanged but is no longer called directly from `monitor_loop`; instead `ctx.sample_fn(STREAM_URL, SAMPLE_DURATION)` is called.

### Functions updated

All state-machine functions already receive `ctx`, so they get `ctx.notifier` and `ctx.azuracast` for free. Specifically:

- `handle_state_transition` — replaces `send_discord_message(...)` with `ctx.notifier.send_message(...)`
- `handle_no_streamer_silence` — replaces `send_discord_alert(...)` with `ctx.notifier.send_alert(...)`
- `handle_streamer_active_silence` — same
- `analyze_audio` — changed to return `(is_active: bool, alert_info: dict | None)` where `alert_info` contains `reason`, `rms`, `variance` when silence is detected. It no longer calls the notifier directly; `monitor_loop` sends the alert using the returned info. This makes `analyze_audio` a pure analysis function.

### Functions removed

The following module-level functions are deleted — their logic moves into the new classes:

- `send_discord_message`
- `send_discord_alert`
- `check_streamer_connected`
- `suspend_streamer`
- `reactivate_streamer`
- `get_all_streamers`

### Composition root

`monitor_loop()` constructs real instances from env vars:

```python
def monitor_loop():
    notifier = DiscordNotifier(DISCORD_WEBHOOK_URL, STAFF_ROLE_ID)
    client = AzuracastClient(AZURACAST_BASE_URL, AZURACAST_API_KEY, AZURACAST_STATION_ID)
    ctx = MonitorContext(notifier=notifier, azuracast=client, sample_fn=grab_audio_sample)
    ...
```

### What stays unchanged

- `determine_next_state` — pure logic, no I/O
- `handle_silence_by_state` — pure router
- `handle_grace_period_silence` — logging only
- `check_grace_period_active` — file I/O, unchanged
- `save_monitor_state` — file I/O, unchanged
- `load_auto_suspended_streamers`, `save_auto_suspended_streamers`, `add_auto_suspended_streamer`, `remove_auto_suspended_streamer` — file I/O, unchanged; used by bot commands

---

## `grace_period_bot.py` Changes

### Module-level client

A single `real_client = AzuracastClient(...)` constructed at module load from env vars, used by all bot command wrappers.

### Command handler extraction

Each `@bot.command` handler becomes a two-line shell:

```python
@bot.command(name='shark')
async def shark(ctx, streamer_id: str = None):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return
    await handle_shark(streamer_id, ctx.send, real_client)
```

Logic moves into extracted `async handle_X(...)` functions with explicit dependencies:

| Handler | Parameters |
|---|---|
| `handle_shark` | `streamer_id, send_fn, client, track_fn=add_auto_suspended_streamer` |
| `handle_unshark` | `streamer_id, send_fn, client, untrack_fn=remove_auto_suspended_streamer` |
| `handle_sharked` | `send_fn, load_fn=load_auto_suspended_streamers` |
| `handle_streamers` | `send_fn, client` |
| `handle_shark_status` | `send_fn, client, load_fn=load_auto_suspended_streamers` |
| `handle_shark_help` | `send_fn` |
| `handle_working_on_it` | `send_fn, grace_file=GRACE_PERIOD_FILE` |
| `handle_cancel_grace` | `send_fn, grace_file=GRACE_PERIOD_FILE` |
| `handle_grace_status` | `send_fn, grace_file=GRACE_PERIOD_FILE` |

`send_fn` is typed as `Callable[[str], Awaitable[None]]` — in production it is `ctx.send`; in tests it is an explicit `async def` wrapper:
```python
captured = []
async def capture(msg): captured.append(msg)
```
Do not pass `captured.append` directly — it is not awaitable.

Auto-suspension functions default to the real implementations; tests pass `Mock()` to verify calls precisely. Grace period file path defaults to the module constant; tests pass `tmp_path / "grace"` so no real files are touched.

---

## Test Changes

### `test_monitor.py`

- Remove all `@patch('monitor_stream.send_discord_*')` decorators
- Tests that check Discord output: construct `FileNotifier(tmp_path / "out.jsonl")`, pass to `MonitorContext`; assert on `notifier.get_records()`
- Tests that only exercise pure state logic: pass `Mock()` for both notifier and client; no behavior assertions on them needed
- Azuracast calls: pass `Mock()` client with `.check_streamer_connected.return_value = (False, None, None)` etc.

Example:
```python
def test_no_streamer_silence_at_threshold(tmp_path):
    notifier = FileNotifier(tmp_path / "out.jsonl")
    ctx = MonitorContext(notifier=notifier, azuracast=Mock())
    ctx.consecutive_silent_checks = SILENCE_ALERT_LEVEL

    handle_no_streamer_silence(ctx)

    records = notifier.get_records()
    assert len(records) == 1
    assert records[0]['type'] == 'alert'
    assert '2 minutes' in records[0]['reason']
```

### `test_bot.py` (new)

Uses `pytest-asyncio`. Each `handle_X` function gets a test class. Every test follows: build `captured = []`, build `Mock()` client with canned returns, call handler, assert on `captured` and mock calls.

Cases covered per command: missing args, invalid args, API failure, already-in-that-state no-ops, happy path.

Example:
```python
@pytest.mark.asyncio
async def test_shark_suspends_active_streamer():
    captured = []
    async def capture(msg): captured.append(msg)

    client = Mock()
    client.get_all_streamers.return_value = [
        {'id': 42, 'display_name': 'TestDJ', 'is_active': True}
    ]
    client.suspend_streamer.return_value = True
    track_fn = Mock()

    await handle_shark("42", capture, client, track_fn=track_fn)

    assert "TestDJ" in captured[0]
    client.suspend_streamer.assert_called_once_with(42)
    track_fn.assert_called_once_with(42, 'TestDJ', reason="staff action via !shark")
```

### Dependencies

Add `pytest-asyncio` to `requirements.txt`. No other new test dependencies.

---

## Post-Refactor Investigation Item

**Auto-suspension appears to have been disabled.** The monitor's `handle_streamer_active_silence` sends alerts at 4 and 10 minutes of silence but never calls `suspend_streamer` or `add_auto_suspended_streamer`. The existing test `test_no_auto_suspend_at_10_minutes` documents this as intentional. However, the tracker functions are still used by the bot's `!shark` / `!sharked` / `!unshark` commands. After the refactor is complete, review whether the monitor should write to the suspension tracker when it sends its 10-minute alert, or whether the current staff-action-only model is intentional and the old auto-suspend path should be formally removed.

---

## File Summary

| File | Action |
|---|---|
| `notifier.py` | New |
| `azuracast_client.py` | New |
| `monitor_stream.py` | Refactor — remove 6 functions, update MonitorContext with notifier/azuracast/sample_fn, update callers |
| `grace_period_bot.py` | Refactor — extract 9 handler functions, thin bot wrappers |
| `test_monitor.py` | Update — remove patches, use FileNotifier + Mock client |
| `test_bot.py` | New |
| `requirements.txt` | Add `pytest-asyncio` |
