# Multi-Station Monitoring: Design

**Date:** 2026-11-11
**Status:** Draft, awaiting Joe's approval

Covers two repos: `greedy-shark` (silence monitor + Discord bot) and `discord-az-monitor`
(now-playing monitor, runs on prod as `az-now-playing`). Each gets its own implementation plan.

## Intent

RadioSpiral will run four stations on one AzuraCast server. Today greedy-shark watches one
station for silence, and `discord-az-monitor` posts now-playing for one. Staff need to learn
when *any* of the four goes unexpectedly quiet, and see at a glance what each is playing.

Typical failures: on the main station, a streamer falls asleep and stays connected; on the other
three (no streamers), AutoDJ stalls. Staff get a Discord alert and fix it in AzuraCast.

## Goals

1. Silence alerts for all four stations, in the existing staff channel, each naming its station.
2. Grace periods (`!woi`, `!cg`, `!gs`) are **per station**; each station can need maintenance alone.
3. Now-playing for the main station is unchanged (posts to the main channel).
4. A second Discord channel, the **status board**, shows one message per *secondary* station
   (three), edited in place on each track change. No new posts, no pings.
5. Develop and test against a separate test AzuraCast before anything touches prod.

## Non-goals

- Restarting AutoDJ from Discord (`!restart`). **Decided:** staff do it in AzuraCast, deliberately,
  to avoid fat-fingered commands against the wrong station.
- Auto-suspending streamers. **Decided:** staff-only suspension stays.
- Changing alert thresholds or cadence (60s check, existing escalation).
- A container orchestrator (Kubernetes/Swarm). Docker Compose is enough on one host.
- Putting the main station on the status board. **Decided** against.

## Anti-goals (would make this a failure even if it works)

- Any test run posting to a prod Discord channel, or sharing prod state files.
- A typo in a command pausing monitoring on the wrong station (see bot rules below).
- Existing prod deployment breaking on upgrade (defaults must preserve today's behaviour).
- Four stations' now-playing history interleaved in one channel.

## Decisions made by Joe

- Separate container per station for the monitors; one shared bot.
- Grace period per station.
- Alerts go to Discord as now; remediation is manual in AzuraCast.
- Status board: main station only in the main channel; the other three on the board only.
- Work happens on the test server first (separate AzuraCast, four stations, partial library).

## Design

### greedy-shark monitor (one container per station)

- New env `STATION_NAME` (display name, shown in every alert and state message).
- New env `STATE_DIR` (default `.`, so today's single-station deploy is unchanged). The three
  state files (`.grace_period_until`, `.auto_suspended_streamers`, `.monitor_state`) live there.
- New env `STREAMERS_ENABLED` (default `true`). For the three secondary stations set `false`:
  the monitor skips the AzuraCast "is a streamer connected?" poll and stays in the no-streamer
  silence path. *(My call: explicit flag rather than inferring from missing config.)*
- Every notifier message/alert is prefixed with the station name.
- Heartbeat: `.monitor_state` mtime stays the health signal (existing compose healthcheck).

### greedy-shark bot (single container)

- Configured with the list of stations (name -> state dir -> AzuraCast station id).
  *(Builder's call: env var vs small config file; keep it simple and documented.)*
- `!woi`, `!cg`, `!gs`, `!shark-status` take a station name argument. **With no/unknown name they
  refuse and list valid names; they never default.** *(Approved in playback.)*
- `!streamers`, `!shark`, `!retrack`, `!unshark`, `!sharked` apply only to stations with
  `STREAMERS_ENABLED=true` (currently the main station) and operate on that station's client.
- Help text updated. Reads/writes each station's state dir, so those dirs are mounted into the bot.
- One Discord token, one process (four bots on one token would answer every command four times).

### discord-az-monitor (one container per station; one image)

- Move the hardcoded host/station at `azmonitor.py:67` (`build_sse_client("spiral.radio",
  "radiospiral")`) to env settings.
- New mode `STATUS_BOARD` (default off = today's behaviour: post a new message per track).
  When on: on first run POST to the webhook with `?wait=true`, save the returned message ID in a
  state file; on each track change PATCH that message. If the saved message is gone (404), post a
  new one and save the new ID. *(Discord webhook message-edit endpoint is from memory, not yet
  tested; the test server is where this gets verified. Fallback if it fails: delete + repost.)*
- Heartbeat file touched on a timer, and on SSE activity, so health does not depend on track
  changes (a long track means no edits for many minutes; that is normal).

### Compose and containers

- One compose file with a shared template block (YAML anchors / `x-` extension) and a short entry
  per station per service, each with its own env file. Adding a station = one new block.
- Services: 4 shark monitors, 1 bot, 1 now-playing (main, post mode), 3 now-playing (status board).
- A single restarter that watches containers by label and restarts unhealthy ones. **Open item
  (Joe):** on prod, `discord-az-monitor-restarter-1` (a `docker:cli` loop) already exists and is
  believed to restart the now-playing monitor when it stops reporting. Its exact behaviour is
  unverified; its definition is not in either local repo. Verify on prod
  (`docker logs` / `docker inspect`) before deciding whether to generalise it.

### Test environment

- Separate AzuraCast (four stations, partial library). Separate Discord test channels and
  webhooks (main, status board, staff alerts). Separate compose project name and container names
  (must not collide with `greedy-shark-*`, `az-now-playing`). Separate state directories.
- Be polite to servers: keep the existing pacing; no extra polling beyond one check per station
  per interval.

## Rollout order

1. State/env parametrisation + station name in alerts (greedy-shark), tested single-station.
2. Four monitors on the test server; verify per-station isolation and alerts.
3. Bot station arguments; verify `!woi` on one station leaves the others alerting.
4. `discord-az-monitor` env config, then status-board mode; verify edit-in-place on test.
5. Compose template + restarter; then prod, one station at a time.

## Left to the builder

Config format for the bot's station list; names of env vars beyond those above; test layout;
exact alert wording (must start with the station name).

## Verified vs not

- Verified: current code is single-station (module-level env, three state files in cwd, one bot
  client, hardcoded station in discord-az-monitor); prod runs `az-now-playing` (`azmonitor.py`),
  the shark monitor/bot, and a `discord-az-monitor-restarter-1` container.
- Not verified: Discord webhook edit behaviour; the restarter's logic; local `discord-az-monitor`
  was behind GitHub at the time of writing (Joe is syncing it).
