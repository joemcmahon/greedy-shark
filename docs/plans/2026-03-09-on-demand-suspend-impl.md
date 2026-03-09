# On-Demand Suspend & Grace Period Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove auto-suspension from the monitor, add on-demand `!shark <id>` and `!streamers` bot commands, fix the `!grace-status` empty-file crash, and lower the silence warning threshold to 4 minutes.

**Architecture:** Two files change: `monitor_stream.py` (silence thresholds and auto-suspend removal) and `grace_period_bot.py` (bug fix, two new commands, help text update). Tests in `test_monitor.py` are updated to match the new silence-handling behavior.

**Tech Stack:** Python, discord.py, pytest, unittest.mock

---

### Task 1: Fix `!grace-status` empty-file crash

**Files:**
- Modify: `grace_period_bot.py:169-188`

The `grace_status` command reads the grace period file and calls `float()` on the content without checking whether the content is empty. When the file exists but is empty, `float('')` raises `ValueError`.

**Step 1: Write the failing test**

Add this test to `test_monitor.py` inside `TestGracePeriod`:

```python
@patch('builtins.open', new_callable=mock_open, read_data='')
@patch('os.path.exists')
def test_grace_period_not_active_when_file_empty(self, mock_exists, mock_file):
    """Test that empty grace period file is treated as inactive."""
    mock_exists.return_value = True

    result = check_grace_period_active()

    assert result is False
```

**Step 2: Run test to verify it passes (this function already handles empty files)**

```bash
cd /home/joemcmahon/greedy-shark && \
  monitor-venv/bin/python -m pytest test_monitor.py::TestGracePeriod::test_grace_period_not_active_when_file_empty -v
```

Expected: PASS — `check_grace_period_active()` in `monitor_stream.py` already guards against empty content (line 249). The bug is only in the bot command, which we fix next.

**Step 3: Fix the bot command**

In `grace_period_bot.py`, the `grace_status` command (around line 169) currently reads:

```python
with open(GRACE_PERIOD_FILE, 'r') as f:
    timestamp = float(f.read().strip())
```

Replace the entire `if os.path.exists(GRACE_PERIOD_FILE):` block with:

```python
if os.path.exists(GRACE_PERIOD_FILE):
    with open(GRACE_PERIOD_FILE, 'r') as f:
        content = f.read().strip()

    if not content:
        await ctx.send("ℹ️ No active grace period. Normal monitoring active.")
        return

    timestamp = float(content)
    expiration = datetime.fromtimestamp(timestamp)
    now = datetime.now()

    if expiration > now:
        remaining = expiration - now
        minutes = int(remaining.total_seconds() / 60)
        seconds = int(remaining.total_seconds() % 60)
        await ctx.send(f"⏳ Grace period active. Expires in {minutes}m {seconds}s at {expiration.strftime('%H:%M:%S')}.")
    else:
        await ctx.send("ℹ️ Grace period has expired. Normal monitoring active.")
```

**Step 4: Commit**

```bash
git add grace_period_bot.py test_monitor.py
git commit -m "fix: guard !grace-status against empty grace period file"
```

---

### Task 2: Lower warning threshold from 8 to 4 minutes

**Files:**
- Modify: `monitor_stream.py:387-388`
- Modify: `test_monitor.py:196-221`

**Step 1: Update the constant in `monitor_stream.py`**

Change line 387:
```python
STREAMER_WARNING_THRESHOLD = 8  # 8 x 60s = 8 minutes
```
to:
```python
STREAMER_WARNING_THRESHOLD = 4  # 4 x 60s = 4 minutes
```

**Step 2: Run existing warning threshold test — expect failure**

```bash
cd /home/joemcmahon/greedy-shark && \
  monitor-venv/bin/python -m pytest test_monitor.py::TestSilenceHandlers::test_streamer_active_warning_at_8_minutes -v
```

Expected: The test may still PASS (it uses `STREAMER_WARNING_THRESHOLD` constant, not the literal 8), but verify the constant imported is now 4.

**Step 3: Rename the test to reflect the new threshold**

In `test_monitor.py`, rename `test_streamer_active_warning_at_8_minutes` to `test_streamer_active_warning_at_4_minutes`. The body is unchanged since it uses the `STREAMER_WARNING_THRESHOLD` constant.

```python
@patch('monitor_stream.send_discord_alert')
def test_streamer_active_warning_at_4_minutes(self, mock_alert):
    """Test that warning is sent at 4-minute threshold."""
    ctx = MonitorContext()
    ctx.state = MonitorState.STREAMER_ACTIVE
    ctx.streamer_name = "TestDJ"
    ctx.consecutive_silent_checks = STREAMER_WARNING_THRESHOLD
    ctx.warning_sent = False

    handle_streamer_active_silence(ctx)

    mock_alert.assert_called_once()
    assert "imminent" in mock_alert.call_args[0][0]
    assert ctx.warning_sent is True
```

**Step 4: Run all silence handler tests**

```bash
cd /home/joemcmahon/greedy-shark && \
  monitor-venv/bin/python -m pytest test_monitor.py::TestSilenceHandlers -v
```

Expected: All pass (the auto-suspend tests will fail in the next task once we change the behavior).

**Step 5: Commit**

```bash
git add monitor_stream.py test_monitor.py
git commit -m "feat: lower streamer silence warning threshold from 8 to 4 minutes"
```

---

### Task 3: Remove auto-suspend, add escalating alert

**Files:**
- Modify: `monitor_stream.py:473-491`
- Modify: `test_monitor.py:223-258`

**Step 1: Write the new failing tests**

Replace the two auto-suspend tests (`test_streamer_suspended_at_10_minutes` and `test_streamer_suspension_failure_handled`) with these three:

```python
@patch('monitor_stream.suspend_streamer')
@patch('monitor_stream.send_discord_alert')
def test_no_auto_suspend_at_10_minutes(self, mock_alert, mock_suspend):
    """Test that streamer is NOT auto-suspended at 10-minute threshold."""
    ctx = MonitorContext()
    ctx.state = MonitorState.STREAMER_ACTIVE
    ctx.streamer_name = "TestDJ"
    ctx.streamer_id = 123
    ctx.consecutive_silent_checks = STREAMER_SUSPEND_THRESHOLD

    handle_streamer_active_silence(ctx)

    mock_suspend.assert_not_called()
    assert ctx.state == MonitorState.STREAMER_ACTIVE
    assert ctx.streamer_id == 123

@patch('monitor_stream.send_discord_alert')
def test_urgent_alert_sent_at_10_minutes(self, mock_alert):
    """Test that an urgent alert is sent at 10-minute threshold."""
    ctx = MonitorContext()
    ctx.state = MonitorState.STREAMER_ACTIVE
    ctx.streamer_name = "TestDJ"
    ctx.streamer_id = 123
    ctx.consecutive_silent_checks = STREAMER_SUSPEND_THRESHOLD

    handle_streamer_active_silence(ctx)

    mock_alert.assert_called_once()
    assert "TestDJ" in mock_alert.call_args[0][0]

@patch('monitor_stream.send_discord_alert')
def test_urgent_alert_repeats_after_10_minutes(self, mock_alert):
    """Test that urgent alert repeats on every check after 10 minutes."""
    ctx = MonitorContext()
    ctx.state = MonitorState.STREAMER_ACTIVE
    ctx.streamer_name = "TestDJ"
    ctx.streamer_id = 123
    ctx.consecutive_silent_checks = STREAMER_SUSPEND_THRESHOLD + 3

    handle_streamer_active_silence(ctx)

    mock_alert.assert_called_once()
    assert "TestDJ" in mock_alert.call_args[0][0]
```

**Step 2: Run the new tests to verify they fail**

```bash
cd /home/joemcmahon/greedy-shark && \
  monitor-venv/bin/python -m pytest test_monitor.py::TestSilenceHandlers::test_no_auto_suspend_at_10_minutes test_monitor.py::TestSilenceHandlers::test_urgent_alert_sent_at_10_minutes test_monitor.py::TestSilenceHandlers::test_urgent_alert_repeats_after_10_minutes -v
```

Expected: `test_no_auto_suspend_at_10_minutes` FAILS (suspend IS called), others may fail too.

**Step 3: Rewrite `handle_streamer_active_silence` in `monitor_stream.py`**

Replace the entire `handle_streamer_active_silence` function (lines 473–491):

```python
def handle_streamer_active_silence(ctx):
    """Handle silence detection when a streamer is actively connected."""
    if ctx.consecutive_silent_checks == STREAMER_WARNING_THRESHOLD and not ctx.warning_sent:
        send_discord_alert(f"⚠️ **Action may be needed soon** - Streamer '{ctx.streamer_name}' has been silent for {STREAMER_WARNING_THRESHOLD} minutes. Use `!shark <id>` to suspend if needed.")
        ctx.warning_sent = True

    elif ctx.consecutive_silent_checks >= STREAMER_SUSPEND_THRESHOLD:
        send_discord_alert(f"🚨 **Staff action required** - Streamer '{ctx.streamer_name}' has been silent for {ctx.consecutive_silent_checks} minutes. Use `!streamers` then `!shark <id>` to suspend.")
```

**Step 4: Run all new and existing silence tests**

```bash
cd /home/joemcmahon/greedy-shark && \
  monitor-venv/bin/python -m pytest test_monitor.py::TestSilenceHandlers -v
```

Expected: All pass. The old auto-suspend tests are gone; the new ones pass.

**Step 5: Run the full test suite**

```bash
cd /home/joemcmahon/greedy-shark && \
  monitor-venv/bin/python -m pytest test_monitor.py -v
```

Expected: All pass.

**Step 6: Commit**

```bash
git add monitor_stream.py test_monitor.py
git commit -m "feat: replace auto-suspend with escalating staff alerts after silence threshold"
```

---

### Task 4: Add `!streamers` command to the bot

**Files:**
- Modify: `grace_period_bot.py` (add new command before `if __name__ == "__main__":`)

This command calls the already-existing `get_all_streamers()` function and lists each streamer's ID, name, and whether they are active or suspended.

**Step 1: Verify `get_all_streamers()` return shape**

Check `monitor_stream.py` around line 221. The function returns a list of dicts from the Azuracast API. The relevant fields are `id` (numeric), `display_name` (string), and `is_active` (bool).

**Step 2: Add the `!streamers` command**

Add this before the `if __name__ == "__main__":` block in `grace_period_bot.py`:

```python
@bot.command(name='streamers')
async def streamers(ctx):
    """
    List all streamers registered in Azuracast with their IDs and status.
    Use IDs with !shark to suspend a specific streamer.
    """
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    try:
        all_streamers = get_all_streamers()

        if all_streamers is None:
            await ctx.send("❌ Failed to fetch streamers from Azuracast. Check logs for details.")
            return

        if not all_streamers:
            await ctx.send("ℹ️ No streamers found in Azuracast.")
            return

        message = "🦈 **Registered Streamers:**\n\n"
        for s in sorted(all_streamers, key=lambda x: x.get('display_name', '').lower()):
            sid = s.get('id', '?')
            name = s.get('display_name', 'Unknown')
            active = s.get('is_active', True)
            status = "✅ active" if active else "🔴 suspended"
            message += f"• **{name}** (ID: `{sid}`) — {status}\n"

        message += f"\nUse `!shark <id>` to suspend a streamer by their ID."
        await ctx.send(message)

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Error in streamers command: {e}")
```

**Step 3: Confirm it is importable (no syntax errors)**

```bash
cd /home/joemcmahon/greedy-shark && \
  monitor-venv/bin/python -c "import grace_period_bot; print('OK')"
```

Expected: `OK`

**Step 4: Commit**

```bash
git add grace_period_bot.py
git commit -m "feat: add !streamers command to list all streamers with their IDs"
```

---

### Task 5: Add `!shark <id>` command to the bot

**Files:**
- Modify: `grace_period_bot.py` (add new command)
- Verify imports: `suspend_streamer`, `add_auto_suspended_streamer` are already imported from `monitor_stream`

**Step 1: Check current imports at top of `grace_period_bot.py`**

The file currently imports:
```python
from monitor_stream import (
    load_auto_suspended_streamers,
    remove_auto_suspended_streamer,
    reactivate_streamer,
    get_all_streamers
)
```

`suspend_streamer` and `add_auto_suspended_streamer` are not yet imported. We need to add them.

**Step 2: Update the imports**

Change the import block to:
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

**Step 3: Add the `!shark <id>` command**

Add this after the `!streamers` command and before `if __name__ == "__main__":`:

```python
@bot.command(name='shark')
async def shark(ctx, streamer_id: str = None):
    """
    Suspend a specific streamer by their Azuracast numeric ID.
    Use !streamers to find the ID first.
    """
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    # Require an ID argument
    if streamer_id is None:
        await ctx.send("❌ You must provide a streamer ID. Use `!streamers` to see IDs, then `!shark <id>`.")
        return

    # Validate it's numeric
    if not streamer_id.isdigit():
        await ctx.send(f"❌ '{streamer_id}' is not a valid ID. IDs are numeric. Use `!streamers` to see them.")
        return

    sid = int(streamer_id)

    try:
        # Look up the streamer in Azuracast to verify they exist and get their name
        all_streamers = get_all_streamers()
        if all_streamers is None:
            await ctx.send("❌ Failed to fetch streamers from Azuracast. Cannot verify ID. Check logs.")
            return

        target = None
        for s in all_streamers:
            if s.get('id') == sid:
                target = s
                break

        if target is None:
            await ctx.send(f"❌ No streamer found with ID `{sid}`. Use `!streamers` to see valid IDs.")
            return

        name = target.get('display_name', f'ID {sid}')

        # Check if already suspended
        if not target.get('is_active', True):
            await ctx.send(f"ℹ️ **{name}** (ID: `{sid}`) is already suspended.")
            return

        # Suspend via Azuracast API
        if suspend_streamer(sid):
            add_auto_suspended_streamer(sid, name, reason="staff action via !shark")
            await ctx.send(f"🦈 **{name}** (ID: `{sid}`) has been suspended by {ctx.author.display_name}.")
            print(f"Streamer {name} (ID: {sid}) suspended by {ctx.author}")
        else:
            await ctx.send(f"❌ Failed to suspend **{name}** (ID: `{sid}`) via Azuracast API. Check logs.")

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Error in shark command: {e}")
```

**Step 4: Confirm no syntax errors**

```bash
cd /home/joemcmahon/greedy-shark && \
  monitor-venv/bin/python -c "import grace_period_bot; print('OK')"
```

Expected: `OK`

**Step 5: Run full test suite to catch any regressions**

```bash
cd /home/joemcmahon/greedy-shark && \
  monitor-venv/bin/python -m pytest test_monitor.py -v
```

Expected: All pass.

**Step 6: Commit**

```bash
git add grace_period_bot.py
git commit -m "feat: add !shark <id> command for on-demand streamer suspension by ID"
```

---

### Task 6: Update `!shark-help`

**Files:**
- Modify: `grace_period_bot.py:77-106` (the `shark_help` command)

**Step 1: Replace the help text**

Replace the `help_text` string in `shark_help` with:

```python
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

• `!letin <username>` - Re-enable a streamer suspended by the Shark
  Example: `!letin TestDJ`
  Note: Only works on suspensions made via !shark, not manual staff suspensions

• `!shark-status` (or `!status`) - Show current Shark monitoring status

• `!shark-help` (or `!sharkhelp`) - Show this help message

**How the Shark Works:**
• No streamer connected: 2-minute silence → staff alert
• Streamer connected: 4-minute warning alert to staff
• 10+ minutes silence: escalating urgent alerts every check interval
• Staff uses `!streamers` then `!shark <id>` to suspend when needed
• Audio detection resets all timers
• Grace period pauses monitoring entirely
""".format(grace_min=GRACE_PERIOD_MINUTES)
```

**Step 2: Confirm no syntax errors**

```bash
cd /home/joemcmahon/greedy-shark && \
  monitor-venv/bin/python -c "import grace_period_bot; print('OK')"
```

Expected: `OK`

**Step 3: Commit**

```bash
git add grace_period_bot.py
git commit -m "docs: update !shark-help to document new commands and thresholds"
```

---

## Verification Checklist

After all tasks are complete:

```bash
# Full test suite
cd /home/joemcmahon/greedy-shark && monitor-venv/bin/python -m pytest test_monitor.py -v

# Import checks
monitor-venv/bin/python -c "import monitor_stream; print('monitor OK')"
monitor-venv/bin/python -c "import grace_period_bot; print('bot OK')"

# Check STREAMER_WARNING_THRESHOLD is 4
monitor-venv/bin/python -c "from monitor_stream import STREAMER_WARNING_THRESHOLD; assert STREAMER_WARNING_THRESHOLD == 4, f'Expected 4, got {STREAMER_WARNING_THRESHOLD}'; print('Threshold OK')"
```
