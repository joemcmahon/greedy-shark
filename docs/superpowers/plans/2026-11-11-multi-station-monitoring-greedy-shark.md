# Multi-Station Monitoring (greedy-shark) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run one silence monitor per station and one shared Discord bot, with alerts naming the station and grace periods per station.

**Architecture:** Each monitor container is configured by env (`STATION_NAME`, `STATE_DIR`, `STREAMERS_ENABLED`) and keeps its three state files in its own `STATE_DIR`. A new `station.py` defines the station registry the single bot reads (`stations.json`); the bot's existing injectable `handle_*` functions already take file paths, so commands just resolve a station and pass that station's paths. Compose gets one service block per station from a shared template.

**Tech Stack:** Python 3.10, discord.py, pytest + pytest-asyncio, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-11-multi-station-monitoring-design.md` (this plan covers the greedy-shark half only; `discord-az-monitor` gets its own plan).

**Run tests with:** `uv run --no-project --with-requirements requirements.txt --with pytest python -m pytest -q` (baseline: 104 passed).

## Global Constraints

- Defaults must preserve today's single-station behaviour: `STATE_DIR` defaults to `.`, `STATION_NAME` to empty (no prefix), `STREAMERS_ENABLED` to `true`.
- Alert thresholds and cadence unchanged (60s check; 2/4/6-minute escalation; repeat every 2 checks).
- Every notifier message and alert, when a station name is set, carries `[<name>] ` immediately after the leading `🦈 **` (before the message text).
- `!woi`, `!cg`, `!gs`, `!shark-status` need a station name; with none or an unknown one they refuse, list valid names, and never default.
- `!streamers`, `!shark`, `!retrack`, `!unshark`, `!sharked` act only on the one station with `streamers_enabled: true`.
- A grace period silences a station's alerts whether or not a streamer is connected (added by plan review; today it only does so when a streamer is connected).
- No auto-suspend, no `!restart` command, no change to alert wording beyond the prefix.
- Branch workflow: implement on `feat/multi-station-monitoring`, branched from `docs/multi-station-design` (so the spec travels). Never commit to `main`. One commit per task; Joe splits/merges.
- Be polite to servers: no extra AzuraCast polling (streamer-less stations poll it zero times).

## Review Focus

Failure modes the spec implies (seven, not five: two were found in plan review); each has a test in the owning task.

1. A typo'd, mis-cased, or missing station name must never pause monitoring on some default station (Task 5).
2. `!woi` on one station must leave every other station's grace period and monitor state alone (Task 5).
3. `!woi` on a station with no streamer connected must actually silence its alerts and announce start and end; today it does nothing there (Task 4).
4. A `STREAMERS_ENABLED=false` monitor must never call AzuraCast's streamer check (Task 3).
5. A malformed `stations.json` (bad JSON, missing key, duplicate names ignoring case, two streamer stations) must fail fast with a clear message, not half-start the bot (Task 1).
6. A monitor started without `DISCORD_WEBHOOK_URL` (for example a compose `env_file` slip) must exit with an error, not run silently mute (Task 3).
7. With no new env vars set, files, messages, and behaviour are exactly today's (Tasks 2 and 3).

## File Structure

- Create `station.py`: `Station`, state-file naming, `load_stations`, lookup helpers. One responsibility: describing stations.
- Create `test_station.py`.
- Modify `monitor_stream.py`: env config, optional `path` params on state functions, `streamers_enabled`, station-aware notifier construction.
- Modify `notifier.py`: `station_name` prefix in `DiscordNotifier`.
- Modify `grace_period_bot.py`: station resolution, per-station handlers, startup loading.
- Modify `test_monitor.py`, `test_notifier.py`, `test_bot.py`.
- Modify `docker-compose.yml`, `README.md`, `check_shark_health.sh`, `.gitignore`; create `stations.json.example`, `stations/*.env.example`.

---

### Task 1: Station registry (`station.py`)

**Files:**
- Create: `station.py`
- Test: `test_station.py`

**Interfaces:**
- Produces:
  - `GRACE_FILENAME = ".grace_period_until"`, `SUSPENDED_FILENAME = ".auto_suspended_streamers"`, `STATE_FILENAME = ".monitor_state"`
  - `class StatePaths(NamedTuple): grace: str; suspended: str; monitor_state: str`
  - `def state_paths(state_dir: str) -> StatePaths` (joins the three filenames onto `state_dir`)
  - `@dataclass(frozen=True) class Station: name: str; state_dir: str; azuracast_station_id: str; streamers_enabled: bool = False`, with property `paths -> StatePaths`
  - `class StationConfigError(ValueError)`
  - `def load_stations(path: str) -> list[Station]`: reads a JSON list of objects with keys `name`, `state_dir`, `azuracast_station_id`, optional `streamers_enabled`
  - `def find_station(name: str | None, stations: list[Station]) -> Station | None`: case-insensitive, whitespace-trimmed, `None` for `None`/unknown
  - `def streamer_station(stations: list[Station]) -> Station | None`
  - `def station_names(stations: list[Station]) -> str`: `"radiospiral, pulse, inner, outer"` in file order

- [ ] **Step 1: Write failing tests in `test_station.py`**
  - `test_state_paths_join_filenames`: `state_paths("/s/a")` equals `StatePaths("/s/a/.grace_period_until", "/s/a/.auto_suspended_streamers", "/s/a/.monitor_state")`.
  - `test_load_stations_valid`: write a 2-entry JSON to `tmp_path`; assert names, `streamers_enabled` defaults `False`, order preserved.
  - `test_find_station_ignores_case_and_whitespace`: `find_station("  PULSE ", stations).name == "pulse"`.
  - `test_find_station_none_and_unknown_return_none`.
  - `test_load_rejects` (parametrized, each `pytest.raises(StationConfigError)`, message must name the problem): invalid JSON; not a list; empty list; entry missing `state_dir`; duplicate names `"Pulse"`/`"pulse"`; two entries with `streamers_enabled: true`.
  - `test_streamer_station_and_names`.
- [ ] **Step 2: Run** `pytest test_station.py -q`. Expected: FAIL (`ModuleNotFoundError: station`).
- [ ] **Step 3: Implement `station.py`** to the interfaces above (stdlib only; `StationConfigError` messages like `"stations.json: duplicate station name 'pulse'"`).
- [ ] **Step 4: Run** `pytest test_station.py -q`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: add station registry (station.py) for multi-station config`

---

### Task 2: Per-station state paths in `monitor_stream.py`

**Files:**
- Modify: `monitor_stream.py` (config block lines ~19-40; state functions lines ~58-166; `save_monitor_state` ~364)
- Test: `test_monitor.py`

**Interfaces:**
- Consumes: `station.state_paths` (Task 1).
- Produces (all `path` params optional; `None` means "use the module constant at call time", so existing tests that `monkeypatch` the constants keep working):
  - `check_grace_period_active(path: str | None = None) -> bool`
  - `check_and_fix_suspended_file_path(path: str | None = None) -> None`
  - `load_auto_suspended_streamers(path: str | None = None) -> dict`
  - `save_auto_suspended_streamers(suspended_dict: dict, path: str | None = None) -> None`
  - `add_auto_suspended_streamer(streamer_id, streamer_name, reason="staff action", path: str | None = None) -> None`
  - `remove_auto_suspended_streamer(streamer_id, path: str | None = None) -> None`
  - `save_monitor_state(ctx, path: str | None = None) -> None`
  - module constants `STATE_DIR` (env `STATE_DIR`, default `"."`), `STATION_NAME` (env, default `""`), `STREAMERS_ENABLED` (env; false only for `false`/`0`/`no`, case-insensitive; default true); `GRACE_PERIOD_FILE`, `AUTO_SUSPENDED_FILE`, `MONITOR_STATE_FILE` now derived from `state_paths(STATE_DIR)`.
  - `def ensure_state_dir(state_dir: str | None = None) -> None`: `os.makedirs(..., exist_ok=True)`.

- [ ] **Step 1: Write failing tests** in `test_monitor.py`:
  - `test_state_functions_use_explicit_path`: with two `tmp_path` subdirs A and B, `add_auto_suspended_streamer(1, "DJ", reason="r", path=A_file)`; `load_auto_suspended_streamers(path=A_file)` contains `"1"`, `load_auto_suspended_streamers(path=B_file) == {}`; `remove_auto_suspended_streamer(1, path=A_file)` empties A.
  - `test_grace_period_checked_per_path`: write a future timestamp to A's grace file only; `check_grace_period_active(path=A) is True`, `check_grace_period_active(path=B) is False`.
  - `test_save_monitor_state_writes_given_path`: after `save_monitor_state(make_ctx(), path=p)`, JSON in `p` has key `"state"`.
  - `test_ensure_state_dir_creates_missing_dir`: nested missing dir is created; calling twice does not raise.
  - `test_default_state_paths_are_todays_files`: `state_paths(".")` yields paths whose basenames are the three existing filenames (guards the back-compat default).
- [ ] **Step 2: Run** `pytest test_monitor.py -q`. Expected: new tests FAIL (unexpected `path` argument / missing `ensure_state_dir`).
- [ ] **Step 3: Implement** the signatures above. Resolve `path = path or CONSTANT` inside each function body (not as a default argument) so monkeypatched constants still apply. `monitor_loop` calls `ensure_state_dir()` first.
- [ ] **Step 4: Run the full suite.** Expected: all pass (104 existing + new).
- [ ] **Step 5: Commit** `feat: make monitor state files per-station via STATE_DIR`

---

### Task 3: Station name in alerts; streamer-less monitors skip the AzuraCast poll; fail fast without a webhook

**Files:**
- Modify: `notifier.py` (`DiscordNotifier`), `monitor_stream.py` (`MonitorContext`, `run_check`, `monitor_loop`)
- Test: `test_notifier.py`, `test_monitor.py`

**Interfaces:**
- Consumes: `STATION_NAME`, `STREAMERS_ENABLED` (Task 2).
- Produces:
  - `DiscordNotifier(webhook_url: str, staff_role_id: str, station_name: str = "")`: prefix is `f"[{station_name}] "` when set, else `""`. `send_message` content becomes `f"🦈 **{prefix}{message}**"`; `send_alert` header becomes `f"... **{prefix}Stream issue detected**\n..."`.
  - `MonitorContext.__init__(self, notifier, azuracast, sample_fn, streamers_enabled: bool = True)`; stores `self.streamers_enabled`.
  - `run_check`: when `ctx.streamers_enabled` is false, uses `(False, None, None)` instead of calling `ctx.azuracast.check_streamer_connected()`.
  - `monitor_loop` passes `station_name=STATION_NAME` and `streamers_enabled=STREAMERS_ENABLED`.
  - `def check_required_config(webhook_url: str | None) -> None`: if `webhook_url` is empty or `None`, logs an error naming `DISCORD_WEBHOOK_URL` and raises `SystemExit(1)`. `monitor_loop` calls it first, before any other work. (Why: `DiscordNotifier` swallows post errors, so a missing webhook would otherwise run healthy and mute.)

- [ ] **Step 1: Write failing tests**
  - `test_notifier.py`, patching `notifier.requests.post` to return `Mock(status_code=204)` and reading `call_args.kwargs["json"]["content"]`:
    - `test_message_prefixed_with_station_name`: `DiscordNotifier("u", "1", station_name="pulse").send_message("Greedy Shark is active")` posts content `"🦈 **[pulse] Greedy Shark is active**"`.
    - `test_alert_prefixed_with_station_name`: content contains `"**[pulse] Stream issue detected**"` and still starts with `"<@&1>"`.
    - `test_no_station_name_is_unchanged`: content `== "🦈 **Greedy Shark is active**"`.
  - `test_monitor.py`:
    - `test_streamerless_monitor_never_polls_azuracast`: `ctx = make_ctx(...)` with `streamers_enabled=False`, `azuracast=Mock()`, `sample_fn=Mock(return_value=None)`; after `run_check(ctx)`, `azuracast.check_streamer_connected.assert_not_called()`, `ctx.state == MonitorState.NO_STREAMER`, `ctx.consecutive_silent_checks == 1`.
    - `test_streamers_enabled_defaults_true`: default `MonitorContext(...)` still calls the check once per `run_check`.
    - `test_check_required_config_exits_without_webhook`: `check_required_config("")` and `check_required_config(None)` raise `SystemExit` with code 1; `check_required_config("https://x")` returns `None`.
- [ ] **Step 2: Run** the two files. Expected: new tests FAIL.
- [ ] **Step 3: Implement** per Interfaces. Update `make_ctx` in `test_monitor.py` to accept `streamers_enabled=True`.
- [ ] **Step 4: Run the full suite.** Expected: all pass.
- [ ] **Step 5: Commit** `feat: prefix alerts with station name; skip streamer poll when streamers disabled; fail fast without webhook`

---

### Task 4: Grace period applies when no streamer is connected

**Files:**
- Modify: `monitor_stream.py` (`determine_next_state` ~262, `handle_state_transition` ~284)
- Test: `test_monitor.py`

**Interfaces:**
- Consumes: `MonitorState`, `MonitorContext` (existing).
- Produces: the new `determine_next_state(ctx, is_streamer_connected, grace_period_active)` table (first matching row wins):

| current state | grace active | streamer connected | next |
|---|---|---|---|
| `STREAMER_ACTIVE` | yes | any | `GRACE_PERIOD` |
| `NO_STREAMER` | yes | any (**new**: also when no streamer) | `GRACE_PERIOD` |
| `GRACE_PERIOD` | yes | no (**new**: stay, do not drop to `NO_STREAMER`) | `None` |
| `GRACE_PERIOD` | no | yes / no | `STREAMER_ACTIVE` / `NO_STREAMER` |
| `NO_STREAMER` | no | yes | `STREAMER_ACTIVE` |
| `STREAMER_ACTIVE` | no | no | `NO_STREAMER` |
| anything else | | | `None` |

  Messages from `handle_state_transition` (through `ctx.notifier.send_message`):
  - `NO_STREAMER` → `GRACE_PERIOD` with `streamer_name is None`: `"Grace period activated. Monitoring paused."`. With a streamer name (existing path): no message, unchanged.
  - `GRACE_PERIOD` → `NO_STREAMER`: `"Grace period expired. Normal monitoring resumed."`.
  - `GRACE_PERIOD` → `STREAMER_ACTIVE`: when `ctx.streamer_name` is still `None`, first set `ctx.streamer_id`/`ctx.streamer_name` from the arguments (a streamer connected during grace), then send the existing "expired for '<name>'" message.

- [ ] **Step 1: Write failing tests** in `test_monitor.py` (add; leave the existing `TestDetermineNextState` tests, which must still pass):
  - `test_no_streamer_with_grace_goes_to_grace_period`: `determine_next_state(ctx, False, True) == GRACE_PERIOD` from `NO_STREAMER`.
  - `test_grace_period_stays_when_streamer_absent_and_grace_active`: from `GRACE_PERIOD`, `(False, True)` → `None`.
  - `test_grace_silences_alerts_with_no_streamer`: run `run_check` four times with `sample_fn=Mock(return_value=None)`, a `FileNotifier`, and `check_grace_period_active` patched to return `True`; assert the notifier recorded exactly one record, the "Grace period activated" message, and no `alert` records.
  - `test_grace_expiry_resumes_alerts_with_no_streamer`: after the above, patch grace to `False`; assert one more "Grace period expired" message, state `NO_STREAMER`, and that continued silence alerts at the normal threshold again.
  - `test_streamer_connecting_during_grace_is_recorded_on_expiry`: state `GRACE_PERIOD` with no streamer info; `handle_state_transition(ctx, STREAMER_ACTIVE, "DJ", 5)` sets `ctx.streamer_name == "DJ"`, `ctx.streamer_id == 5`.
- [ ] **Step 2: Run** `pytest test_monitor.py -q`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** the table and messages above. Reuse the existing counter reset in `handle_state_transition`.
- [ ] **Step 4: Run the full suite.** Expected: all pass.
- [ ] **Step 5: Commit** `fix: grace periods also silence alerts when no streamer is connected`

---

### Task 5: Bot resolves a station for grace-period commands

**Files:**
- Modify: `grace_period_bot.py` (config block, `handle_working_on_it`, `handle_cancel_grace`, `handle_grace_status`, `handle_shark_status`, their `@bot.command` wrappers)
- Test: `test_bot.py`

**Interfaces:**
- Consumes: `Station`, `find_station`, `station_names`, `StationConfigError`, `load_stations` (Task 1); `load_auto_suspended_streamers(path=)` (Task 2).
- Produces:
  - Module globals `STATIONS: list[Station] = []` (set at startup), `STATIONS_FILE = os.getenv("STATIONS_FILE", "stations.json")`.
  - `def tagged(message: str, station_name: str = "") -> str`: `f"[{station_name}] {message}"` if name set, else unchanged.
  - `async def with_station(name: str | None, send_fn, stations: list[Station], action) -> None`: if `find_station` finds nothing, sends one refusal message containing the valid names (from `station_names`) and, for an unknown non-empty name, that name; **does not call `action`**. Otherwise `await action(station)`.
  - `handle_working_on_it`, `handle_cancel_grace`, `handle_grace_status` gain keyword `station_name: str = ""` and wrap every message they send in `tagged(...)`. `handle_shark_status` gains `station_name: str = ""` and tags its message the same way.
  - Command wrappers take `station: str = None` and call `with_station`, passing `grace_file=st.paths.grace` etc. `!shark-status` also passes `state_file=st.paths.monitor_state`, `load_fn=lambda: load_auto_suspended_streamers(st.paths.suspended)`, and a per-station `AzuracastClient(AZURACAST_BASE_URL, AZURACAST_API_KEY, st.azuracast_station_id)`.
  - Startup (`__main__`): `STATIONS = load_stations(STATIONS_FILE)`; on `StationConfigError` print the message and `exit(1)`; call `check_and_fix_suspended_file_path(st.paths.suspended)` for every station.

- [ ] **Step 1: Write failing tests** in `test_bot.py` (using the existing `capture()` helper):
  - `test_with_station_refuses_none_and_lists_names`: stations radiospiral/pulse; `name=None`; assert one message containing `"radiospiral, pulse"` and that `action` (an `AsyncMock`) was not awaited.
  - `test_with_station_refuses_unknown_naming_it`: `"pulze"` → message contains `"pulze"` and the valid names; `action` not awaited.
  - `test_with_station_is_case_insensitive`: `"PULSE"` → `action` awaited once with the `pulse` Station.
  - `test_woi_on_one_station_leaves_others_alone`: two stations with separate `tmp_path` dirs; `await handle_working_on_it(send, grace_file=A.paths.grace, station_name="pulse")`; assert A's grace file exists and B's does not; `handle_grace_status(send, grace_file=B.paths.grace, station_name="inner")` reports no active grace period; `handle_cancel_grace(send, grace_file=B.paths.grace, ...)` reports nothing to cancel and A's file still exists.
  - `test_handlers_tag_messages_with_station_name`: each of the four handlers' first message starts with `"[pulse] "` when `station_name="pulse"`; with the default `""` they equal today's text (existing tests already cover the untagged text and must still pass).
- [ ] **Step 2: Run** `pytest test_bot.py -q`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** per Interfaces. `with_station`'s refusal wording is yours but must include the names and an example like `!woi pulse`.
- [ ] **Step 4: Run the full suite.** Expected: all pass.
- [ ] **Step 5: Commit** `feat: bot grace-period and status commands take a station name`

---

### Task 6: Streamer commands route to the streamers station; help text

**Files:**
- Modify: `grace_period_bot.py` (`handle_shark_help`, streamer command wrappers: `!streamers`, `!shark`, `!retrack`, `!unshark`, `!sharked`)
- Test: `test_bot.py`

**Interfaces:**
- Consumes: Task 5 globals and helpers; `streamer_station` (Task 1); `add_auto_suspended_streamer`/`remove_auto_suspended_streamer`/`load_auto_suspended_streamers` with `path=` (Task 2).
- Produces:
  - `class StreamerTools(NamedTuple): client; track_fn; untrack_fn; load_fn`
  - `def streamer_tools(station: Station, base_url: str, api_key: str) -> StreamerTools`: `client` is an `AzuracastClient` for `station.azuracast_station_id`; `track_fn`/`untrack_fn`/`load_fn` are `functools.partial`s of the Task 2 functions bound to `station.paths.suspended`.
  - `async def with_streamer_station(send_fn, stations, action) -> None`: if `streamer_station(stations)` is `None`, sends one message saying no station has streamers enabled and does not call `action`; else `await action(station)`.
  - `handle_shark_help(send_fn, grace_minutes=..., station_names: str = "")` adds a "Stations:" line listing the names and shows `<station>` in the `!woi`, `!cg`, `!gs`, `!shark-status` usage lines.

- [ ] **Step 1: Write failing tests**
  - `test_with_streamer_station_none_configured`: stations without streamers → one message, `action` not awaited.
  - `test_with_streamer_station_routes_to_streamer_station`: `action` awaited with the streamers-enabled Station.
  - `test_streamer_tools_write_only_to_that_stations_dir`: two `tmp_path` stations; `t = streamer_tools(A, "http://x", "k")`; `t.track_fn(7, "DJ", reason="r")`; `t.load_fn()` has `"7"`; `load_auto_suspended_streamers(path=B.paths.suspended) == {}`; `t.untrack_fn(7)` empties A.
  - `test_streamer_tools_client_uses_station_id`: `t.client.station_id == A.azuracast_station_id`.
  - `test_help_lists_stations_and_station_argument`: help text contains `"radiospiral, pulse"` and `"!woi <station>"`.
- [ ] **Step 2: Run** `pytest test_bot.py -q`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** per Interfaces; the five streamer wrappers use `with_streamer_station` and `streamer_tools(...)` instead of the module-level `real_client` and default track functions. Remove `real_client` and the now-unused module constants `GRACE_PERIOD_FILE`/`AUTO_SUSPENDED_FILE`/`AZURACAST_STATION_ID` if nothing else references them (grep first).
- [ ] **Step 4: Run the full suite.** Expected: all pass.
- [ ] **Step 5: Commit** `feat: route streamer commands to the streamers station; update help`

---

### Task 7: Compose template, env examples, docs, health script

**Files:**
- Modify: `docker-compose.yml`, `README.md`, `check_shark_health.sh`, `.gitignore`
- Create: `stations.json.example`, `stations/radiospiral.env.example`, `stations/pulse.env.example` (inner/outer follow the same pattern; one pattern file per extra station)

**Interfaces:**
- Consumes: env vars from Tasks 2-3; `stations.json` schema from Task 1.
- Produces: a runnable compose stack. Station names `radiospiral` (streamers), `pulse`, `inner`, `outer` are placeholders from the wiki: **confirm the AzuraCast shortcodes on the test server** (`radiospiral` is the main station's known shortcode).

- [ ] **Step 1: Rewrite `docker-compose.yml`:**
  - Shared `x-shark-common` block (build, `network_mode: host`, `restart: unless-stopped`). It does **not** hold `env_file`: a service-level `env_file` replaces a merged one rather than adding to it, so every service lists both files itself.
  - One monitor service per station (`monitor-radiospiral`, `monitor-pulse`, `monitor-inner`, `monitor-outer`) `env_file: [.env, stations/<name>.env]` (later file wins), volume `./state/<name>:/state`, `command: python monitor_stream.py`, and a healthcheck that checks `$STATE_DIR/.monitor_state` mtime within 120s (`python -c` using `os.getenv('STATE_DIR','.')`).
  - One `bot` service (`env_file: [.env]`): volumes `./state:/state` and `./stations.json:/app/stations.json:ro`, `command: python grace_period_bot.py`, healthcheck unchanged, `depends_on` all monitors.
  - No `container_name` (so a test stack with `-p spiraltest` cannot collide with prod). Remove the unused `networks:` block.
  - Each `stations/<name>.env.example` sets `STATION_NAME`, `STREAM_URL`, `AZURACAST_STATION_ID`, `STATE_DIR=/state`, and `STREAMERS_ENABLED=false` for the three streamer-less stations (omitted/true for radiospiral).
  - `stations.json.example` lists the four stations with `"state_dir": "/state/<name>"`, `radiospiral` with `"streamers_enabled": true`.
- [ ] **Step 2: `.gitignore`:** add `stations/*.env`, `stations.json`, `state/`. Keep `*.example` tracked.
- [ ] **Step 3: Update `README.md`:** a "Multiple stations" section (the per-station env files, `stations.json`, `docker compose -p <project> up -d` for a separate test stack with its own Discord webhooks), the three new env vars in the variable table, and migration for an existing single-station install: `mkdir -p state/radiospiral && mv .grace_period_until .auto_suspended_streamers .monitor_state state/radiospiral/` before first `up` (keeps the suspended-streamer list).
- [ ] **Step 4: Rewrite `check_shark_health.sh`** to loop over the containers of the current compose project (`docker ps -a --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}" --format '{{.Names}}'`), printing status/health/restarts for each and keeping the exit codes 0 (all running and healthy), 1 (running, unhealthy), 2 (any not running).
- [ ] **Step 5: Verify:**
  - `python3 -c "import station; print(station.station_names(station.load_stations('stations.json.example')))"` prints `radiospiral, pulse, inner, outer`.
  - Copy each `*.env.example` to `*.env` in a scratch copy and run `docker compose config -q` (expected: no output, exit 0). If Docker is not installed on this Mac (it is not, as of 2026-11-11), say so and leave this and the next check for the test server.
  - On the test server run `docker compose config` and confirm that **each monitor's environment contains both** `DISCORD_WEBHOOK_URL` (from `.env`) and `STATION_NAME` (from its station file). `-q` alone does not prove the shared file was kept.
  - `bash -n check_shark_health.sh` passes.
- [ ] **Step 6: Commit** `feat: per-station compose template, example configs, docs, health script`

---

## Self-review (spec coverage)

- Per-station monitor env, `STATE_DIR`, name in alerts, streamer poll skipped, fail-fast without webhook: Tasks 2-3.
- Per-station grace that also works with no streamer connected (design goal 2): Task 4 (spec needs one matching line; see handoff).
- Single bot, station argument, refuse-don't-default, per-station grace isolation: Task 5.
- Streamer commands only on the streamers station: Task 6.
- Compose template, explicit per-service env lists, test-stack isolation (project name, no `container_name`, separate state dirs and webhooks): Task 7.
- Rollout step 5's restarter and everything in `discord-az-monitor` (hardcoded station, watchdog, status board, SQLite store) belong to the second plan. The restarter's behaviour is still unverified on prod.
- Alerts that name the station in a server-wide outage remain four pings (accepted Known limitation, no task).
