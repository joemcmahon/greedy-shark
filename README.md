# greedy-shark
"My ear is open like a greedy shark, To catch the tunings of a voice divine." Detects sliences on an audio stream.

I created this script because, from time to time, our DJs will sometimes sign off, but forget to disconnect from
the stream, resulting in dead air. 

The Greedy Shark connects to the audio stream, and, using ffmpeg, "listens" for long silences.

It currently conforms to the standard used at most radio stations: a full two-minute silence is an all-hands-on-deck,
why are we not broadcasting event.

Greedy Shark, as it's currently implemented, is relatively restrained in what it does: It just pushes a notification
to a Discord channel to alert us that hey, something is up.

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

## How it Works

Greedy Shark uses a state machine to monitor your stream with two different rulesets:

### No Streamer Connected (2-minute rule)
When no live streamer/DJ is connected to Azuracast, the Shark applies the standard broadcast rule: **2 consecutive minutes of silence triggers an alert**. This catches situations where your AutoDJ has failed or the stream has gone down.

### Streamer Connected (10-minute rule)
When a live streamer is connected, the Shark switches to a more lenient rule to accommodate natural pauses, technical adjustments, or brief breaks:

- **8 minutes of silence**: Sends a warning message "Forced disconnect imminent" to give the streamer a 2-minute heads-up
- **10 minutes of silence**: Automatically suspends the streamer's account in Azuracast (preventing auto-reconnect) and sends a "Streamer forced off" notification

### Audio Detection Resets Timer
Any time audio is detected, all timers reset. This means streamers can make "please stand by" announcements or play brief audio clips to keep their connection active while working through issues.

### State Transitions
- When a streamer connects, the monitor switches from 2-minute to 10-minute mode
- When a streamer disconnects (naturally or via suspension), it switches back to 2-minute mode
- All silence counters reset on state transitions

The silence detection algorithm looks for zero-amplitude signals. The signal level is hardcoded, as it has proven reliable for detecting true silence versus very quiet audio.
