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

`STREAM_URL` - The audio stream that the Shark will sample.
`DISCORD_WEBHOOK_URL` - Defines the Discord and Discord channel that messages will go to.

`CHECK_INTERVAL` - How often the Shark will wake up and listen.
`SAMPLE_DURATION` - The number of seconds it will "listen".
`FFMPEG_TIMEOUT` - A safety valve; if `ffmpeg` gets locked up, this interrupts it
`ALERT_COOLDOWN` - How long the Shark waits before posting another alert

`MIN_RMS_THRESHOLD` and `MIN_VARIANCE_THRESHOLD` can be used to detect streams that are active but not changing. We're an ambient station, and don't use this.

`STAFF_ROLE_ID` - The `@role` that the Shark sends its message to.

Currently, the silence detection algorithm looks for a zero-amplitude signal for more than two minutes. The signal level and duration are hardcoded, as
I didn't feel that I wanted or needed to change those. It would be simple to add these to the `.env` as well if desired -- patches welcome.
