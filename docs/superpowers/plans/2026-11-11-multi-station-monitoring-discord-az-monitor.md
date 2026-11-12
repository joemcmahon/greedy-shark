# Multi-Station Monitoring (discord-az-monitor) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `discord-az-monitor` run once per station from one image: main station posts as today, three secondary stations keep one edited-in-place status message each, with no secrets in the image and a heartbeat-based health signal.

**Architecture:** A new `settings.py` is the single place env is read and validated. `azclient.py`/`resilient_runner.py` gain parameters (AutoDJ name, read timeout, heartbeat hooks) and a per-event parse guard. A new `status_board.py` (SQLite store + edit-in-place sender) is selected by `azmonitor.py` when `STATUS_BOARD` is on. Compose runs one service per station plus one generalised restarter.

**Tech Stack:** Python 3.10, `discord-webhook==1.3.1`, `sseclient-py`, stdlib `sqlite3`/`unittest` (the repo's test framework; CI runs it), Docker Compose.

**Spec:** `/Users/joemcmahon/Code/radiospiral/greedy-shark/docs/superpowers/specs/2026-11-11-multi-station-monitoring-design.md`, section "discord-az-monitor" and "Compose and containers" (branch `docs/az-monitor-rebaseline`).

**Repo and baseline:** `~/Code/radiospiral/discord-az-monitor` (GitHub `joemcmahon/python-azuracast-monitor`), at `origin/main` 8be61ac. Work on branch `feat/multi-station` from there (a worktree is preferred); never commit to `main`. The repo's local `main` has no upstream set, so compare against `origin/main` explicitly. Test command: `uv run --no-project --python 3.10 --with-requirements requirements.txt python -m unittest discover -v` (baseline: 17 tests pass, `test_azclient` only). Docker is not installed on this Mac.

## Global Constraints

- With no new env vars set and `NOW_PLAYING_WEBHOOK` set, behaviour equals today's: server `spiral.radio`, shortcode `radiospiral`, AutoDJ name `"Spud the Ambient Robot"`, a new message per track to `NOW_PLAYING_WEBHOOK`.
- `ResilientRunner` stays the only retry layer (infinite retries, existing backoff). No second watchdog, no ping-derived timer, no exit-after-retries.
- Status-board mode reads `STATUS_BOARD_WEBHOOK` only and never `NOW_PLAYING_WEBHOOK`; an empty value exits non-zero with a message naming the variable.
- The image never contains `.env`; each service gets settings from compose `env_file`. Status-board services must NOT load the main `.env`.
- Never log or echo a webhook URL or token (error messages name the variable, not the value).
- Every webhook `execute`/`edit` carries an explicit timeout (default 10s); SSE read timeout default 300s, configurable.
- Python 3.10; no new third-party dependencies (`sqlite3` is stdlib); CI runs `python -m unittest`.
- Heartbeat is touched only on a received SSE event or a successful (re)connect, never from a timer.
- A connection attempt fails if the SSE endpoint answers anything other than HTTP 200, or if the connection closes before delivering a single event. Both go through `ResilientRunner`'s retry count and backoff (never an immediate reconnect), and the first event delivered on a connection resets the backoff.
- `.gitignore` covers `stations/*.env`, `state/` and `*.sqlite3` (real station env files hold webhook secrets); `*.example` files stay tracked.

## Review Focus

1. Status mode with only `NOW_PLAYING_WEBHOOK` set (or a blank `STATUS_BOARD_WEBHOOK`) must refuse to start, never post to the main channel (Tasks 1 and 6).
2. A container recreate must keep editing the same message, not post a new one (Task 5).
3. A deleted status message (404 on edit) must repost; any other webhook failure must not crash or lose the saved ID (Task 5).
4. One malformed or unparseable event must not abort the connection or cause a reconnect loop (Task 2).
5. A Discord call that never answers must fail within the timeout (Task 6), and `DRY_RUN` must never construct a webhook (Task 6).
6. The image must never contain `.env` (Task 3).
7. A non-200 reply from the SSE endpoint, or a connection that closes without delivering an event, must back off and never spin (Task 4).
8. A real `stations/<name>.env` must never be committable (Tasks 3 and 7).
9. A restarter started after 05:00 must not restart every monitor on startup (Task 7).

## File Structure

- Create `settings.py`, `heartbeat.py`, `status_board.py`, `healthcheck.py` (tiny CLI around `heartbeat.check_heartbeat`), `restarter.sh`, `.dockerignore`, `stations/*.env.example`.
- Create tests: `test_settings.py`, `test_heartbeat.py`, `test_status_board.py`, `test_azmonitor.py`, `test_deploy_config.py`, `test_restarter.py`; extend `test_azclient.py` and add runner tests (`test_resilient_runner.py`).
- Modify `azclient.py`, `resilient_runner.py`, `azmonitor.py`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`, `.github/workflows/test.yml`.

---

### Task 1: Settings (`settings.py`)

**Files:** Create `settings.py`; Test `test_settings.py`

**Interfaces:**
- Produces:
  - `DEFAULT_AUTODJ_NAME = "Spud the Ambient Robot"`, `WEBHOOK_PREFIX = "https://discord.com/api/webhooks/"`
  - `class SettingsError(ValueError)`
  - `@dataclass(frozen=True) class Settings`: `station_server: str`, `station_shortcode: str`, `station_name: str`, `autodj_name: str`, `status_board: bool`, `webhook_url: str`, `state_dir: str`, `webhook_timeout: float`, `sse_read_timeout: float`, `dry_run: bool`
  - `parse_bool(value: str | None, default: bool = False) -> bool` (true for `1/true/yes/on`, false for `0/false/no/off`, case-insensitive, stripped; anything else or `None` returns `default`)
  - `load_settings(environ: Mapping[str, str]) -> Settings`. Env names: `STATION_SERVER` (default `spiral.radio`), `STATION_SHORTCODE` (`radiospiral`), `STATION_NAME` (default: the shortcode), `AUTODJ_NAME`, `STATUS_BOARD` (false), `NOW_PLAYING_WEBHOOK`, `STATUS_BOARD_WEBHOOK`, `STATE_DIR` (`.`), `WEBHOOK_TIMEOUT` (10), `SSE_READ_TIMEOUT` (300), `DRY_RUN` (false). `webhook_url` is chosen by mode: `STATUS_BOARD_WEBHOOK` when status mode is on, else `NOW_PLAYING_WEBHOOK`; it must be non-blank (stripped) and start with `WEBHOOK_PREFIX`, except when `dry_run` is on. Timeouts must be positive numbers. Every `SettingsError` message names the variable and never contains a webhook value.

- [ ] **Step 1: Write failing tests** (`unittest`, `test_settings.py`):
  - `test_defaults_preserve_prod`: `{NOW_PLAYING_WEBHOOK: valid}` gives server `spiral.radio`, shortcode `radiospiral`, station_name `radiospiral`, AutoDJ default, `status_board False`, `webhook_timeout 10.0`, `sse_read_timeout 300.0`, `state_dir "."`.
  - `test_status_mode_requires_its_own_webhook`: `{STATUS_BOARD: "true", NOW_PLAYING_WEBHOOK: valid}` raises `SettingsError` whose text contains `STATUS_BOARD_WEBHOOK` and does not contain the `NOW_PLAYING_WEBHOOK` value.
  - `test_status_mode_uses_status_webhook`: with `STATUS_BOARD_WEBHOOK` valid, `settings.webhook_url` equals it even if `NOW_PLAYING_WEBHOOK` is also set.
  - `test_invalid_webhooks_rejected` (subTest each): missing, `"   "`, `"http://example.com/x"` raise `SettingsError`.
  - `test_dry_run_allows_missing_webhook`.
  - `test_numeric_settings_validated`: `WEBHOOK_TIMEOUT="abc"`, `"0"`, `"-5"` raise `SettingsError` naming `WEBHOOK_TIMEOUT`.
  - `test_parse_bool` table, including `None`, `""`, `" TRUE "`, `"off"`, `"maybe"` (returns default).
- [ ] **Step 2: Run** `python -m unittest test_settings` (use the test command above with the module name). Expected: FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** `settings.py` to the interface (stdlib only).
- [ ] **Step 4: Run** the new tests, then the full suite. Expected: pass.
- [ ] **Step 5: Commit** `feat: add settings module with mode-specific webhook selection`

---

### Task 2: Parsing fix, per-event guard, AutoDJ name setting

**Files:** Modify `azclient.py` (`extract_metadata`, `run`); Test `test_azclient.py`

**Interfaces:**
- Consumes: `DEFAULT_AUTODJ_NAME` (Task 1).
- Produces:
  - `extract_metadata(np: dict, autodj_name: str = DEFAULT_AUTODJ_NAME) -> NowPlayingResponse`. Title splitting: with exactly two ` - ` parts, unchanged (`track = parts[0]`, `album = parts[1]`); with more, `track = ' - '.join(parts[:-1])`, `album = parts[-1]`. The AutoDJ name replaces the hardcoded string.
  - `fallback_metadata(np, autodj_name=DEFAULT_AUTODJ_NAME) -> NowPlayingResponse | None`: built defensively with `.get` from the raw `now_playing.song` title/artist (album and art empty if missing, duration `"00:00:00"`, `start = datetime.now()`); `None` when there is no title and no artist or `np` is not a dict.
  - `run(client, callback, shortcode, autodj_name=DEFAULT_AUTODJ_NAME)`: for each event, invalid JSON, a payload with neither `connect` nor `channel`, or a payload missing the expected keys is logged at debug and skipped; an `extract_metadata` exception is logged with its traceback and replaced by `fallback_metadata` (skip the event if that is `None`); nothing in this path escapes `run()`. Exceptions from `callback` still propagate (the sender already guards its own failures).

- [ ] **Step 1: Write failing tests** (add to `test_azclient.py`, `unittest`):
  - `test_three_part_title_with_empty_album`: title `"Artist - Track - Remix"`, album `""` returns track `"Artist - Track"`, album `"Remix"`, and does not raise.
  - `test_two_part_title_unchanged`: the existing behaviour is pinned (the existing test stays).
  - `test_autodj_name_is_configurable`: with `is_live` false, `extract_metadata(np, autodj_name="Robot X").dj == "Robot X"`; default still returns `"Spud the Ambient Robot"`.
  - `test_fallback_metadata_uses_raw_fields` and `test_fallback_metadata_none_without_title_or_artist`.
  - `test_run_survives_bad_events`: a fake client whose `events()` yields (a) non-JSON data, (b) `{}`, (c) a `connect` payload missing `subs`, (d) a `channel` payload whose song lacks keys, (e) one valid `channel` event; `run(...)` returns normally (does not raise) and the callback is called for (d) via fallback or skipped, and definitely for (e).
- [ ] **Step 2: Run** `python -m unittest test_azclient`. Expected: new tests FAIL (the three-part case raises `AttributeError`).
- [ ] **Step 3: Implement** to the interface above.
- [ ] **Step 4: Run** the full suite. Expected: all pass.
- [ ] **Step 5: Commit** `fix: title split with 3+ parts; guard each SSE event; configurable AutoDJ name`

---

### Task 3: No secrets in the image

**Files:** Modify `Dockerfile`, `.env.example`, `.gitignore`; Create `.dockerignore`; Test `test_deploy_config.py`

**Interfaces:**
- Produces: a `Dockerfile` whose `COPY` lines never name `.env` (copy `*.py` only; `healthcheck.py` and `restarter.sh` are added by later tasks, `restarter.sh` is mounted, not copied); a `.dockerignore` listing `.env`, `.venv`, `.git`, `__pycache__`, `*.log`, `state/`, `stations/*.env`. `.env.example` documents every variable from Task 1 and states which are main-only (`NOW_PLAYING_WEBHOOK`) versus status-board-only (`STATUS_BOARD`, `STATUS_BOARD_WEBHOOK`, `STATION_*`). `.gitignore` gains `stations/*.env`, `state/` and `*.sqlite3`, while `stations/*.env.example` stays tracked.

- [ ] **Step 1: Write failing tests** (`test_deploy_config.py`, plain file reads, no Docker):
  - `test_dockerfile_does_not_copy_env`: no `COPY`/`ADD` line contains `.env`.
  - `test_dockerignore_excludes_secrets_and_state`: `.dockerignore` exists and contains `.env`, `.venv`, `.git`, `state/`.
  - `test_env_example_documents_all_settings`: every env name from Task 1 appears in `.env.example`.
  - `test_gitignore_covers_station_env_and_state`: `.gitignore` contains `stations/*.env`, `state/` and `*.sqlite3`, and has no line that would ignore `*.example` files.
- [ ] **Step 2: Run** `python -m unittest test_deploy_config`. Expected: FAIL (today's `Dockerfile` has `COPY *.py .env .`).
- [ ] **Step 3: Implement** the four files.
- [ ] **Step 4: Run** the full suite. Expected: pass.
- [ ] **Step 5: Commit** `fix: stop baking .env into the image; document settings`

---

### Task 4: Connection health (read timeout, heartbeat, healthcheck)

**Files:** Modify `azclient.py` (`with_urllib3`, `build_sse_client`, `run`), `resilient_runner.py`; Create `heartbeat.py`, `healthcheck.py`; Test `test_heartbeat.py`, `test_resilient_runner.py`, `test_azclient.py`

**Interfaces:**
- Produces:
  - `with_urllib3(url, headers, timeout=30, read_timeout=300)` and `build_sse_client(server, shortcode, read_timeout=300)`: the read timeout reaches `urllib3.Timeout(read=...)`. `with_urllib3` raises `SSEConnectionError` (a new `Exception` subclass in `azclient.py`; its message carries the status code and no URL) for any HTTP status other than 200.
  - `run(client, callback, shortcode, autodj_name=..., on_event=None)`: `on_event()` is called for every received event, first, before any parsing, including events with no recognised payload; an exception inside `on_event` is logged and ignored.
  - `ResilientRunner(server, shortcode, callback, max_retries=None, autodj_name=DEFAULT_AUTODJ_NAME, read_timeout=300.0, on_event=None, on_connect=None)`: forwards `read_timeout` to `build_sse_client`, `autodj_name`/`on_event` to `run`, and calls `on_connect()` after each successful (HTTP 200) `build_sse_client`. Backoff rules: the first event delivered on a connection resets the retry count and the backoff (wrap the caller's `on_event` to do this, then call it); a `run()` that returns without any event having been delivered on that connection is a failed attempt: the retry count and a `_calculate_backoff` sleep (shutdown-aware) apply, so it never reconnects immediately. Everything else about the retry behaviour is untouched.
  - `heartbeat.py`: `heartbeat_path(state_dir: str) -> str` (`<state_dir>/heartbeat`); `class Heartbeat` with `__init__(self, path)` and `touch(self) -> None` (creates parent dirs, updates mtime); `check_heartbeat(path: str, max_age: float, now: float | None = None) -> bool` (False if missing or older than `max_age`); `main(environ=os.environ) -> int` reading `STATE_DIR` (default `.`) and `HEARTBEAT_MAX_AGE` (default 900: the read timeout 300 + the backoff cap 300 + margin), returning 0 or 1. `healthcheck.py` is `sys.exit(main())` guarded by `__main__`.

- [ ] **Step 1: Write failing tests:**
  - `test_heartbeat.py`: `touch` creates the file and creates missing directories; a second `touch` after `os.utime` into the past moves the mtime forward; `check_heartbeat` is False for a missing file, True when fresh, False when older than `max_age` (pass `now`); `main` returns 0/1 with `STATE_DIR` pointing at a tmp dir (missing, fresh, stale), and the default max age is 900.
  - `test_azclient.py`: `with_urllib3` raises `SSEConnectionError` for a 503 and a 404 response and returns for a 200 (patch `urllib3.PoolManager` to return a response object with `.status`); `with_urllib3` builds its `PoolManager` with a `Timeout` whose `read_total` equals the argument; `run` calls `on_event` once per event for a fake client yielding `{}`, non-JSON and a valid event; an `on_event` that raises does not stop `run`.
  - `test_resilient_runner.py` (patch `resilient_runner.build_sse_client` and `azclient.run`): `on_connect` is called once per connection attempt that succeeds; `read_timeout` and `autodj_name`/`on_event` are forwarded; a `build_sse_client` that raises (including `SSEConnectionError`) does not call `on_connect`. Using a fake clock (patch `resilient_runner.time` so `sleep` advances `time()` and nothing really waits): `test_event_resets_backoff` (after a connection delivers an event and then fails, the next delay is the initial backoff); `test_backoff_doubles_without_events` (consecutive failures with no events double the delay up to the cap); `test_event_less_close_backs_off_instead_of_spinning` (`run` returns at once with no event, the shutdown check stops the loop after a few passes, and the sleeps seen are positive and non-decreasing, never zero).
- [ ] **Step 2: Run** the three test files. Expected: FAIL.
- [ ] **Step 3: Implement** to the interfaces.
- [ ] **Step 4: Run** the full suite. Expected: pass.
- [ ] **Step 5: Commit** `feat: configurable SSE read timeout; heartbeat and healthcheck`

---

### Task 5: Status board store and sender (`status_board.py`)

**Files:** Create `status_board.py`; Test `test_status_board.py`

**Interfaces:**
- Consumes: `NowPlayingResponse` (azclient), `DiscordWebhook`/`DiscordEmbed` (`discord_webhook==1.3.1`: `execute()` sets `.id` from the response, `edit()` PATCHes message `.id`, `DiscordWebhook(url=..., id=..., timeout=...)`).
- Produces:
  - `class StatusBoardStore`: `__init__(self, path: str)` (creates the SQLite file, parent dirs and table `status(station TEXT PRIMARY KEY, message_id TEXT, last_value TEXT, updated_at REAL)`); `get(station: str) -> StoredStatus | None` (`StoredStatus` is a `NamedTuple(message_id: str, last_value: str, updated_at: float)`); `save(station: str, message_id: str, last_value: str) -> None`.
  - `format_status(response: NowPlayingResponse, station_name: str) -> dict` with keys `title` (starts with `station_name`), `description`, `timestamp`, `thumbnail_url`.
  - `class StatusBoardSender`: `__init__(self, webhook_url, station_name, store, timeout=10.0, webhook_factory=DiscordWebhook)`; `send(self, response: NowPlayingResponse) -> None`:
    1. If the stored `last_value` equals this response's value, do nothing. The value is `f"{dj}|{artist}|{track}|{album}"`, the same four fields `NowPlayingResponse.__eq__` compares, so a change in elapsed time alone never triggers an edit.
    2. If there is no stored message ID, post a new message and save the returned ID with the value (a failed post saves nothing, so the next event retries).
    3. Otherwise edit the stored message; success updates `last_value`. A 404 on edit means the message is gone, so post a new one and save the new ID. Any other status, and any exception, is logged (no URL in the log), changes nothing in the store, and never propagates.

- [ ] **Step 1: Write failing tests** (a `FakeWebhook` class recording `url`, `id`, `timeout`, embeds and calls, with scripted `execute()`/`edit()` results that set `.id` like the library; the store lives in a `tempfile.TemporaryDirectory`):
  - `test_first_send_posts_and_saves_id`.
  - `test_second_send_edits_same_message`: a new track edits using the stored `id`, no second post.
  - `test_same_track_is_not_resent`, and `test_elapsed_only_change_is_not_resent`.
  - `test_recreate_keeps_editing_same_message`: build a new `StatusBoardStore` and `StatusBoardSender` on the same file (simulating a container recreate); the next track edits the original message ID and does not post.
  - `test_deleted_message_reposts`: edit returns 404 → one new post, the new ID is saved and the next edit uses it.
  - `test_failures_keep_id_and_do_not_raise`: edit returns 500, then raises `requests.exceptions.Timeout`; both leave the stored ID unchanged and `send` does not raise; a later successful send edits normally.
  - `test_failed_first_post_saves_nothing_and_retries`.
  - `test_timeout_is_passed_to_every_webhook`.
  - `test_title_starts_with_station_name`.
  - `test_logs_never_contain_the_webhook_url` (capture logs during a failing send; the URL string is absent).
- [ ] **Step 2: Run** `python -m unittest test_status_board`. Expected: FAIL.
- [ ] **Step 3: Implement** to the interfaces. SQLite connection per operation is fine.
- [ ] **Step 4: Run** the full suite. Expected: pass.
- [ ] **Step 5: Commit** `feat: status-board sender that edits one message per station`

---

### Task 6: Wire it together (`azmonitor.py`)

**Files:** Modify `azmonitor.py`; Test `test_azmonitor.py`

**Interfaces:**
- Consumes: Tasks 1, 4 and 5.
- Produces (all module-level constants `NOW_PLAYING_WEBHOOK`, `STATION_SERVER`, `STATION_SHORTCODE` and the function `validate_environment` are removed):
  - `send_webhook(embed_data: dict, webhook_url: str, timeout: float = 10.0) -> None` (builds `DiscordWebhook(url=webhook_url, timeout=timeout)`; still catches and logs its own exceptions).
  - `DiscordSender(webhook_url: str, timeout: float = 10.0)` with `send(response)` (existing duplicate suppression and embed content).
  - `class DryRunSender` with `send(response)` that logs what would be posted and never touches Discord.
  - `build_sender(settings: Settings)`: `DryRunSender` when `settings.dry_run`; else `StatusBoardSender(settings.webhook_url, settings.station_name, StatusBoardStore(<state_dir>/status_board.sqlite3), settings.webhook_timeout)` when `settings.status_board`; else `DiscordSender(settings.webhook_url, settings.webhook_timeout)`.
  - `main(environ=os.environ) -> int`: loads `.env` (`load_dotenv()`), then `load_settings`; on `SettingsError` logs the message and returns 1 without connecting; builds one `Heartbeat(heartbeat_path(state_dir))`, a `ResilientRunner(server, shortcode, sender.send, max_retries=None, autodj_name=..., read_timeout=settings.sse_read_timeout, on_event=hb.touch, on_connect=hb.touch)`, keeps the existing signal handling, and returns `runner.run(...)`. `if __name__ == "__main__": sys.exit(main())`.

- [ ] **Step 1: Write failing tests** (`test_azmonitor.py`; patch `azmonitor.DiscordWebhook`, `azmonitor.ResilientRunner`):
  - `test_build_sender_selects_class` for dry-run, status-board and plain settings.
  - `test_dry_run_never_constructs_a_webhook`: sending through `DryRunSender` leaves the patched `DiscordWebhook` uncalled.
  - `test_discord_sender_passes_timeout`: the `DiscordWebhook` mock is called with `timeout=` the configured value and the right `url`.
  - `test_main_exits_1_without_connecting_on_bad_settings`: `main({"STATUS_BOARD": "true", "NOW_PLAYING_WEBHOOK": valid})` returns 1 and `ResilientRunner` is never constructed.
  - `test_main_wires_heartbeat_and_settings`: with valid settings the `ResilientRunner` mock receives `on_event` and `on_connect` callables that touch the heartbeat file under `STATE_DIR`, plus the configured `read_timeout`, `autodj_name`, server and shortcode, and `main` returns the runner's exit code.
  - Plain-mode send still dedups: two identical responses produce one webhook post.
- [ ] **Step 2: Run** `python -m unittest test_azmonitor`. Expected: FAIL.
- [ ] **Step 3: Implement** to the interfaces. Also run the CI import check: `python -c "import azmonitor, azclient, resilient_runner, logger_config, settings, heartbeat, status_board"`.
- [ ] **Step 4: Run** the full suite. Expected: pass.
- [ ] **Step 5: Commit** `feat: select main or status-board sender from settings; timeouts, dry run, heartbeat wiring`

---

### Task 7: Compose, generalised restarter, docs, CI

**Files:** Modify `docker-compose.yml`, `README.md`, `.github/workflows/test.yml`, `.gitignore`; Create `restarter.sh`, `stations/pulse.env.example`, `stations/inner.env.example`, `stations/outer.env.example`; Test `test_restarter.py`

**Interfaces:**
- Consumes: env names from Task 1; `healthcheck.py` (Task 4).
- Produces:
  - `restarter.sh` (POSIX `sh`, runs in `docker:cli`), loops forever with a 60s sleep. Each pass: (a) restarts every container matching `docker ps -q --filter label=az-monitor.managed=true --filter health=unhealthy`; (b) once per calendar day, when the current time is at or after `RESTART_AT` (default `05:00`) and the day has not been done, restarts every container matching `--filter label=az-monitor.managed=true` and records the day in `RESTARTER_STATE` (default `/tmp/restarter.last`). On its first pass after startup, if the time is already at or after `RESTART_AT` and no marker exists, it records today as done **without** restarting anything (so recreating the restarter during the day does not restart every monitor). Test hooks: `DOCKER` (command to call, default `docker`), `RUN_ONCE=1` (one pass, no sleep), `NOW_HM` and `NOW_DAY` (override the clock).
  - `docker-compose.yml`: service `az-now-playing` (main, post mode; `container_name: az-now-playing` kept for continuity; `build: .` and `image: az-now-playing:latest`; `env_file: [.env]`; `environment: STATE_DIR=/state`; volume `./state/radiospiral:/state`; label `az-monitor.managed: "true"`; healthcheck `["CMD", "python", "healthcheck.py"]` with interval 60s, timeout 10s, retries 3, start_period 60s; `restart: unless-stopped`). Services `status-pulse`, `status-inner`, `status-outer` from a shared `x-` block under `profiles: [secondary]`, each with `env_file: [stations/<name>.env]` **only** (not the main `.env`), `STATE_DIR=/state`, volume `./state/<name>:/state`, the same label and healthcheck, and no `container_name`. A `restarter` service (`docker:cli`, docker socket mounted, `restarter.sh` mounted read-only, `entrypoint: ["/bin/sh", "/restarter.sh"]`, `restart: unless-stopped`). A comment explains why `env_file` is per service.
  - `stations/<name>.env.example`: `STATUS_BOARD=true`, `STATION_SERVER`, `STATION_SHORTCODE`, `STATION_NAME`, `AUTODJ_NAME`, `STATUS_BOARD_WEBHOOK` placeholder, `STATE_DIR=/state`. Comment that the shortcodes, the AutoDJ persona names and the test-server host must be confirmed on the test server.
  - `.github/workflows/test.yml`: the import check lists the new modules; the test step is `python -m unittest discover -v`.
  - `.gitignore` entries and the `HEARTBEAT_MAX_AGE` formula are covered by Task 3 and Task 4; the README documents both.
  - README: settings table, a "Multiple stations" section (status board, per-station env files, state directories, `--profile secondary`, a separate test stack with its own webhooks), and the migration for the existing prod container: its webhook currently comes only from the `.env` that was baked into the old image, so `.env` must stay on the host next to `docker-compose.yml` and be loaded through `env_file` **before** the first rebuild; also stop the old `restarter` container (it targets `az-now-playing` only), then `docker compose up -d --build`.

- [ ] **Step 1: Write failing tests** (`test_restarter.py`, using `subprocess` to run `sh restarter.sh` with a fake `DOCKER` script in a temp dir that logs its arguments and prints scripted container IDs for `ps`):
  - `test_unhealthy_sweep_restarts_labelled_unhealthy_containers`: `NOW_HM=12:00`; the fake `ps` (called with `health=unhealthy`) prints two IDs; the log shows `restart` called with both and no daily restart.
  - `test_daily_restart_once_per_day`: `NOW_HM=05:00`, `NOW_DAY=2026-11-12`, fake `ps` (labelled only) prints three IDs; the first pass restarts all three and writes the state file; a second pass the same day restarts nothing for the daily rule; a pass with `NOW_DAY=2026-11-13` restarts again.
  - `test_before_restart_time_does_nothing_daily`: `NOW_HM=04:59`.
  - `test_startup_after_restart_time_does_not_restart`: no state file, `NOW_HM=14:00`, `NOW_DAY=2026-11-12`: the first pass restarts nothing for the daily rule but records the day; a pass with `NOW_DAY=2026-11-13` and `NOW_HM=05:00` restarts.
  - `test_ps_uses_the_managed_label`: the recorded `ps` arguments contain `label=az-monitor.managed=true`.
- [ ] **Step 2: Run** `python -m unittest test_restarter`. Expected: FAIL (no script).
- [ ] **Step 3: Implement** all files above. Make the clock logic compare `HH:MM` strings (zero-padded) so "at or after" works.
- [ ] **Step 4: Verify (Docker is not installed here, so say so in the report):**
  - `sh -n restarter.sh`; `shellcheck restarter.sh` if available.
  - Parse `docker-compose.yml` with PyYAML (`uv run --no-project --with pyyaml python -c ...`) and assert: `az-now-playing` has `env_file` `[".env"]`; each `status-*` service has `env_file` containing only its own `stations/<name>.env` and has `profiles: [secondary]`; every app service has the label and a healthcheck; none of the `status-*` services has `container_name`; the restarter mounts `restarter.sh`.
  - Run the full suite once more, then `git status` to confirm no stray files.
  - Deferred to the test server: `docker compose config`, profiles, the restarter against a real daemon, and the webhook edit against Discord.
- [ ] **Step 5: Commit** `feat: per-station compose services, generalised restarter, docs and CI`

---

## Self-review (spec coverage)

- Station settings, AutoDJ name, status-board mode and its own webhook with no fallback: Tasks 1 and 6.
- No `.env` in the image, `env_file` per service, migration giving the main container its env: Tasks 3 and 7.
- Timeouts on every webhook call, configurable SSE read timeout, `ResilientRunner` kept as the only retry layer: Tasks 4 and 6.
- Heartbeat only on events and connects, healthcheck with window above the read timeout and the backoff cap: Task 4 (default max age 900 = read timeout 300 + backoff cap 300 + margin; the first event resets the backoff; a non-200 or event-less close backs off instead of spinning).
- Known residual: a server that answers 200 and then closes immediately every time would touch the heartbeat once per backoff interval and so still look healthy. Accepted: the backoff stops it hammering the server, and the status board's staleness would show.
- Parse fix with regression test and per-event guard: Task 2.
- Status state in a per-station SQLite file in a mounted volume, edit in place, repost on 404: Tasks 5 and 7.
- One generalised restarter (daily restart plus unhealthy sweep, selected by label): Task 7.
- Dry-run switch (replacing the stashed `BLOCK_POST` idea): Task 6.
- Known and unverified: that SSE keepalives arrive as events (the reconnect-touches-heartbeat path covers the case where they do not); Discord's behaviour for webhook edits and 404 on a deleted message; Compose profiles, label filters and the restarter against a real daemon. All to be confirmed on the test server. The earlier `discord-az-monitor` stash (`claude-rebaseline-2026-11-11`) is superseded by this plan.
