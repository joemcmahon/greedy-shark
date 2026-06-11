from __future__ import annotations
import logging
import requests


class AzuracastClient:
    def __init__(self, base_url: str, api_key: str, station_id: str) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.station_id = station_id
        self._headers = {"X-API-Key": api_key}

    def _configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.station_id)

    def check_streamer_connected(self) -> tuple[bool, str | None, int | None]:
        if not self._configured():
            logging.warning("Azuracast API not configured. Skipping streamer check.")
            return False, None, None
        try:
            url = f"{self.base_url}/api/nowplaying/{self.station_id}"
            resp = requests.get(url, headers=self._headers, timeout=10)
            if resp.status_code != 200:
                logging.error("Azuracast API error: %s - %s", resp.status_code, resp.text)
                return False, None, None
            live = resp.json().get("live", {})
            is_live = live.get("is_live", False)
            name = live.get("streamer_name")
            sid = live.get("broadcaster_id")
            logging.info("Streamer check: is_live=%s, streamer=%s, id=%s", is_live, name, sid)
            return is_live, name, sid
        except Exception as e:
            logging.error("Error checking streamer status: %s", e)
            return False, None, None

    def suspend_streamer(self, streamer_id: int) -> bool:
        if not self._configured():
            logging.error("Azuracast API not configured. Cannot suspend streamer.")
            return False
        try:
            url = f"{self.base_url}/api/station/{self.station_id}/streamer/{streamer_id}"
            resp = requests.put(url, headers=self._headers,
                                json={"is_active": False}, timeout=10)
            if resp.status_code in [200, 204]:
                logging.info("Suspended streamer ID %s", streamer_id)
                return True
            logging.error("Failed to suspend: %s - %s", resp.status_code, resp.text)
            return False
        except Exception as e:
            logging.error("Error suspending streamer: %s", e)
            return False

    def reactivate_streamer(self, streamer_id: int) -> bool:
        if not self._configured():
            logging.error("Azuracast API not configured. Cannot reactivate streamer.")
            return False
        try:
            url = f"{self.base_url}/api/station/{self.station_id}/streamer/{streamer_id}"
            resp = requests.put(url, headers=self._headers,
                                json={"is_active": True}, timeout=10)
            if resp.status_code in [200, 204]:
                logging.info("Reactivated streamer ID %s", streamer_id)
                return True
            logging.error("Failed to reactivate: %s - %s", resp.status_code, resp.text)
            return False
        except Exception as e:
            logging.error("Error reactivating streamer: %s", e)
            return False

    def get_all_streamers(self) -> list[dict] | None:
        if not self._configured():
            logging.error("Azuracast API not configured. Cannot fetch streamers.")
            return None
        try:
            url = f"{self.base_url}/api/station/{self.station_id}/streamers"
            resp = requests.get(url, headers=self._headers, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            logging.error("Failed to fetch streamers: %s - %s", resp.status_code, resp.text)
            return None
        except Exception as e:
            logging.error("Error fetching streamers: %s", e)
            return None
