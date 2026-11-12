# Multi-Station Monitoring: Design

**Date:** 2026-11-11
**Status:** Draft, revised after pushback reviews 2026-11-11. The greedy-shark half is implemented and merged; the discord-az-monitor half was re-baselined on `origin/main` (8be61ac) and awaits its plan.

Covers two repos: `greedy-shark` (silence monitor + Discord bot) and `discord-az-monitor`
(now-playing monitor, GitHub `joemcmahon/python-azuracast-monitor`, runs on prod as `az-now-playing`).
Each gets its own implementation plan.

## Intent

RadioSpiral will run four stations on one AzuraCast server. Today greedy-shark watches one
station for silence, and `discord-az-monitor` posts now-playing for one. Staff need to learn
when *any* of the four goes unexpectedly quiet, and see at a glance what each is playing.

Typical failures: on the main station, a streamer falls asleep and stays connected; on the other
three (no streamers), AutoDJ stalls. Staff get a Discord alert and fix it in AzuraCast.

## Goals

1. Silence alerts for all four stations, in the existing staff channel, each naming its station.
2. Grace periods (`!woi`, `!cg`, `!gs`) are **per station**; each station can need maintenance alone.
   A grace period silences that station's alerts whether or not a streamer is connected.
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
- A "metadata out of sync" notice in Discord when the now-playing monitor cannot reconnect.
  **Decided** against for now: if the server is unreachable the music stopping is the louder
  signal; the heartbeat goes stale, the container is reported unhealthy and restarted. Revisit if a
  frozen board misleads people.
- Coordinating alerts across stations during a server-wide outage (see Known limitations).

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

**Baseline:** this section is written against `origin/main` of the repo (8be61ac, 2026-11-11).
Already there, not to be redone: the shortcode is passed into `run(client, callback, shortcode)`
and builds the `station:<shortcode>` connect key; `ResilientRunner` reconnects with exponential
backoff plus jitter (cap 5 minutes) and infinite retries; the SSE read timeout is 300s; logging,
environment validation, signal handling, tests and CI exist. Line numbers below are from that
version; re-check after pulling.

- **Station settings.** The host and shortcode (`STATION_SERVER`, `STATION_SHORTCODE` in
  `azmonitor.py`, currently `spiral.radio` / `radiospiral`) come from env, with those defaults so
  prod is unchanged. The AutoDJ display name (`"Spud the Ambient Robot"` in `extract_metadata`) is a
  setting defaulting to that value.
- **No secrets in the image.** The `Dockerfile` must not copy `.env` (today: `COPY *.py .env .`),
  and a `.dockerignore` excludes it. Each service gets its settings from compose `env_file`. The
  existing main container's webhook currently comes only from the baked `.env`, so its migration
  must give it an `env_file` before the first rebuild, or it loses its webhook.
- **Status-board mode** (`STATUS_BOARD`, default off = today's behaviour: post a new message per
  track). When on, the monitor reads its own `STATUS_BOARD_WEBHOOK` and never reads
  `NOW_PLAYING_WEBHOOK`; if `STATUS_BOARD_WEBHOOK` is empty it exits non-zero with a clear message.
  (Reason: a missing variable must not fall back to the main channel's webhook.) The first run posts
  and saves the returned message ID; each track change edits that message; if the saved message is
  gone (404) it posts a new one and saves the new ID. `discord-webhook==1.3.1` provides this
  (`execute()` sets the message id, `edit()` PATCHes; confirmed in the library source, not yet tested
  against Discord, so verify on the test server; fallback if it fails: delete and repost).
- **Timeouts.** Every webhook `execute`/`edit` has an explicit timeout (default 10s, configurable);
  the library's default is none, so a Discord call that never answers would block the SSE loop.
  The SSE read timeout is configurable (`SSE_READ_TIMEOUT`, default 300).
- **One retry layer.** Keep `ResilientRunner` and its infinite retries. No second watchdog, no
  ping-derived timer, and no exit-after-retries.
- **Heartbeat and health.** A heartbeat file is touched only when an SSE message is received or a
  (re)connection succeeds, never from a timer. A Docker healthcheck reads its mtime, with a window
  longer than the read timeout. A monitor that cannot stay connected therefore goes unhealthy and is
  restarted by the restarter (see Compose). A long track means no status edits for minutes, but
  keepalives still arrive, so this is normal. Reconnecting re-delivers the current track; within
  one process the existing comparison with the last track prevents a duplicate post or edit. After
  a restart, main-channel mode posts the current track once more (in-memory comparison).
- **Metadata parsing must not abort the connection.** The title split is
  `track = ' - '.join(parts[:-1])` with the last part as the album (the current
  `parts[:-2].join(' - ')` raises `AttributeError` for a title with two or more " - " parts and an
  empty album), with a regression test. A parse failure on one event is logged and falls back to the
  raw title and artist; it never escapes `run()`, so one bad event cannot cause a reconnect loop.
- **Status state.** Message ID, plus the time and value last seen, is kept in a per-station SQLite
  file in a mounted volume, so a container recreate (every deploy) keeps editing the same message
  instead of posting a new one and orphaning the old. Each status message starts with the station
  name so a stray orphan is recognisable.

### Compose and containers

- One compose file with a shared template block (YAML anchors / `x-` extension) and a short entry
  per station per service, each with its own env file. Adding a station = one new block.
- Services: 4 shark monitors, 1 bot, 1 now-playing (main, post mode), 3 now-playing (status board).
- Every now-playing container mounts its own state directory (same pattern as the shark
  monitors); the status-board ones require it.
- One restarter for all now-playing containers, selected by a label rather than by container name.
  It is the existing `docker:cli` loop, generalised. As found in the repo's `docker-compose.yml`, the
  prod restarter is not health-based: it sleeps until 05:00 and runs `docker restart az-now-playing`
  once a day. The generalised one keeps that daily restart and also restarts any labelled container
  reported unhealthy. The repo's fixed `container_name: az-now-playing` cannot serve several
  stations: the main container may keep that name for continuity; the others are named per station.

### Test environment

- Separate AzuraCast (four stations, partial library). Separate Discord test channels and
  webhooks (main, status board, staff alerts). Separate compose project name and container names
  (must not collide with `greedy-shark-*`, `az-now-playing`). Separate state directories.
- Be polite to servers: keep the existing pacing; no extra polling beyond one check per station
  per interval.

## Known limitations

- A server-wide outage silences all four stations at once; each monitor alerts independently, so
  staff get one role ping per station per alert cycle until it is resolved. Accepted for now;
  observe it on the test server (stop the AzuraCast container) before deciding whether to change it.

## Rollout order

1. State/env parametrisation + station name in alerts (greedy-shark), tested single-station.
2. Four monitors on the test server; verify per-station isolation and alerts.
3. Bot station arguments; verify `!woi` on one station leaves the others alerting.
4. `discord-az-monitor`, on top of `origin/main`: drop `.env` from the image and move settings to
   `env_file` (main container first, same behaviour), then env-driven station settings and parsing
   fix, then timeouts and heartbeat, then status-board mode; verify edit-in-place on test.
5. Compose template + generalised restarter; then prod, one station at a time.

## Left to the builder

Config format for the bot's station list; names of env vars beyond those above; test layout;
exact alert wording (must start with the station name).

## Verified vs not

- Verified (code read 2026-11-11): the greedy-shark half is implemented, merged and tested (170
  tests). `origin/main` of discord-az-monitor already has `ResilientRunner`, a 300s SSE read timeout,
  the shortcode-driven connect key, tests and CI; its `Dockerfile` copies `.env` into the image and
  its compose file's restarter is a daily 05:00 `docker restart az-now-playing`;
  `discord-webhook==1.3.1` has no default timeout and supports `execute()` id capture and `edit()`;
  the three-part-title `AttributeError` reproduces against the GitHub version.
- Not verified: Discord webhook edit behaviour against Discord; that AzuraCast's SSE `connect`
  message carries `ping` and keepalives arrive about every 15s (Joe's experience with another
  client; confirm on the test server); that the prod container was built from `origin/main`
  (assumed); Compose behaviours (profiles, orphans, missing bind mounts) for the greedy-shark half
  are for the test server.
