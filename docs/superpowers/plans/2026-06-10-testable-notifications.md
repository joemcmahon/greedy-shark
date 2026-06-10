# Testable Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor monitor_stream.py and grace_period_bot.py to inject Discord notifications, Azuracast API calls, and audio sampling so tests can capture all output without hitting real services.

**Architecture:** Extract a `Notifier` protocol (Discord vs. file implementations), an `AzuracastClient` class, and a `sample_fn` callable into `MonitorContext`. Bot commands become thin wrappers over extracted `handle_X` async functions that accept `send_fn` and `client` as injectable dependencies.

**Tech Stack:** Python 3.10+, pytest, pytest-asyncio, unittest.mock, discord.py, requests

---

### Task 1: Add pytest-asyncio

**Files:**
- Modify: `requirements.txt`
- Create: `pytest.ini`

- [ ] **Step 1: Add pytest-asyncio to requirements.txt**

Replace contents of `requirements.txt`:
```
pydub
numpy
requests
ffmpeg-python
python-dotenv
discord.py
pytest
pytest-mock
pytest-asyncio
```

- [ ] **Step 2: Create pytest.ini**

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 3: Install and verify**

```bash
pip install pytest-asyncio
pytest test_monitor.py -v
```

Expected: all existing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt pytest.ini
git commit -m "chore: add pytest-asyncio for async bot handler tests"
```

---

### Task 2: Create notifier.py (TDD)

**Files:**
- Create: `test_notifier.py`
- Create: `notifier.py`

- [ ] **Step 1: Write failing tests**

Create `test_notifier.py`:
```python
import pytest
from notifier import FileNotifier


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
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest test_notifier.py -v
```

Expected: `ModuleNotFoundError: No module named 'notifier'`

- [ ] **Step 3: Implement notifier.py**

Create `notifier.py`:
```python
from __future__ import annotations
import json
import logging
import os
import time
from typing import Protocol

import requests


class Notifier(Protocol):
    def send_message(self, message: str) -> None: ...
    def send_alert(self, reason: str, rms: float | None = None,
                   variance: float | None = None, stderr: str = "") -> None: ...


class DiscordNotifier:
    def __init__(self, webhook_url: str, staff_role_id: str):
        self.webhook_url = webhook_url
        self.staff_role_id = staff_role_id
        self._last_alert_time: float = 0
        self._alert_cooldown: float = float(os.getenv("ALERT_COOLDOWN", 600))

    def send_message(self, message: str) -> None:
        payload = {
            "content": f"\U0001F988 **{message}**",
            "allowed_mentions": {"roles": [self.staff_role_id]},
        }
        try:
            resp = requests.post(self.webhook_url, json=payload)
            if resp.status_code != 204:
                logging.warning("Discord message failed: %s", resp.text)
        except Exception as e:
            logging.error("Error sending Discord message: %s", e)

    def send_alert(self, reason: str, rms: float | None = None,
                   variance: float | None = None, stderr: str = "") -> None:
        now = time.time()
        if now - self._last_alert_time < self._alert_cooldown:
            logging.info("Skipping Discord alert due to cooldown.")
            return
        self._last_alert_time = now

        content = (f"<@&{self.staff_role_id}> \U0001F988 \U0001F6A8 "
                   f"**Stream issue detected**\n**Reason**: {reason}")
        if rms is not None:
            content += f"\n**RMS**: {rms:.2f}"
        if variance is not None:
            content += f"\n**Variance**: {variance:.2f}"
        if stderr:
            content += f"\n**FFmpeg Error**:\n```{stderr.strip()[:500]}```"

        payload = {
            "content": content,
            "allowed_mentions": {"roles": [self.staff_role_id]},
        }
        try:
            resp = requests.post(self.webhook_url, json=payload)
            if resp.status_code != 204:
                logging.warning("Discord alert failed: %s", resp.text)
        except Exception as e:
            logging.error("Error sending Discord alert: %s", e)


class FileNotifier:
    def __init__(self, path) -> None:
        self.path = str(path)

    def send_message(self, message: str) -> None:
        self._append({"type": "message", "content": message})

    def send_alert(self, reason: str, rms: float | None = None,
                   variance: float | None = None, stderr: str = "") -> None:
        self._append({"type": "alert", "reason": reason,
                      "rms": rms, "variance": variance, "stderr": stderr})

    def _append(self, record: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def get_records(self) -> list[dict]:
        try:
            with open(self.path) as f:
                return [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []
```

- [ ] **Step 4: Run to confirm passing**

```bash
pytest test_notifier.py -v
```

Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add notifier.py test_notifier.py
git commit -m "feat: add Notifier protocol with DiscordNotifier and FileNotifier"
```

---

### Task 3: Create azuracast_client.py

**Files:**
- Create: `azuracast_client.py`

- [ ] **Step 1: Create azuracast_client.py**

```python
from __future__ import annotations
import logging
import requests


class AzuracastClient:
    def __init__(self, base_url: str, api_key: str, station_id: str) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.station_id = station_id
        self._headers = {"X-API-Key": api_key}

    def _configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.station_id)

    def check_streamer_connected(self) -> tuple[bool, str | None, int | None]:
        if not self._configured():
            logging.warning("Azuracast API not configured. Skipping streamer check.")
            return False, None, None
        try:
            url = f"{self.base_url}/api/nowplaying/{self.station_id}"
            resp = requests.get(url, headers=self._headers, timeout=10)
            if resp.status_code != 200:
                logging.error("Azuracast API error: %s - %s", resp.status_code, resp.text)
                return False, None, None
            live = resp.json().get("live", {})
            is_live = live.get("is_live", False)
            name = live.get("streamer_name")
            sid = live.get("broadcaster_id")
            logging.info("Streamer check: is_live=%s, streamer=%s, id=%s", is_live, name, sid)
            return is_live, name, sid
        except Exception as e:
            logging.error("Error checking streamer status: %s", e)
            return False, None, None

    def suspend_streamer(self, streamer_id: int) -> bool:
        if not self._configured():
            logging.error("Azuracast API not configured. Cannot suspend streamer.")
            return False
        try:
            url = f"{self.base_url}/api/station/{self.station_id}/streamer/{streamer_id}"
            resp = requests.put(url, headers=self._headers,
                                json={"is_active": False}, timeout=10)
            if resp.status_code in [200, 204]:
                logging.info("Suspended streamer ID %s", streamer_id)
                return True
            logging.error("Failed to suspend: %s - %s", resp.status_code, resp.text)
            return False
        except Exception as e:
            logging.error("Error suspending streamer: %s", e)
            return False

    def reactivate_streamer(self, streamer_id: int) -> bool:
        if not self._configured():
            logging.error("Azuracast API not configured. Cannot reactivate streamer.")
            return False
        try:
            url = f"{self.base_url}/api/station/{self.station_id}/streamer/{streamer_id}"
            resp = requests.put(url, headers=self._headers,
                                json={"is_active": True}, timeout=10)
            if resp.status_code in [200, 204]:
                logging.info("Reactivated streamer ID %s", streamer_id)
                return True
            logging.error("Failed to reactivate: %s - %s", resp.status_code, resp.text)
            return False
        except Exception as e:
            logging.error("Error reactivating streamer: %s", e)
            return False

    def get_all_streamers(self) -> list[dict] | None:
        if not self._configured():
            logging.error("Azuracast API not configured. Cannot fetch streamers.")
            return None
        try:
            url = f"{self.base_url}/api/station/{self.station_id}/streamers"
            resp = requests.get(url, headers=self._headers, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            logging.error("Failed to fetch streamers: %s - %s", resp.status_code, resp.text)
            return None
        except Exception as e:
            logging.error("Error fetching streamers: %s", e)
            return None
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
pytest test_monitor.py -v
```

Expected: all existing tests pass (no imports changed yet).

- [ ] **Step 3: Commit**

```bash
git add azuracast_client.py
git commit -m "feat: add AzuracastClient wrapping all Azuracast API calls"
```

---

### Task 4: Refactor monitor_stream.py

**Files:**
- Modify: `monitor_stream.py`
- Modify: `test_monitor.py` (must be done in same commit — existing tests break mid-task)

Note: Existing tests break when `MonitorContext.__init__` gains required parameters. Fix `test_monitor.py` before committing.

- [ ] **Step 1: Replace imports at top of monitor_stream.py**

Replace the first 15 lines of `monitor_stream.py` with:
```python
import atexit
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from datetime import datetime
from enum import Enum
from typing import Callable

import numpy as np
import requests
from dotenv import load_dotenv
from pydub import AudioSegment

from azuracast_client import AzuracastClient
from notifier import DiscordNotifier, FileNotifier, Notifier
```

- [ ] **Step 2: Remove the six module-level functions and the global**

Delete these entirely from `monitor_stream.py`:
- `last_alert_time = 0`
- `def send_discord_message(message):`
- `def send_discord_alert(reason, rms=None, variance=None, stderr=""):`
- `def check_streamer_connected():`
- `def suspend_streamer(streamer_id):`
- `def reactivate_streamer(streamer_id):`
- `def get_all_streamers():`

Also delete the module-level:
```python
def exit_handler():
    send_discord_message("Monitor has exited")

atexit.register(exit_handler)
signal.signal(signal.SIGINT, kill_handler)
signal.signal(signal.SIGTERM, kill_handler)
```
(The atexit registration moves into `monitor_loop`; keep `kill_handler` itself.)

- [ ] **Step 3: Update MonitorContext**

Replace the `MonitorContext` class:
```python
class MonitorContext:
    """Holds the state machine context and injectable dependencies."""
    def __init__(self, notifier: Notifier, azuracast: AzuracastClient,
                 sample_fn: Callable[[str, int], bytes | None]) -> None:
        self.notifier = notifier
        self.azuracast = azuracast
        self.sample_fn = sample_fn
        self.state = MonitorState.NO_STREAMER
        self.consecutive_silent_checks = 0
        self.streamer_id = None
        self.streamer_name = None
        self.warning_sent = False

    def reset_counters(self):
        self.consecutive_silent_checks = 0
        self.warning_sent = False

    def clear_streamer_info(self):
        self.streamer_id = None
        self.streamer_name = None
```

- [ ] **Step 4: Update analyze_audio to be a pure function**

Replace `analyze_audio`:
```python
def analyze_audio(wav_bytes: bytes) -> tuple[bool, dict | None]:
    """Returns (is_active, alert_info). alert_info is None when audio is present."""
    audio = AudioSegment.from_file(BytesIO(wav_bytes), format="wav")
    samples = np.array(audio.get_array_of_samples()).astype(float)

    if len(samples) == 0:
        return False, {"reason": "Audio sample is empty.", "rms": None, "variance": None}

    rms = float(np.sqrt(np.mean(samples**2)))
    variance = float(np.var(samples))
    logging.info(f"Analyzed audio - RMS: {rms:.2f}, Variance: {variance:.2f}")

    if np.max(np.abs(samples)) == 0:
        return False, {"reason": "Stream is completely silent (zero amplitude)",
                       "rms": rms, "variance": variance}

    return True, None
```

- [ ] **Step 5: Update handle_state_transition**

Replace the two `send_discord_message` calls:
```python
if new_state == MonitorState.GRACE_PERIOD and previous_state == MonitorState.STREAMER_ACTIVE:
    ctx.notifier.send_message(f"Grace period activated for '{ctx.streamer_name}'. Monitoring paused.")

elif new_state == MonitorState.STREAMER_ACTIVE and previous_state == MonitorState.GRACE_PERIOD:
    ctx.notifier.send_message(f"Grace period expired for '{ctx.streamer_name}'. Normal monitoring resumed.")
```

- [ ] **Step 6: Update handle_no_streamer_silence**

```python
def handle_no_streamer_silence(ctx):
    if ctx.consecutive_silent_checks == SILENCE_ALERT_LEVEL:
        ctx.notifier.send_alert("🚨 **Stream silent for 2 minutes!** (No streamer connected)")
        ctx.consecutive_silent_checks = 0
```

- [ ] **Step 7: Update handle_streamer_active_silence**

```python
def handle_streamer_active_silence(ctx):
    if ctx.consecutive_silent_checks == STREAMER_WARNING_THRESHOLD and not ctx.warning_sent:
        ctx.notifier.send_alert(
            f"⚠️ **Action may be needed soon** - Streamer '{ctx.streamer_name}' has been silent "
            f"for {STREAMER_WARNING_THRESHOLD} minutes. Use `!shark <id>` to suspend if needed.")
        ctx.warning_sent = True
    elif ctx.consecutive_silent_checks >= STREAMER_SUSPEND_THRESHOLD:
        ctx.notifier.send_alert(
            f"🚨 **Staff action required** - Streamer '{ctx.streamer_name}' has been silent "
            f"for {ctx.consecutive_silent_checks} minutes. Use `!streamers` then `!shark <id>` to suspend.")
```

- [ ] **Step 8: Replace monitor_loop**

```python
def monitor_loop():
    notifier = DiscordNotifier(DISCORD_WEBHOOK_URL, STAFF_ROLE_ID)
    client = AzuracastClient(AZURACAST_BASE_URL, AZURACAST_API_KEY, AZURACAST_STATION_ID)
    ctx = MonitorContext(notifier=notifier, azuracast=client, sample_fn=grab_audio_sample)

    atexit.register(lambda: notifier.send_message("Monitor has exited"))
    signal.signal(signal.SIGINT, kill_handler)
    signal.signal(signal.SIGTERM, kill_handler)

    notifier.send_message("Greedy Shark is active")

    while True:
        logging.info(f"🔁 Checking stream... [State: {ctx.state.value}]")

        is_streamer_connected, streamer_name, streamer_id = ctx.azuracast.check_streamer_connected()
        grace_period_active = check_grace_period_active()

        new_state = determine_next_state(ctx, is_streamer_connected, grace_period_active)
        if new_state:
            handle_state_transition(ctx, new_state, streamer_name, streamer_id)

        wav_bytes = ctx.sample_fn(STREAM_URL, SAMPLE_DURATION)

        if wav_bytes:
            is_active, alert_info = analyze_audio(wav_bytes)
            if is_active:
                logging.info("✅ Stream is active and broadcasting.")
                ctx.reset_counters()
            else:
                ctx.consecutive_silent_checks += 1
                if alert_info:
                    ctx.notifier.send_alert(**alert_info)
                logging.warning(f"⚠️ Stream appears silent or inactive. ({ctx.consecutive_silent_checks} checks)")
        else:
            ctx.consecutive_silent_checks += 1
            logging.error(f"❌ Failed to retrieve audio sample. ({ctx.consecutive_silent_checks} checks)")

        handle_silence_by_state(ctx)
        save_monitor_state(ctx)
        time.sleep(CHECK_INTERVAL_SECONDS)
```

- [ ] **Step 9: Update test_monitor.py — fix MonitorContext construction**

Every `MonitorContext()` call in `test_monitor.py` needs the three new parameters. Add this fixture near the top of the file, and a helper:

```python
from unittest.mock import Mock, patch, mock_open, MagicMock
from notifier import FileNotifier
from azuracast_client import AzuracastClient

def make_ctx(notifier=None, azuracast=None, sample_fn=None):
    """Build a MonitorContext with sensible test defaults."""
    from monitor_stream import MonitorContext
    return MonitorContext(
        notifier=notifier or Mock(),
        azuracast=azuracast or Mock(),
        sample_fn=sample_fn or Mock(return_value=None),
    )
```

Replace every `ctx = MonitorContext()` with `ctx = make_ctx()`.

- [ ] **Step 10: Update tests that checked Discord calls**

Replace `@patch('monitor_stream.send_discord_message')` tests with FileNotifier assertions. For example, `test_transition_to_grace_period_sends_message`:

```python
def test_transition_to_grace_period_sends_message(self, tmp_path):
    notifier = FileNotifier(tmp_path / "out.jsonl")
    ctx = make_ctx(notifier=notifier)
    ctx.state = MonitorState.STREAMER_ACTIVE
    ctx.streamer_name = "TestDJ"

    handle_state_transition(ctx, MonitorState.GRACE_PERIOD)

    records = notifier.get_records()
    assert len(records) == 1
    assert records[0]["type"] == "message"
    assert "Grace period activated" in records[0]["content"]
```

Apply same pattern to `test_transition_to_no_streamer_clears_info`, `test_full_streamer_lifecycle`, `test_grace_period_activation_and_expiration`, and any test using `@patch('monitor_stream.send_discord_alert')`.

For silence handler tests that used `@patch('monitor_stream.send_discord_alert')`, replace mock assertions with FileNotifier record checks:

```python
def test_no_streamer_silence_at_threshold(self, tmp_path):
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
```

- [ ] **Step 11: Run all tests**

```bash
pytest test_monitor.py test_notifier.py -v
```

Expected: all pass.

- [ ] **Step 12: Commit**

```bash
git add monitor_stream.py test_monitor.py
git commit -m "refactor: inject notifier, azuracast client, and sample_fn into MonitorContext"
```

---

### Task 5: Refactor grace_period_bot.py — imports and handler extraction

**Files:**
- Modify: `grace_period_bot.py`

- [ ] **Step 1: Replace the import block at the top of grace_period_bot.py**

Replace:
```python
from monitor_stream import (
    load_auto_suspended_streamers,
    remove_auto_suspended_streamer,
    reactivate_streamer,
    get_all_streamers,
    suspend_streamer,
    add_auto_suspended_streamer
)
```

With:
```python
from azuracast_client import AzuracastClient
from monitor_stream import (
    load_auto_suspended_streamers,
    remove_auto_suspended_streamer,
    add_auto_suspended_streamer,
    MONITOR_STATE_FILE,
)
```

- [ ] **Step 2: Add module-level real_client after the env var block**

After `GRACE_PERIOD_FILE = ".grace_period_until"`, add:
```python
AZURACAST_BASE_URL = os.getenv("AZURACAST_BASE_URL")
AZURACAST_API_KEY = os.getenv("AZURACAST_API_KEY")
AZURACAST_STATION_ID = os.getenv("AZURACAST_STATION_ID")

real_client = AzuracastClient(
    base_url=AZURACAST_BASE_URL or "",
    api_key=AZURACAST_API_KEY or "",
    station_id=AZURACAST_STATION_ID or "",
)
```

- [ ] **Step 3: Add extracted handler functions before the bot command definitions**

Insert these functions between the `bot = commands.Bot(...)` line and the first `@bot.event`:

```python
async def handle_shark_help(send_fn, grace_minutes=GRACE_PERIOD_MINUTES):
    help_text = """
🦈 **Greedy Shark Bot Commands**

**Grace Period Commands:**
• `!working-on-it` (or `!woi`) - Activate a {grace_min}-minute grace period
  Pauses monitoring while you fix technical issues

• `!grace-status` (or `!gs`) - Check if grace period is active and when it expires

• `!cancel-grace` (or `!cg`) - Cancel an active grace period early

**Streamer Management:**
• `!streamers` - List all registered streamers with their IDs and status
  Use this first to find the ID you need for !shark

• `!shark <id>` - Suspend a streamer by their numeric Azuracast ID
  Example: `!shark 42`
  Recommended workflow: `!streamers` → find ID → `!shark <id>`

• `!sharked` - List all streamers currently suspended by the Shark
  Shows names, timestamps, and reasons for suspension

• `!unshark <id>` - Re-enable a suspended streamer by their numeric Azuracast ID
  Example: `!unshark 42`
  Recommended workflow: `!streamers` → find ID → `!unshark <id>`

• `!shark-status` (or `!status`) - Show current Shark monitoring status

• `!shark-help` (or `!sharkhelp`) - Show this help message

**How the Shark Works:**
• No streamer connected: 2-minute silence → staff alert
• Streamer connected: 4-minute silence → warning alert to staff
• 10+ minutes silence: escalating urgent alerts every check interval
• Staff uses `!streamers` then `!shark <id>` to suspend when needed
• Audio detection resets all timers
• Grace period pauses monitoring entirely
""".format(grace_min=grace_minutes)
    await send_fn(help_text)


async def handle_working_on_it(send_fn, grace_file=GRACE_PERIOD_FILE,
                               grace_minutes=GRACE_PERIOD_MINUTES):
    try:
        expiration = datetime.now() + timedelta(minutes=grace_minutes)
        with open(grace_file, 'w') as f:
            f.write(str(expiration.timestamp()))
        await send_fn(
            f"✅ Grace period activated! Monitoring suspended for {grace_minutes} minutes. "
            f"Auto-suspension disabled until {expiration.strftime('%H:%M:%S')}.")
    except Exception as e:
        await send_fn(f"❌ Failed to activate grace period: {str(e)}")


async def handle_cancel_grace(send_fn, grace_file=GRACE_PERIOD_FILE):
    try:
        if os.path.exists(grace_file):
            os.remove(grace_file)
            await send_fn("✅ Grace period cancelled. Normal monitoring resumed.")
        else:
            await send_fn("ℹ️ No active grace period to cancel.")
    except Exception as e:
        await send_fn(f"❌ Failed to cancel grace period: {str(e)}")


async def handle_grace_status(send_fn, grace_file=GRACE_PERIOD_FILE):
    try:
        if not os.path.exists(grace_file):
            await send_fn("ℹ️ No active grace period. Normal monitoring active.")
            return
        with open(grace_file, 'r') as f:
            content = f.read().strip()
        if not content:
            await send_fn("ℹ️ No active grace period. Normal monitoring active.")
            return
        expiration = datetime.fromtimestamp(float(content))
        now = datetime.now()
        if expiration > now:
            remaining = expiration - now
            minutes = int(remaining.total_seconds() / 60)
            seconds = int(remaining.total_seconds() % 60)
            await send_fn(
                f"⏳ Grace period active. Expires in {minutes}m {seconds}s "
                f"at {expiration.strftime('%H:%M:%S')}.")
        else:
            await send_fn("ℹ️ Grace period has expired. Normal monitoring active.")
    except Exception as e:
        await send_fn(f"❌ Failed to check grace period status: {str(e)}")


async def handle_sharked(send_fn, load_fn=load_auto_suspended_streamers):
    try:
        suspended = load_fn()
        if not suspended:
            await send_fn("ℹ️ No streamers are currently suspended by the Shark. All clear! 🦈")
            return
        message = "🦈 **Streamers suspended by the Shark:**\n\n"
        for sid, info in suspended.items():
            name = info.get('name', 'Unknown')
            suspended_at = info.get('suspended_at', 'Unknown time')
            reason = info.get('reason', 'Unknown reason')
            try:
                time_str = datetime.fromisoformat(suspended_at).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                time_str = suspended_at
            message += f"• **{name}** (ID: {sid})\n"
            message += f"  ├ Suspended: {time_str}\n"
            message += f"  └ Reason: {reason}\n\n"
        message += "Use `!unshark <id>` to re-enable a streamer by their ID."
        await send_fn(message)
    except Exception as e:
        await send_fn(f"❌ Error: {str(e)}")


async def handle_streamers(send_fn, client):
    try:
        all_streamers = client.get_all_streamers()
        if all_streamers is None:
            await send_fn("❌ Failed to fetch streamers from Azuracast. Check logs for details.")
            return
        if not all_streamers:
            await send_fn("ℹ️ No streamers found in Azuracast.")
            return
        message = "🦈 **Registered Streamers:**\n\n"
        for s in sorted(all_streamers, key=lambda x: x.get('display_name', '').lower()):
            sid = s.get('id', '?')
            name = s.get('display_name', 'Unknown')
            status = "✅ active" if s.get('is_active', True) else "🔴 suspended"
            message += f"• **{name}** (ID: `{sid}`) — {status}\n"
        message += "\nUse `!shark <id>` to suspend a streamer by their ID."
        await send_fn(message)
    except Exception as e:
        await send_fn(f"❌ Error: {str(e)}")


async def handle_shark(streamer_id, send_fn, client,
                       track_fn=add_auto_suspended_streamer, author_name="staff"):
    if streamer_id is None:
        await send_fn("❌ You must provide a streamer ID. Use `!streamers` to see IDs, then `!shark <id>`.")
        return
    if not streamer_id.isdigit():
        await send_fn(f"❌ '{streamer_id}' is not a valid ID. IDs are numeric. Use `!streamers` to see them.")
        return
    sid = int(streamer_id)
    try:
        all_streamers = client.get_all_streamers()
        if all_streamers is None:
            await send_fn("❌ Failed to fetch streamers from Azuracast. Cannot verify ID. Check logs.")
            return
        target = next((s for s in all_streamers if s.get('id') == sid), None)
        if target is None:
            await send_fn(f"❌ No streamer found with ID `{sid}`. Use `!streamers` to see valid IDs.")
            return
        name = target.get('display_name', f'ID {sid}')
        if not target.get('is_active', True):
            await send_fn(f"ℹ️ **{name}** (ID: `{sid}`) is already suspended.")
            return
        if client.suspend_streamer(sid):
            track_fn(sid, name, reason="staff action via !shark")
            await send_fn(f"🦈 **{name}** (ID: `{sid}`) has been suspended by {author_name}.")
        else:
            await send_fn(f"❌ Failed to suspend **{name}** (ID: `{sid}`) via Azuracast API. Check logs.")
    except Exception as e:
        await send_fn(f"❌ Error: {str(e)}")


async def handle_unshark(streamer_id, send_fn, client,
                         untrack_fn=remove_auto_suspended_streamer):
    if streamer_id is None:
        await send_fn("❌ You must provide a streamer ID. Use `!streamers` to see IDs, then `!unshark <id>`.")
        return
    if not streamer_id.isdigit():
        await send_fn(f"❌ '{streamer_id}' is not a valid ID. IDs are numeric. Use `!streamers` to see them.")
        return
    sid = int(streamer_id)
    try:
        all_streamers = client.get_all_streamers()
        if all_streamers is None:
            await send_fn("❌ Failed to fetch streamers from Azuracast. Cannot verify ID. Check logs.")
            return
        target = next((s for s in all_streamers if s.get('id') == sid), None)
        if target is None:
            await send_fn(f"❌ No streamer found with ID `{sid}`. Use `!streamers` to see valid IDs.")
            return
        name = target.get('display_name', f'ID {sid}')
        if target.get('is_active', True):
            await send_fn(f"ℹ️ **{name}** (ID: `{sid}`) is not currently suspended.")
            return
        if client.reactivate_streamer(sid):
            untrack_fn(sid)
            await send_fn(f"✅ Successfully re-enabled **{name}** (ID: `{sid}`)!")
        else:
            await send_fn(f"❌ Failed to re-enable **{name}** (ID: `{sid}`) via Azuracast API. Check logs.")
    except Exception as e:
        await send_fn(f"❌ Error: {str(e)}")


async def handle_shark_status(send_fn, client,
                              load_fn=load_auto_suspended_streamers,
                              state_file=MONITOR_STATE_FILE,
                              grace_file=GRACE_PERIOD_FILE):
    try:
        suspended = load_fn()
        if not suspended:
            suspended_msg = "No users suspended"
        else:
            count = len(suspended)
            word = "user" if count == 1 else "users"
            names = [info.get('name', 'Unknown') for info in suspended.values()]
            suspended_msg = f"{count} {word} suspended: {', '.join(names)}"

        if not os.path.exists(state_file):
            status_msg = "Monitor state not available"
        else:
            try:
                with open(state_file, 'r') as f:
                    state_data = json.load(f)
                monitor_state = state_data.get('state', 'unknown')
                silence_checks = state_data.get('consecutive_silent_checks', 0)
                streamer_name = state_data.get('streamer_name', '')

                grace_active = False
                grace_remaining = 0
                if os.path.exists(grace_file):
                    try:
                        with open(grace_file, 'r') as f:
                            content = f.read().strip()
                        if content:
                            grace_ts = float(content)
                            now = time.time()
                            if grace_ts > now:
                                grace_active = True
                                grace_remaining = int((grace_ts - now) / 60)
                    except Exception:
                        pass

                if grace_active and streamer_name:
                    s = 's' if grace_remaining != 1 else ''
                    status_msg = (f"**{streamer_name}** streaming, "
                                  f"{grace_remaining} minute{s} into grace period")
                elif monitor_state == 'no_streamer':
                    if silence_checks == 0:
                        status_msg = "No silence detected"
                    else:
                        secs = silence_checks * 60
                        s = 's' if secs != 1 else ''
                        status_msg = f"No streamer, {secs} second{s} silence"
                elif monitor_state == 'streamer_active':
                    if silence_checks == 0:
                        status_msg = f"**{streamer_name}** streaming, no silence detected"
                    else:
                        s = 's' if silence_checks != 1 else ''
                        status_msg = f"**{streamer_name}** streaming, {silence_checks} minute{s} silence"
                else:
                    status_msg = "Monitor status unknown"
            except Exception as e:
                status_msg = f"Error reading monitor state: {str(e)}"

        message = "🦈 **Greedy Shark Status**\n\n"
        message += f"**Suspended:** {suspended_msg}\n"
        message += f"**Status:** {status_msg}"
        await send_fn(message)
    except Exception as e:
        await send_fn(f"❌ Error: {str(e)}")
```

- [ ] **Step 4: Replace all @bot.command handlers with thin wrappers**

Replace the body of every `@bot.command` handler. After each `if ctx.channel.id != DISCORD_CHANNEL_ID: return`, delegate to the matching handler:

```python
@bot.command(name='shark-help', aliases=['sharkhelp'])
async def shark_help(ctx):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return
    await handle_shark_help(ctx.send)

@bot.command(name='working-on-it', aliases=['workingonit', 'woi'])
async def working_on_it(ctx):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return
    await handle_working_on_it(ctx.send)

@bot.command(name='cancel-grace', aliases=['cancelgrace', 'cg'])
async def cancel_grace(ctx):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return
    await handle_cancel_grace(ctx.send)

@bot.command(name='grace-status', aliases=['gracestatus', 'gs'])
async def grace_status(ctx):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return
    await handle_grace_status(ctx.send)

@bot.command(name='unshark', aliases=['letin', 'let-in'])
async def unshark(ctx, streamer_id: str = None):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return
    await handle_unshark(streamer_id, ctx.send, real_client)

@bot.command(name='shark-status', aliases=['sharkstatus', 'status'])
async def shark_status(ctx):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return
    await handle_shark_status(ctx.send, real_client)

@bot.command(name='sharked')
async def sharked(ctx):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return
    await handle_sharked(ctx.send)

@bot.command(name='streamers')
async def streamers(ctx):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return
    await handle_streamers(ctx.send, real_client)

@bot.command(name='shark')
async def shark(ctx, streamer_id: str = None):
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return
    await handle_shark(streamer_id, ctx.send, real_client,
                       author_name=ctx.author.display_name)
```

- [ ] **Step 5: Run monitor tests to verify no regressions**

```bash
pytest test_monitor.py test_notifier.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add grace_period_bot.py
git commit -m "refactor: extract bot command logic into injectable handle_X functions"
```

---

### Task 6: Create test_bot.py

**Files:**
- Create: `test_bot.py`

- [ ] **Step 1: Write test_bot.py**

```python
import json
import os
import time
import pytest
from unittest.mock import Mock, patch
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
```

- [ ] **Step 2: Run all tests**

```bash
pytest test_bot.py test_monitor.py test_notifier.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add test_bot.py
git commit -m "test: add test_bot.py covering all extracted bot handler functions"
```

---

### Self-Review

**Spec coverage:**
- `notifier.py` with `Notifier` protocol, `DiscordNotifier`, `FileNotifier` → Task 2 ✅
- `azuracast_client.py` with all four methods → Task 3 ✅
- `MonitorContext` gains `notifier`, `azuracast`, `sample_fn` → Task 4 ✅
- `analyze_audio` returns `(is_active, alert_info)` → Task 4 ✅
- Six old monitor functions deleted → Task 4 ✅
- `monitor_loop` as composition root → Task 4 ✅
- `atexit` moved into `monitor_loop` → Task 4 ✅
- `grace_period_bot.py` uses `AzuracastClient` module-level instance → Task 5 ✅
- Nine `handle_X` functions extracted → Task 5 ✅
- Thin `@bot.command` wrappers → Task 5 ✅
- `author_name` parameter on `handle_shark` → Task 5 ✅
- `grace_minutes` parameter on `handle_working_on_it` → Task 5 ✅
- `state_file` + `grace_file` injectable on `handle_shark_status` → Task 5 ✅
- `test_monitor.py` updated to remove patches → Task 4 ✅
- `test_bot.py` with happy path + error cases per command → Task 6 ✅
- `pytest-asyncio` added → Task 1 ✅
- Post-refactor investigation item (auto-suspension): not a code task — flagged in spec only ✅

**Placeholder scan:** No TBD, no "handle edge cases", no "similar to Task N". All code blocks are complete.

**Type consistency:** `send_fn` is `async def send_fn(msg): msgs.append(msg)` throughout Task 6. `handle_X` signatures in Task 5 match parameters used in Task 6 tests. `FileNotifier(tmp_path / "out.jsonl")` matches the constructor defined in Task 2.
