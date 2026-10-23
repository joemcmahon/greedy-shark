"""Send one test alert through the monitor's notifier to check that the staff role ping works."""
import os
import logging
from dotenv import load_dotenv

from notifier import DiscordNotifier

# Config from environment
load_dotenv()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
STAFF_ROLE_ID = os.getenv("STAFF_ROLE_ID")

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

if __name__ == "__main__":
    DiscordNotifier(DISCORD_WEBHOOK_URL, STAFF_ROLE_ID).send_alert(
        "testing Greedy Shark alerting; please ignore")
