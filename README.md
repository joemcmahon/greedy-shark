# greedy-shark
"My ear is open like a greedy shark, To catch the tunings of a voice divine." Detects sliences on an audio stream.

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

## Using it
If you want to use the Greedy Shark, you'll need to set up a `.env` file for it. Fill out the following values:

    STREAM_URL=<your audio stream; usually https://someplace.com/your_stream.mp3>
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/<set this up on your Discord>
    CHECK_INTERVAL=60
    SAMPLE_DURATION=10
    FFMPEG_TIMEOUT=15
    ALERT_COOLDOWN=600

    MIN_RMS_THRESHOLD=500
    MIN_VARIANCE_THRESHOLD=1000

    STAFF_ROLE_ID=<captured from your Discord>

    # Azuracast API Configuration
    AZURACAST_BASE_URL=https://your-azuracast-instance.com
    AZURACAST_API_KEY=<your API key from Azuracast>
    AZURACAST_STATION_ID=<your station shortcode>

    # Discord Bot Configuration
    DISCORD_BOT_TOKEN=<your bot token from Discord Developer Portal>
    DISCORD_CHANNEL_ID=<your Discord channel ID>
    GRACE_PERIOD_MINUTES=15

`STREAM_URL` - The audio stream that the Shark will sample.
`DISCORD_WEBHOOK_URL` - Defines the Discord and Discord channel that messages will go to.

`CHECK_INTERVAL` - How often the Shark will wake up and listen.
`SAMPLE_DURATION` - The number of seconds it will "listen".
`FFMPEG_TIMEOUT` - A safety valve; if `ffmpeg` gets locked up, this interrupts it
`ALERT_COOLDOWN` - How long the Shark waits before posting another alert

`MIN_RMS_THRESHOLD` and `MIN_VARIANCE_THRESHOLD` can be used to detect streams that are active but not changing. We're an ambient station, and don't use this.

`STAFF_ROLE_ID` - The `@role` that the Shark sends its message to.

`AZURACAST_BASE_URL` - The base URL of your Azuracast instance (e.g., https://azuracast.yourstation.com).
`AZURACAST_API_KEY` - An API key created in Azuracast with permissions for "View Station Reports" and "Manage Station Broadcasting".
`AZURACAST_STATION_ID` - Your station's shortcode or ID from Azuracast.

`DISCORD_BOT_TOKEN` - A Discord bot token from the Discord Developer Portal (required for all bot commands).
`DISCORD_CHANNEL_ID` - The Discord channel ID where the bot should listen for commands.
`GRACE_PERIOD_MINUTES` - Duration in minutes for the grace period (default: 15).

## How it Works

Greedy Shark uses a state machine to monitor your stream with two different rulesets:

### 2-minute rule
When no live streamer/DJ is connected to Azuracast, the Shark applies the standard broadcast rule: **2 consecutive minutes of silence triggers an alert**. This catches situations where your AutoDJ has failed or the stream has gone down, the streamer has fallen asleep or walked away, forgetting they're still connected (it happens!).

In these cases, a Discord `@Staff` member can knock the streamer offline with a `!shark` command. This tells Azuracast to suspend the streamer until they're reinstated via a `!unshark` command.

### Streamer Connected (10-minute rule)
When a live streamer is connected, the Shark switches to a more lenient rule:

- **4 minutes of silence**: Sends a warning "Action may be needed soon" with a reminder to use `!shark <id>` if needed
- **10 minutes of silence**: Sends a staff alert that action is required, pointing to `!streamers` then `!shark <id>`

The Shark does not auto-suspend when a streamer is connected — staff use `!shark` to manually suspend when they judge it necessary.

### Audio Detection Resets Timer
Any time audio is detected, all timers reset. This means streamers can make "please stand by" announcements or play brief audio clips to keep their connection active while working through issues.

### Re-enabling Suspended Streamers
When a streamer is suspended via `!shark`, the Shark tracks this. Staff can manage these suspensions with Discord commands:

- **`!streamers`** - List all streamers registered in Azuracast and their ID number
- **`!sharked`** - List all streamers currently suspended via `!shark`
- **`!unshark <id>`** - Re-enable any suspended streamer by their Azuracast ID

### Getting Help
Use `!shark-help` (or `!sharkhelp`) in Discord to see all available commands with usage examples.

### State Transitions
- When a streamer connects, the monitor switches from 2-minute to 10-minute mode
- When a streamer disconnects (naturally or via suspension), it switches back to 2-minute mode
- When `!working-on-it` is used, the monitor enters grace period mode
- When grace period expires, normal monitoring resumes
- All silence counters reset on state transitions

The silence detection algorithm looks for zero-amplitude signals. The signal level is hardcoded, as it has proven reliable for detecting true silence versus very quiet audio.

## Setting Up the Discord Bot

To enable bot commands (`!shark`, `!unshark`, `!streamers`, grace period commands, etc.):

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application or use an existing one
3. Navigate to the "Bot" section and create a bot
4. Copy the bot token and add it to your `.env` as `DISCORD_BOT_TOKEN`
5. In the "Bot" tab, Under "Privileged Gateway Intents", enable "Message Content Intent"
6. In the Oauth2 tab, first check "bot" in the Oauth2 URL Generator section, then in the section that appears, check "Send Messages", "View Channels", and "Read Message History"
6. Copy the generated URL and open it in the browser to invite the bot to your server.
7. Get your channel ID (enable Developer Mode in Discord, right-click channel, Copy ID)
8. Run the bot: `python grace_period_bot.py`

The bot can run alongside the monitor script or separately.

## Running with Docker Compose

The easiest way to run both the monitor and the bot together is with Docker Compose:

```bash
# Build and start both services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

This will:
- Build the Docker image with all dependencies
- Start the monitor and bot as separate containers
- Share state files between them via volumes
- Auto-restart on failures
- Run in the background

Both services share `.grace_period_until`, `.auto_suspended_streamers`, and `.monitor_state` via shared volumes, allowing the bot and monitor to communicate.

## Running Tests

The project includes comprehensive unit tests that don't require a running Azuracast instance:

```bash
# Install test dependencies
pip install -r requirements.txt

# Run all tests
pytest test_monitor.py -v

# Run specific test class
pytest test_monitor.py::TestMonitorContext -v

# Run with coverage report
pytest test_monitor.py --cov=monitor_stream --cov-report=term-missing
```

The test suite covers:
- MonitorContext class initialization and methods
- State transition logic (determine_next_state)
- State transition execution (handle_state_transition)
- Silence handling for all three states
- Grace period file handling
- Full lifecycle scenarios

All external dependencies (Azuracast API, Discord webhooks, file I/O) are mocked, so tests run quickly and reliably without network access.
