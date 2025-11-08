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

def monitor_loop():
    send_discord_message("Greedy Shark is active")

    # State machine variables
    current_state = MonitorState.NO_STREAMER
    consecutive_silent_checks = 0
    current_streamer_id = None
    current_streamer_name = None
    warning_sent = False

    while True:
        logging.info(f"🔁 Checking stream... [State: {current_state.value}]")

        # Check if a streamer is connected
        is_streamer_connected, streamer_name, streamer_id = check_streamer_connected()

        # State transition logic
        previous_state = current_state

        if is_streamer_connected and current_state == MonitorState.NO_STREAMER:
            # Transition: NO_STREAMER -> STREAMER_ACTIVE
            current_state = MonitorState.STREAMER_ACTIVE
            current_streamer_id = streamer_id
            current_streamer_name = streamer_name
            consecutive_silent_checks = 0
            warning_sent = False
            logging.info(f"State transition: {previous_state.value} -> {current_state.value}")
            logging.info(f"Streamer connected: {streamer_name} (ID: {streamer_id})")

        elif not is_streamer_connected and current_state == MonitorState.STREAMER_ACTIVE:
            # Transition: STREAMER_ACTIVE -> NO_STREAMER
            current_state = MonitorState.NO_STREAMER
            logging.info(f"State transition: {previous_state.value} -> {current_state.value}")
            logging.info(f"Streamer disconnected: {current_streamer_name}")
            current_streamer_id = None
            current_streamer_name = None
            consecutive_silent_checks = 0
            warning_sent = False

        # Sample and analyze audio
        wav_bytes = grab_audio_sample(STREAM_URL, SAMPLE_DURATION)

        if wav_bytes:
            is_active = analyze_audio(wav_bytes)
            if is_active:
                logging.info("✅ Stream is active and broadcasting.")
                consecutive_silent_checks = 0  # reset on success
                warning_sent = False
            else:
                consecutive_silent_checks += 1
                logging.warning(f"⚠️ Stream appears silent or inactive. ({consecutive_silent_checks} checks)")
        else:
            consecutive_silent_checks += 1
            logging.error(f"❌ Failed to retrieve audio sample. ({consecutive_silent_checks} checks)")

        # Handle silence based on current state
        if current_state == MonitorState.NO_STREAMER:
            # 2-minute rule when no streamer connected
            if consecutive_silent_checks == SILENCE_ALERT_LEVEL:
                send_discord_alert("🚨 **Stream silent for 2 minutes!** (No streamer connected)")
                consecutive_silent_checks = 0  # prevent continuous yelling

        elif current_state == MonitorState.STREAMER_ACTIVE:
            # 10-minute rule for connected streamers
            if consecutive_silent_checks == STREAMER_WARNING_THRESHOLD and not warning_sent:
                send_discord_alert(f"⚠️ **Forced disconnect imminent** - Streamer '{current_streamer_name}' has been silent for 8 minutes. Suspension in 2 minutes if silence continues.")
                warning_sent = True

            elif consecutive_silent_checks >= STREAMER_SUSPEND_THRESHOLD:
                if suspend_streamer(current_streamer_id):
                    send_discord_alert(f"🚨 **Streamer forced off** - '{current_streamer_name}' has been suspended after 10 minutes of silence.")
                else:
                    send_discord_alert(f"❌ **Failed to suspend streamer** - '{current_streamer_name}' has been silent for 10 minutes but suspension failed. Manual intervention required.")

                # Transition back to NO_STREAMER state after suspension
                current_state = MonitorState.NO_STREAMER
                consecutive_silent_checks = 0
                warning_sent = False
                current_streamer_id = None
                current_streamer_name = None
                logging.info(f"State transition: STREAMER_ACTIVE -> NO_STREAMER (post-suspension)")

        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    monitor_loop()
