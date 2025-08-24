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

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

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

SILENCE_ALERT_LEVEL = 2 # 6 x 60s = ~60 seconds

consecutive_silent_checks = 0

def monitor_loop():
    send_discord_message("Greedy Shark is active")
    consecutive_silent_checks = 0

    while True:
        logging.info("🔁 Checking stream...")

        wav_bytes = grab_audio_sample(STREAM_URL, SAMPLE_DURATION)

        if wav_bytes:
            is_active = analyze_audio(wav_bytes)
            if is_active:
                logging.info("✅ Stream is active and broadcasting.")
                consecutive_silent_checks = 0  # reset on success
            else:
                consecutive_silent_checks += 1
                logging.warning("⚠️ Stream appears silent or inactive.")
        else:
            consecutive_silent_checks += 1
            logging.error("❌ Failed to retrieve audio sample.")

        if consecutive_silent_checks == SILENCE_ALERT_LEVEL:
            send_discord_alert("🚨 **listen failed for 2 minutes!** @Staff")
            consecutive_silent_checks = 0  # prevent continuous yelling

        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    monitor_loop()
