from __future__ import annotations
import json
import logging
import os
import time
from typing import Protocol

import requests


class Notifier(Protocol):
    def send_message(self, message: str) -> None: ...
    def send_alert(self, reason: str, rms: float | None = None,
                   variance: float | None = None, stderr: str = "") -> None: ...


class DiscordNotifier:
    def __init__(self, webhook_url: str, staff_role_id: str):
        self.webhook_url = webhook_url
        self.staff_role_id = staff_role_id
        self._last_alert_time: float = 0
        self._alert_cooldown: float = float(os.getenv("ALERT_COOLDOWN", 600))

    def send_message(self, message: str) -> None:
        payload = {
            "content": f"\U0001F988 **{message}**",
            "allowed_mentions": {"roles": [self.staff_role_id]},
        }
        try:
            resp = requests.post(self.webhook_url, json=payload)
            if resp.status_code != 204:
                logging.warning("Discord message failed: %s", resp.text)
        except Exception as e:
            logging.error("Error sending Discord message: %s", e)

    def send_alert(self, reason: str, rms: float | None = None,
                   variance: float | None = None, stderr: str = "") -> None:
        now = time.time()
        if now - self._last_alert_time < self._alert_cooldown:
            logging.info("Skipping Discord alert due to cooldown.")
            return
        self._last_alert_time = now

        content = (f"<@&{self.staff_role_id}> \U0001F988 \U0001F6A8 "
                   f"**Stream issue detected**\n**Reason**: {reason}")
        if rms is not None:
            content += f"\n**RMS**: {rms:.2f}"
        if variance is not None:
            content += f"\n**Variance**: {variance:.2f}"
        if stderr:
            content += f"\n**FFmpeg Error**:\n```{stderr.strip()[:500]}```"

        payload = {
            "content": content,
            "allowed_mentions": {"roles": [self.staff_role_id]},
        }
        try:
            resp = requests.post(self.webhook_url, json=payload)
            if resp.status_code != 204:
                logging.warning("Discord alert failed: %s", resp.text)
        except Exception as e:
            logging.error("Error sending Discord alert: %s", e)


class FileNotifier:
    def __init__(self, path) -> None:
        self.path = str(path)

    def send_message(self, message: str) -> None:
        self._append({"type": "message", "content": message})

    def send_alert(self, reason: str, rms: float | None = None,
                   variance: float | None = None, stderr: str = "") -> None:
        self._append({"type": "alert", "reason": reason,
                      "rms": rms, "variance": variance, "stderr": stderr})

    def _append(self, record: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def get_records(self) -> list[dict]:
        try:
            with open(self.path) as f:
                return [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []
