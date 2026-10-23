# greedy-shark
"My ear is open like a greedy shark, To catch the tunings of a voice divine." Detects silences on an audio stream.

I created this script because, from time to time, our DJs will sometimes sign off, but forget to disconnect from
the stream, resulting in dead air.

The Greedy Shark connects to the audio stream, and, using ffmpeg, "listens" for long silences.

It currently conforms to the standard used at most radio stations: a full two-minute silence is an all-hands-on-deck,
why are we not broadcasting event.

Greedy Shark, as it's currently implemented, is relatively restrained in what it does: It just pushes a notification
to a Discord channel to alert us that hey, something is up; there are commands (!shark/!unshark) to allow a human
to suspend a streamer or enable them.

This also lets the Shark double as a gatekeeper for guest streamers: add them to Azuracast and suspend them; the
Shark can list the users and then a Discord admin can !unshark them to allow them to broadcast, and !shark them
again at the end of their slot.

## Components

The Shark is two long-running processes that share a working directory:

| Process | Script | Talks to | Purpose |
|---|---|---|---|
| Monitor | `monitor_stream.py` | Audio stream (ffmpeg), Azuracast API, Discord **webhook** | Samples the stream once per check, tracks silence, posts alerts |
| Bot | `grace_period_bot.py` | Discord **bot gateway**, Azuracast API | Handles `!shark`, `!unshark`, `!streamers`, grace-period commands, etc. |

They coordinate through three state files in the working directory: `.grace_period_until`, `.auto_suspended_streamers`, and `.monitor_state`. Both processes must run from the **same directory** (or, under Docker, mount the same files).

## Installation

### 1. Requirements

On the machine that will run the Shark (the "monitor machine"):

- Network access to your stream URL, your Azuracast instance, and Discord.
- **Either** Docker with the Compose plugin (recommended), **or** Python 3.10+ and `ffmpeg` on the `PATH`.

### 2. Set up the Discord server

The Shark expects the following on your Discord server. You need the "Manage Server" (or "Manage Webhooks" and "Manage Roles") permission to set them up.

1. **Turn on Developer Mode** (User Settings → Advanced → Developer Mode) so that you can copy IDs with right-click → *Copy ID*.

2. **A staff role.** This is the role that alert messages `@`-mention, e.g. `@stream-staff`. Give it to everyone who should be pinged about dead air.
   - Copy its ID (Server Settings → Roles → right-click the role → *Copy Role ID*). This is `STAFF_ROLE_ID`.
   - If the role gets mentioned but nobody is notified, turn on *Allow anyone to @mention this role* for it.

3. **A private staff channel**, e.g. `#shark-tank`. Alerts are posted here and commands are typed here.
   - **Lock this channel down to staff.** The bot does not check roles: **anyone who can post in this channel can run `!shark` and `!unshark`**. The channel's permissions are the only access control. Deny *View Channel* to `@everyone`, then allow it for the staff role and for the bot.
   - The bot ignores commands typed in any other channel.
   - Copy the channel ID (right-click the channel → *Copy Channel ID*). This is `DISCORD_CHANNEL_ID`.

4. **A webhook in that channel** for the monitor's alerts: Channel settings → Integrations → Webhooks → *New Webhook*. Name it (e.g. "Greedy Shark") and *Copy Webhook URL*. This is `DISCORD_WEBHOOK_URL`.
   - You can point the webhook at a different channel than the commands, but staff will usually want alerts and commands in the same place.

5. **A bot application** for the commands:
   1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application.
   2. In the **Bot** tab, click *Reset Token* and copy the token. This is `DISCORD_BOT_TOKEN`. Treat it like a password.
   3. Still in the **Bot** tab, under *Privileged Gateway Intents*, enable **Message Content Intent**. Without it the bot connects but never sees commands.
   4. In the **OAuth2** tab's *URL Generator*, check the `bot` scope, then these bot permissions: **View Channels**, **Send Messages**, **Read Message History**.
   5. Open the generated URL in a browser and add the bot to your server.
   6. Make sure the bot can see and post in the staff channel from step 3. If that channel is private, add the bot (or its role) to the channel's permissions explicitly.

### 3. Set up Azuracast

1. **Find your station's shortcode.** It's the short name in your station's URLs, e.g. `my_station` in `https://azuracast.example.com/public/my_station`. The numeric station ID also works. This is `AZURACAST_STATION_ID`.
2. **Create an API key**: user menu (top right) → *My API Keys* → *Add API Key*. This is `AZURACAST_API_KEY`.
   - The key acts with the permissions of the Azuracast user who created it. That user needs to be able to view the station and to manage its streamers/DJs. The Shark reads now-playing data, lists streamers, enables or disables streamer accounts, and disconnects a live streamer.
   - A dedicated Azuracast user for the Shark, with a role limited to that station, is a good idea.
3. **Streamers/DJs** must be set up in Azuracast (Station → Streamers/DJs). `!shark` and `!unshark` work by toggling a streamer account's *enabled* flag, and use the numeric ID that `!streamers` lists.

### 4. Create `.env`

Create a file named `.env` in the project directory (next to `monitor_stream.py`). Both processes and Docker Compose read it. It holds secrets: it's already in `.gitignore`, so don't commit it, and `chmod 600 .env` is a good idea.

```dotenv
# --- Stream monitor ---
STREAM_URL=https://radio.example.com/listen/my_station/radio.mp3
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123456789012345678/abcDEF...
STAFF_ROLE_ID=123456789012345678
CHECK_INTERVAL=60
SAMPLE_DURATION=10

# --- Azuracast API (used by both the monitor and the bot) ---
AZURACAST_BASE_URL=https://azuracast.example.com
AZURACAST_API_KEY=0123456789abcdef:0123456789abcdef0123456789abcdef
AZURACAST_STATION_ID=my_station

# --- Discord bot ---
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_CHANNEL_ID=123456789012345678
GRACE_PERIOD_MINUTES=15
```

| Variable | Required? | Default | Used by | Meaning |
|---|---|---|---|---|
| `STREAM_URL` | yes | — | monitor | The public audio stream to sample (anything ffmpeg can open, usually an Icecast/Shoutcast `.mp3` or `.aac` URL). |
| `DISCORD_WEBHOOK_URL` | yes | — | monitor | Webhook that alerts and status messages are posted to. |
| `STAFF_ROLE_ID` | yes | — | monitor | Numeric ID of the role to `@`-mention on alerts. |
| `CHECK_INTERVAL` | no | `60` | monitor | Seconds to sleep between checks. The alert thresholds are counted in checks and assume a 60-second interval (2 checks = 2 minutes, and so on), so changing this stretches or shrinks every threshold proportionally. Leave it at 60 unless you mean that. |
| `SAMPLE_DURATION` | no | `10` | monitor | Seconds of audio to capture on each check. |
| `AZURACAST_BASE_URL` | yes* | — | both | Base URL of Azuracast, no trailing slash. |
| `AZURACAST_API_KEY` | yes* | — | both | API key from step 3. |
| `AZURACAST_STATION_ID` | yes* | — | both | Station shortcode or numeric ID. |
| `DISCORD_BOT_TOKEN` | yes (bot) | — | bot | Bot token from the Developer Portal. The bot refuses to start without it. |
| `DISCORD_CHANNEL_ID` | yes (bot) | — | bot | Channel the bot accepts commands in. The bot refuses to start without it. |
| `GRACE_PERIOD_MINUTES` | no | `15` | bot | How long `!working-on-it` pauses monitoring. |

\* If the Azuracast settings are missing, the monitor still runs but can't tell whether a streamer is connected, so it always applies the no-streamer rule. The streamer commands in the bot won't work.

Each check takes roughly `CHECK_INTERVAL + SAMPLE_DURATION` seconds plus the API round-trip. With the recommended values, "2 minutes" of silence is really about 2½ minutes of wall-clock time.

Older versions also read `FFMPEG_TIMEOUT`, `MIN_RMS_THRESHOLD`, `MIN_VARIANCE_THRESHOLD`, and `ALERT_COOLDOWN`. These are no longer used and can be deleted from existing `.env` files.

### 5. Run it with Docker Compose (recommended)

From the project directory:

```bash
# Create the shared state files FIRST. If they don't exist, Docker creates
# directories with these names instead, and the Shark can't use them.
touch .grace_period_until .auto_suspended_streamers .monitor_state

# Build and start both services
docker compose up -d --build

# Watch the logs
docker compose logs -f

# Stop
docker compose down
```

This builds one image (Python 3.10 + ffmpeg) and starts two containers, `greedy-shark-monitor` and `greedy-shark-bot`. They use host networking, restart automatically unless stopped, and bind-mount the three state files from the project directory so that the processes can coordinate.

Notes:
- The containers get `.env` at startup through `env_file`; it is not baked into the image. After editing `.env`, run `docker compose up -d` again to recreate them.
- `check_shark_health.sh` reports the status, health, and restart count of both containers. It exits 0 when everything is healthy, so you can run it from cron.

### 5 (alternative). Run it directly with Python

```bash
sudo apt-get install ffmpeg          # or your platform's equivalent
python3 -m venv monitor-venv
. monitor-venv/bin/activate
pip install -r requirements.txt

# Run both, from the project directory, in separate terminals or under systemd/tmux
python monitor_stream.py
python grace_period_bot.py
```

Both processes look for `.env` and the state files in the **current working directory**, so start them from the project directory. Under systemd, set `WorkingDirectory=` to the project directory.

### 6. Verify the installation

When everything is wired up correctly:

1. The webhook posts **"🦈 Greedy Shark is active"** when the monitor starts. If it doesn't, check `STREAM_URL` and `DISCORD_WEBHOOK_URL`, and look at the monitor log.
2. The bot posts **"Greedy Shark is now online and monitoring!"** in the staff channel. If the log says `Could not find channel`, the channel ID is wrong or the bot can't see that channel.
3. `!shark-help` in the staff channel gets a reply. If the bot is online but silent, Message Content Intent is probably off.
4. `!streamers` lists your Azuracast streamers. If it doesn't, check the Azuracast settings and the API key's permissions.
5. While audio is playing, the monitor log shows a line like `Analyzed audio - RMS: …` once a minute.
6. Optional: `python staff_ping_test.py` sends a test alert that `@`-mentions the staff role, so that you can confirm the ping actually reaches people.

## How it Works

Greedy Shark uses a state machine to monitor your stream with two different rulesets:

### 2-minute rule
When no live streamer/DJ is connected to Azuracast, the Shark applies the standard broadcast rule: **2 consecutive minutes of silence triggers an alert**, and it **repeats every 2 minutes** until audio comes back — with no streamer on, dead air means something is actually broken.

In these cases, a site admin will need to log in to Azuracast and try to determine what the issue is (or if the server machine itself has gone down).

### Streamer Connected (escalating alerts)
This catches situations where the streamer has fallen asleep or walked away, forgetting they're still connected (it happens!). When a live streamer is connected, the Shark escalates instead of alerting once:

- **2 minutes of silence**: Sends a "Silence detected" heads-up naming the streamer
- **4 minutes of silence**: Sends a warning "Action may be needed soon" with a reminder to use `!shark <id>` if needed
- **6 minutes of silence**: Sends a staff alert that action is required, pointing to `!streamers` then `!shark <id>`, and repeats it every 2 minutes until audio returns or the streamer disconnects

The Shark does not auto-suspend when a streamer is connected — staff use `!shark` to manually suspend when they judge it necessary. `!shark` both disables the streamer's account and disconnects them if they're live.

### Audio Detection Resets Timer
Any time audio is detected, all timers reset. This means streamers can make "please stand by" announcements or play brief audio clips to keep their connection active while working through issues.

### Re-enabling Suspended Streamers
When a streamer is suspended via `!shark`, the Shark tracks this. Staff can manage these suspensions with Discord commands:

- **`!streamers`** - List all streamers registered in Azuracast and their ID number
- **`!sharked`** - List all streamers currently suspended via `!shark`
- **`!unshark <id>`** - Re-enable any suspended streamer by their Azuracast ID
- **`!retrack <id>`** - Add a streamer who was disabled directly in Azuracast to the `!sharked` list

It's probably safest to not `!unshark` a streamer until they've actually checked in, or they have done it themselves. Most streaming software will keep trying to reconnect, so if you `!unshark` a streamer _before_ they've shut off their streaming software, you'll just end up with dead air again.

### Getting Help
Use `!shark-help` (or `!sharkhelp`) in Discord to see all available commands with usage examples. (Plain `!help` shows discord.py's generic command list instead.)

### State Transitions
- When `!working-on-it` is used, the monitor enters grace period mode
- When grace period expires, normal monitoring resumes
- All silence counters reset on state transitions

The silence detection algorithm looks for zero-amplitude signals. The signal level is hardcoded, as it has proven reliable for detecting true silence versus very quiet audio. A failure to fetch audio from the stream at all also counts as a silent check.

## Running Tests

The project includes unit tests that don't require a running Azuracast instance or Discord:

```bash
# Install test dependencies
pip install -r requirements.txt

# Run all tests
pytest -v

# Run one file or test class
pytest test_monitor.py::TestMonitorContext -v

# Run with coverage report (requires pytest-cov)
pytest --cov=monitor_stream --cov=grace_period_bot --cov-report=term-missing
```

The test suite covers:
- `test_monitor.py`: the monitor state machine, silence handling for each state, alert cadence, grace period and state-file handling, and full lifecycle scenarios
- `test_bot.py`: every bot command handler
- `test_notifier.py`: the Discord and file notifiers

All external dependencies (Azuracast API, Discord webhooks, file I/O) are mocked, so tests run quickly and reliably without network access.
