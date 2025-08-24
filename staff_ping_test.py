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

def send_discord_message(message):
    content = f"\U0001F988 **{message}**"
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": content})
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

if __name__ == "__main__":
    send_discord_alert("testing Greedy Shark alerting; please ignore")
