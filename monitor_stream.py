import os
import shutil
import subprocess
import tempfile
import time
import logging
import requests
import numpy as np
import json
import sys
from io import BytesIO
from pydub import AudioSegment
from dotenv import load_dotenv
from enum import Enum
from datetime import datetime

from azuracast_client import AzuracastClient
from notifier import DiscordNotifier, Notifier

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

# Auto-suspension tracking
AUTO_SUSPENDED_FILE = ".auto_suspended_streamers"

# Monitor state tracking (shared with bot)
MONITOR_STATE_FILE = ".monitor_state"

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# State machine for monitoring modes
class MonitorState(Enum):
    NO_STREAMER = "no_streamer"           # No streamer connected, 2-minute rule
    STREAMER_ACTIVE = "streamer_active"   # Streamer connected, 10-minute rule
    GRACE_PERIOD = "grace_period"         # Future: streamer acknowledged issues

import atexit
import signal

def kill_handler(*args):
    sys.exit(0)


def check_grace_period_active():
    """
    Check if a grace period is currently active by reading the timestamp file.
    Returns True if grace period is active, False otherwise.
    """
    try:
        if not os.path.exists(GRACE_PERIOD_FILE):
            return False

        with open(GRACE_PERIOD_FILE, 'r') as f:
            content = f.read().strip()

        # Empty file means no grace period
        if not content:
            return False

        timestamp = float(content)

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


def check_and_fix_suspended_file_path():
    if os.path.exists(AUTO_SUSPENDED_FILE) and not os.path.isfile(AUTO_SUSPENDED_FILE):
        logging.warning(
            "'%s' is a directory, not a file — removing it so the bot can start cleanly",
            AUTO_SUSPENDED_FILE,
        )
        shutil.rmtree(AUTO_SUSPENDED_FILE)


def load_auto_suspended_streamers():
    """
    Load the list of auto-suspended streamers from file.
    Returns dict: {streamer_id: {name, suspended_at, reason}}
    """
    if os.path.exists(AUTO_SUSPENDED_FILE) and not os.path.isfile(AUTO_SUSPENDED_FILE):
        raise RuntimeError(
            f"'{AUTO_SUSPENDED_FILE}' exists but is a directory, not a file — "
            "remove it and restart the bot"
        )
    try:
        if not os.path.exists(AUTO_SUSPENDED_FILE):
            return {}

        with open(AUTO_SUSPENDED_FILE, 'r') as f:
            return json.load(f)

    except Exception as e:
        logging.error(f"Error loading auto-suspended streamers: {e}")
        return {}


def save_auto_suspended_streamers(suspended_dict):
    """
    Save the list of auto-suspended streamers to file.
    """
    if os.path.exists(AUTO_SUSPENDED_FILE) and not os.path.isfile(AUTO_SUSPENDED_FILE):
        raise RuntimeError(
            f"'{AUTO_SUSPENDED_FILE}' exists but is a directory, not a file — "
            "remove it and restart the bot"
        )
    try:
        with open(AUTO_SUSPENDED_FILE, 'w') as f:
            json.dump(suspended_dict, f, indent=2)

    except Exception as e:
        logging.error(f"Error saving auto-suspended streamers: {e}")


def add_auto_suspended_streamer(streamer_id, streamer_name, reason="10 minutes of silence"):
    """
    Add a streamer to the auto-suspension tracking list.
    """
    suspended = load_auto_suspended_streamers()
    suspended[str(streamer_id)] = {
        "name": streamer_name,
        "suspended_at": datetime.now().isoformat(),
        "reason": reason
    }
    save_auto_suspended_streamers(suspended)
    logging.info(f"Added {streamer_name} (ID: {streamer_id}) to auto-suspended list")


def remove_auto_suspended_streamer(streamer_id):
    """
    Remove a streamer from the auto-suspension tracking list.
    Returns True if removed, False if not found.
    """
    suspended = load_auto_suspended_streamers()
    streamer_id_str = str(streamer_id)

    if streamer_id_str in suspended:
        del suspended[streamer_id_str]
        save_auto_suspended_streamers(suspended)
        logging.info(f"Removed streamer ID {streamer_id} from auto-suspended list")
        return True

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


def analyze_audio(wav_bytes: bytes) -> tuple[bool, dict | None]:
    """Returns (is_active, alert_info). alert_info is None when audio is present."""
    if not wav_bytes:
        return False, {"reason": "No audio data received.", "rms": None, "variance": None}
    audio = AudioSegment.from_file(BytesIO(wav_bytes), format="wav")
    samples = np.array(audio.get_array_of_samples()).astype(float)

    if len(samples) == 0:
        return False, {"reason": "Audio sample is empty.", "rms": None, "variance": None}

    rms = float(np.sqrt(np.mean(samples**2)))
    variance = float(np.var(samples))
    logging.info(f"Analyzed audio - RMS: {rms:.2f}, Variance: {variance:.2f}")

    if np.max(np.abs(samples)) == 0:
        return False, {"reason": "Stream is completely silent (zero amplitude)",
                       "rms": rms, "variance": variance}

    return True, None


SILENCE_ALERT_LEVEL = 2 # 2 x 60s = 2 minutes (no streamer threshold)
STREAMER_WARNING_THRESHOLD = 4  # 4 x 60s = 4 minutes
STREAMER_SUSPEND_THRESHOLD = 10  # 10 x 60s = 10 minutes


class MonitorContext:
    """Holds the state machine context and injectable dependencies."""
    def __init__(self, notifier: Notifier, azuracast: AzuracastClient,
                 sample_fn) -> None:
        self.notifier = notifier
        self.azuracast = azuracast
        self.sample_fn = sample_fn
        self.state = MonitorState.NO_STREAMER
        self.consecutive_silent_checks = 0
        self.streamer_id = None
        self.streamer_name = None
        self.warning_sent = False

    def reset_counters(self):
        self.consecutive_silent_checks = 0
        self.warning_sent = False

    def clear_streamer_info(self):
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
        ctx.notifier.send_message(f"Grace period activated for '{ctx.streamer_name}'. Monitoring paused.")

    elif new_state == MonitorState.STREAMER_ACTIVE and previous_state == MonitorState.GRACE_PERIOD:
        ctx.notifier.send_message(f"Grace period expired for '{ctx.streamer_name}'. Normal monitoring resumed.")

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
        ctx.notifier.send_alert("🚨 **Stream silent for 2 minutes!** (No streamer connected)")
        ctx.consecutive_silent_checks = 0


def handle_streamer_active_silence(ctx):
    """Handle silence detection when a streamer is actively connected."""
    if ctx.consecutive_silent_checks == STREAMER_WARNING_THRESHOLD and not ctx.warning_sent:
        ctx.notifier.send_alert(
            f"⚠️ **Action may be needed soon** - Streamer '{ctx.streamer_name}' has been silent "
            f"for {STREAMER_WARNING_THRESHOLD} minutes. Use `!shark <id>` to suspend if needed.")
        ctx.warning_sent = True
    elif ctx.consecutive_silent_checks >= STREAMER_SUSPEND_THRESHOLD:
        ctx.notifier.send_alert(
            f"🚨 **Staff action required** - Streamer '{ctx.streamer_name}' has been silent "
            f"for {ctx.consecutive_silent_checks} minutes. Use `!streamers` then `!shark <id>` to suspend.")


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


def save_monitor_state(ctx):
    """Save current monitor state to file for bot access."""
    try:
        state_data = {
            "state": ctx.state.value,
            "consecutive_silent_checks": ctx.consecutive_silent_checks,
            "streamer_name": ctx.streamer_name or "",
            "streamer_id": ctx.streamer_id,
            "timestamp": time.time()
        }
        with open(MONITOR_STATE_FILE, 'w') as f:
            json.dump(state_data, f)
    except Exception as e:
        logging.error(f"Error saving monitor state: {e}")


def monitor_loop():
    notifier = DiscordNotifier(DISCORD_WEBHOOK_URL, STAFF_ROLE_ID)
    client = AzuracastClient(AZURACAST_BASE_URL, AZURACAST_API_KEY, AZURACAST_STATION_ID)
    ctx = MonitorContext(notifier=notifier, azuracast=client, sample_fn=grab_audio_sample)

    atexit.register(lambda: notifier.send_message("Monitor has exited"))
    signal.signal(signal.SIGINT, kill_handler)
    signal.signal(signal.SIGTERM, kill_handler)

    notifier.send_message("Greedy Shark is active")

    while True:
        logging.info(f"🔁 Checking stream... [State: {ctx.state.value}]")

        is_streamer_connected, streamer_name, streamer_id = ctx.azuracast.check_streamer_connected()
        grace_period_active = check_grace_period_active()

        new_state = determine_next_state(ctx, is_streamer_connected, grace_period_active)
        if new_state:
            handle_state_transition(ctx, new_state, streamer_name, streamer_id)

        wav_bytes = ctx.sample_fn(STREAM_URL, SAMPLE_DURATION)

        if wav_bytes:
            is_active, alert_info = analyze_audio(wav_bytes)
            if is_active:
                logging.info("✅ Stream is active and broadcasting.")
                ctx.reset_counters()
            else:
                ctx.consecutive_silent_checks += 1
                if alert_info:
                    ctx.notifier.send_alert(**alert_info)
                logging.warning(f"⚠️ Stream appears silent or inactive. ({ctx.consecutive_silent_checks} checks)")
        else:
            ctx.consecutive_silent_checks += 1
            logging.error(f"❌ Failed to retrieve audio sample. ({ctx.consecutive_silent_checks} checks)")

        handle_silence_by_state(ctx)
        save_monitor_state(ctx)
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    monitor_loop()
