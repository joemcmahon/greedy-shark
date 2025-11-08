import os
import subprocess
import tempfile
import time
import logging
import requests
import numpy as np
from io import BytesIO
from pydub import AudioSegment
from dotenv import load_dotenv
from enum import Enum

# Config from environment
load_dotenv()
STREAM_URL = os.getenv("STREAM_URL")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL", 300))
SAMPLE_DURATION = int(os.getenv("SAMPLE_DURATION", 10))
FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT", 15))
ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN", 600))
STAFF_ROLE_ID = os.getenv("STAFF_ROLE_ID")

MIN_RMS_THRESHOLD = float(os.getenv("MIN_RMS_THRESHOLD", 500))
MIN_VARIANCE_THRESHOLD = float(os.getenv("MIN_VARIANCE_THRESHOLD", 1000))

# Azuracast API Configuration
AZURACAST_BASE_URL = os.getenv("AZURACAST_BASE_URL")
AZURACAST_API_KEY = os.getenv("AZURACAST_API_KEY")
AZURACAST_STATION_ID = os.getenv("AZURACAST_STATION_ID")

# Grace period configuration
GRACE_PERIOD_FILE = ".grace_period_until"

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# State machine for monitoring modes
class MonitorState(Enum):
    NO_STREAMER = "no_streamer"           # No streamer connected, 2-minute rule
    STREAMER_ACTIVE = "streamer_active"   # Streamer connected, 10-minute rule
    GRACE_PERIOD = "grace_period"         # Future: streamer acknowledged issues

last_alert_time = 0

import atexit
import signal

def exit_handler():
    send_discord_message("Monitor has exited")

def kill_handler(*args):
    sys.exit(0)

atexit.register(exit_handler)
signal.signal(signal.SIGINT, kill_handler)
signal.signal(signal.SIGTERM, kill_handler)

def send_discord_message(message):
    content = f"\U0001F988 **{message}**"
    payload = {
            "content": content,
            "allowed_mentions": {
                "roles": [STAFF_ROLE_ID]
                }
            }
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        if resp.status_code != 204:
            logging.warning("Discord alert failed: %s", resp.text)
    except Exception as e:
        logging.error("Error sending Discord alert: %s", e)

def send_discord_alert(reason, rms=None, variance=None, stderr=""):
    global last_alert_time
    now = time.time()
    if now - last_alert_time < ALERT_COOLDOWN_SECONDS:
        logging.info("Skipping Discord alert due to cooldown.")
        return
    last_alert_time = now

    content = f"<@&{STAFF_ROLE_ID}> \U0001F988 \U0001F6A8 **Stream issue detected**\n**Reason**: {reason}"
    if rms is not None:
        content += f"\n**RMS**: {rms:.2f}"
    if variance is not None:
        content += f"\n**Variance**: {variance:.2f}"
    if stderr:
        content += f"\n**FFmpeg Error**:\n```{stderr.strip()[:500]}```"

    payload = {
            "content": content,
            "allowed_mentions": {
                "roles": [STAFF_ROLE_ID]
                }
            }
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        if resp.status_code != 204:
            logging.warning("Discord alert failed: %s", resp.text)
    except Exception as e:
        logging.error("Error sending Discord alert: %s", e)


def check_streamer_connected():
    """
    Check if a live streamer/DJ is currently connected via Azuracast API.
    Returns tuple: (is_connected: bool, streamer_name: str or None, streamer_id: int or None)
    """
    if not AZURACAST_BASE_URL or not AZURACAST_API_KEY or not AZURACAST_STATION_ID:
        logging.warning("Azuracast API not configured. Skipping streamer check.")
        return False, None, None

    try:
        url = f"{AZURACAST_BASE_URL}/api/nowplaying/{AZURACAST_STATION_ID}"
        headers = {"X-API-Key": AZURACAST_API_KEY}

        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            logging.error(f"Azuracast API error: {resp.status_code} - {resp.text}")
            return False, None, None

        data = resp.json()
        live_info = data.get("live", {})
        is_live = live_info.get("is_live", False)
        streamer_name = live_info.get("streamer_name")

        # Try to get streamer ID from the broadcaster info if available
        streamer_id = live_info.get("broadcaster_id")

        logging.info(f"Streamer check: is_live={is_live}, streamer={streamer_name}, id={streamer_id}")
        return is_live, streamer_name, streamer_id

    except Exception as e:
        logging.error(f"Error checking streamer status: {e}")
        return False, None, None


def suspend_streamer(streamer_id):
    """
    Suspend a streamer account via Azuracast API.
    This prevents auto-reconnect by disabling the account entirely.
    Returns True if successful, False otherwise.
    """
    if not AZURACAST_BASE_URL or not AZURACAST_API_KEY or not AZURACAST_STATION_ID:
        logging.error("Azuracast API not configured. Cannot suspend streamer.")
        return False

    if not streamer_id:
        logging.error("No streamer ID provided. Cannot suspend.")
        return False

    try:
        # Suspend the streamer account to prevent reconnection
        streamer_url = f"{AZURACAST_BASE_URL}/api/station/{AZURACAST_STATION_ID}/streamer/{streamer_id}"
        headers = {"X-API-Key": AZURACAST_API_KEY}
        payload = {"is_active": False}

        resp = requests.put(streamer_url, headers=headers, json=payload, timeout=10)
        if resp.status_code in [200, 204]:
            logging.info(f"Successfully suspended streamer account ID {streamer_id}")
            return True
        else:
            logging.error(f"Failed to suspend streamer: {resp.status_code} - {resp.text}")
            return False

    except Exception as e:
        logging.error(f"Error suspending streamer: {e}")
        return False


def check_grace_period_active():
    """
    Check if a grace period is currently active by reading the timestamp file.
    Returns True if grace period is active, False otherwise.
    """
    try:
        if not os.path.exists(GRACE_PERIOD_FILE):
            return False

        with open(GRACE_PERIOD_FILE, 'r') as f:
            timestamp = float(f.read().strip())

        expiration = time.time()
        if timestamp > expiration:
            return True
        else:
            # Grace period expired, clean up the file
            os.remove(GRACE_PERIOD_FILE)
            return False

    except Exception as e:
        logging.error(f"Error checking grace period: {e}")
        return False


def grab_audio_sample(url, duration):
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-t", str(duration),        # 5 seconds capture
        "-i", url,
        "-f", "wav",
        "-ac", "1",
        "-ar", "44100",
        "pipe:1"
    ]

    try:
        logging.debug("Running ffmpeg command:", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False
        )
        logging.debug(f"ffmpeg exited with code {proc.returncode}")
        if proc.stdout:
            logging.debug(f"ffmpeg stdout length: {len(proc.stdout)} bytes")
        if proc.stderr:
            logging.debug("ffmpeg stderr output:")
            logging.debug(proc.stderr.decode(errors='replace'))
        else:
            logging.debug("No ffmpeg stderr output.")
        if proc.returncode != 0:
            return None
        return proc.stdout
    except subprocess.TimeoutExpired:
        logging.error("ffmpeg command timed out.")
        return None
    except Exception as e:
        logging.error(f"Exception running ffmpeg: {e}")
        return None

def analyze_audio(wav_bytes):
    audio = AudioSegment.from_file(BytesIO(wav_bytes), format="wav")
    samples = np.array(audio.get_array_of_samples()).astype(float)

    if len(samples) == 0:
        send_discord_alert("Audio sample is empty.")
        return False

    rms = np.sqrt(np.mean(samples**2))
    variance = np.var(samples)

    logging.info(f"Analyzed audio - RMS: {rms:.2f}, Variance: {variance:.2f}")

    if np.max(np.abs(samples)) == 0:
        send_discord_alert("Stream is completely silent (zero amplitude)", rms, variance)
        return False

    return True

SILENCE_ALERT_LEVEL = 2 # 2 x 60s = 2 minutes (no streamer threshold)
STREAMER_WARNING_THRESHOLD = 8  # 8 x 60s = 8 minutes
STREAMER_SUSPEND_THRESHOLD = 10  # 10 x 60s = 10 minutes

consecutive_silent_checks = 0

class MonitorContext:
    """Holds the state machine context and variables."""
    def __init__(self):
        self.state = MonitorState.NO_STREAMER
        self.consecutive_silent_checks = 0
        self.streamer_id = None
        self.streamer_name = None
        self.warning_sent = False

    def reset_counters(self):
        """Reset silence counters and warning flag."""
        self.consecutive_silent_checks = 0
        self.warning_sent = False

    def clear_streamer_info(self):
        """Clear current streamer information."""
        self.streamer_id = None
        self.streamer_name = None


def determine_next_state(ctx, is_streamer_connected, grace_period_active):
    """
    Determine the next state based on current state and conditions.
    Returns the new state or None if no transition needed.
    """
    current = ctx.state

    # Grace period transitions
    if grace_period_active and current == MonitorState.STREAMER_ACTIVE:
        return MonitorState.GRACE_PERIOD

    if not grace_period_active and current == MonitorState.GRACE_PERIOD:
        return MonitorState.STREAMER_ACTIVE if is_streamer_connected else MonitorState.NO_STREAMER

    # Streamer connection/disconnection transitions
    if is_streamer_connected and current == MonitorState.NO_STREAMER:
        return MonitorState.GRACE_PERIOD if grace_period_active else MonitorState.STREAMER_ACTIVE

    if not is_streamer_connected and current in [MonitorState.STREAMER_ACTIVE, MonitorState.GRACE_PERIOD]:
        return MonitorState.NO_STREAMER

    return None  # No transition


def handle_state_transition(ctx, new_state, streamer_name=None, streamer_id=None):
    """Execute a state transition with logging and counter resets."""
    previous_state = ctx.state
    ctx.state = new_state
    ctx.reset_counters()

    logging.info(f"State transition: {previous_state.value} -> {new_state.value}")

    # Handle transition-specific actions
    if new_state == MonitorState.GRACE_PERIOD and previous_state == MonitorState.STREAMER_ACTIVE:
        send_discord_message(f"Grace period activated for '{ctx.streamer_name}'. Monitoring paused.")

    elif new_state == MonitorState.STREAMER_ACTIVE and previous_state == MonitorState.GRACE_PERIOD:
        send_discord_message(f"Grace period expired for '{ctx.streamer_name}'. Normal monitoring resumed.")

    elif new_state == MonitorState.STREAMER_ACTIVE and previous_state == MonitorState.NO_STREAMER:
        ctx.streamer_id = streamer_id
        ctx.streamer_name = streamer_name
        logging.info(f"Streamer connected: {streamer_name} (ID: {streamer_id})")

    elif new_state == MonitorState.GRACE_PERIOD and previous_state == MonitorState.NO_STREAMER:
        ctx.streamer_id = streamer_id
        ctx.streamer_name = streamer_name
        logging.info(f"Streamer connected: {streamer_name} (ID: {streamer_id})")

    elif new_state == MonitorState.NO_STREAMER:
        logging.info(f"Streamer disconnected: {ctx.streamer_name}")
        ctx.clear_streamer_info()


def handle_no_streamer_silence(ctx):
    """Handle silence detection when no streamer is connected."""
    if ctx.consecutive_silent_checks == SILENCE_ALERT_LEVEL:
        send_discord_alert("🚨 **Stream silent for 2 minutes!** (No streamer connected)")
        ctx.consecutive_silent_checks = 0


def handle_streamer_active_silence(ctx):
    """Handle silence detection when a streamer is actively connected."""
    if ctx.consecutive_silent_checks == STREAMER_WARNING_THRESHOLD and not ctx.warning_sent:
        send_discord_alert(f"⚠️ **Forced disconnect imminent** - Streamer '{ctx.streamer_name}' has been silent for 8 minutes. Suspension in 2 minutes if silence continues.")
        ctx.warning_sent = True

    elif ctx.consecutive_silent_checks >= STREAMER_SUSPEND_THRESHOLD:
        if suspend_streamer(ctx.streamer_id):
            send_discord_alert(f"🚨 **Streamer forced off** - '{ctx.streamer_name}' has been suspended after 10 minutes of silence.")
        else:
            send_discord_alert(f"❌ **Failed to suspend streamer** - '{ctx.streamer_name}' has been silent for 10 minutes but suspension failed. Manual intervention required.")

        # Transition to NO_STREAMER after suspension
        logging.info(f"State transition: STREAMER_ACTIVE -> NO_STREAMER (post-suspension)")
        ctx.state = MonitorState.NO_STREAMER
        ctx.reset_counters()
        ctx.clear_streamer_info()


def handle_grace_period_silence(ctx):
    """Handle silence during grace period (no action, just logging)."""
    logging.info(f"⏸️ Grace period active. Silent checks: {ctx.consecutive_silent_checks} (monitoring paused)")


def handle_silence_by_state(ctx):
    """Route silence handling to the appropriate state handler."""
    if ctx.state == MonitorState.NO_STREAMER:
        handle_no_streamer_silence(ctx)
    elif ctx.state == MonitorState.STREAMER_ACTIVE:
        handle_streamer_active_silence(ctx)
    elif ctx.state == MonitorState.GRACE_PERIOD:
        handle_grace_period_silence(ctx)


def monitor_loop():
    send_discord_message("Greedy Shark is active")

    ctx = MonitorContext()

    while True:
        logging.info(f"🔁 Checking stream... [State: {ctx.state.value}]")

        # Check external conditions
        is_streamer_connected, streamer_name, streamer_id = check_streamer_connected()
        grace_period_active = check_grace_period_active()

        # Determine and execute state transitions
        new_state = determine_next_state(ctx, is_streamer_connected, grace_period_active)
        if new_state:
            handle_state_transition(ctx, new_state, streamer_name, streamer_id)

        # Sample and analyze audio
        wav_bytes = grab_audio_sample(STREAM_URL, SAMPLE_DURATION)

        if wav_bytes:
            is_active = analyze_audio(wav_bytes)
            if is_active:
                logging.info("✅ Stream is active and broadcasting.")
                ctx.reset_counters()
            else:
                ctx.consecutive_silent_checks += 1
                logging.warning(f"⚠️ Stream appears silent or inactive. ({ctx.consecutive_silent_checks} checks)")
        else:
            ctx.consecutive_silent_checks += 1
            logging.error(f"❌ Failed to retrieve audio sample. ({ctx.consecutive_silent_checks} checks)")

        # Handle silence based on current state
        handle_silence_by_state(ctx)

        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    monitor_loop()
