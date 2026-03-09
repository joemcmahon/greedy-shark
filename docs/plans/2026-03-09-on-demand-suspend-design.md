# On-Demand Suspend & Grace Period Fix Design

**Date:** 2026-03-09

## Problem

1. Auto-suspend was locking out streamers automatically after 10 minutes of silence, which didn't work reliably and was too aggressive.
2. `!grace-status` crashed with "could not convert string to float: ''" when the grace period file existed but was empty.
3. The Azuracast live-streamer detection can sometimes show the wrong name, so suspending by name is unsafe.

## Solution Overview

Remove automatic suspension from the monitor. Replace it with:
- Staff-triggered `!shark <id>` command that suspends a specific streamer by their Azuracast numeric ID
- `!streamers` command to list all streamers with IDs so staff can identify who to target
- Fix `!grace-status` to handle empty file gracefully
- Lower the silence warning threshold from 8 to 4 minutes
- After 10 minutes, keep sending escalating alerts every check interval instead of auto-suspending

## Changes

### `monitor_stream.py`

**`handle_streamer_active_silence()`**
- Change `STREAMER_WARNING_THRESHOLD` from 8 to 4 (minutes)
- Remove the `elif ctx.consecutive_silent_checks >= STREAMER_SUSPEND_THRESHOLD` branch entirely
- Replace it with a repeated urgent alert at 10+ minutes (no suspension, no state reset)
- Remove `add_auto_suspended_streamer()` call from this function
- `suspend_streamer()` and related functions stay — used by the bot

### `grace_period_bot.py`

**`!grace-status` bug fix**
- Before calling `float(f.read().strip())`, check for empty content and handle gracefully

**New `!streamers` command**
- Calls `get_all_streamers()` (already exists in monitor_stream.py)
- Displays each streamer's numeric ID, display name, and active/suspended status
- Restricted to configured channel

**New `!shark <id>` command**
- Requires a numeric streamer ID argument (not a name)
- Validates the ID exists in Azuracast before acting
- Calls `suspend_streamer(id)` and `add_auto_suspended_streamer(id, name)`
- Confirms with streamer name in response so staff can verify correct target
- Restricted to configured channel

**`!shark-help` updates**
- Add `!streamers` and `!shark <id>` documentation
- Document the recommended workflow: `!streamers` → identify ID → `!shark <id>`
- Remove auto-suspension wording from "How the Shark Works" section
- Update thresholds: 4-minute warning, 10-minute escalating alert

## Thresholds Summary

| Threshold | Old | New |
|---|---|---|
| No-streamer silence alert | 2 min | 2 min (unchanged) |
| Streamer warning | 8 min | 4 min |
| Streamer urgent/escalating alert | 10 min (then auto-suspend) | 10 min (then repeat alert only) |
