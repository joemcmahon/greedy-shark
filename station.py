"""Station registry: describes the stations the bot and monitor handle."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import NamedTuple

GRACE_FILENAME = ".grace_period_until"
SUSPENDED_FILENAME = ".auto_suspended_streamers"
STATE_FILENAME = ".monitor_state"


class StatePaths(NamedTuple):
    grace: str
    suspended: str
    monitor_state: str


def state_paths(state_dir: str) -> StatePaths:
    return StatePaths(
        os.path.join(state_dir, GRACE_FILENAME),
        os.path.join(state_dir, SUSPENDED_FILENAME),
        os.path.join(state_dir, STATE_FILENAME),
    )


@dataclass(frozen=True)
class Station:
    name: str
    state_dir: str
    azuracast_station_id: str
    streamers_enabled: bool = False

    @property
    def paths(self) -> StatePaths:
        return state_paths(self.state_dir)


class StationConfigError(ValueError):
    pass


def load_stations(path: str) -> list[Station]:
    label = os.path.basename(path) or path
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise StationConfigError(f"{label}: invalid JSON ({e})") from e
    except OSError as e:
        raise StationConfigError(f"{label}: cannot read file ({e})") from e

    if not isinstance(data, list):
        raise StationConfigError(f"{label}: expected a list of stations")
    if not data:
        raise StationConfigError(f"{label}: station list is empty")

    stations: list[Station] = []
    seen: set[str] = set()
    dirs: dict[str, str] = {}
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise StationConfigError(f"{label}: entry {i} is not an object")
        for key in ("name", "state_dir", "azuracast_station_id"):
            value = entry.get(key)
            if not isinstance(value, (str, int)) or isinstance(value, bool) \
                    or not str(value).strip():
                raise StationConfigError(
                    f"{label}: entry {i} missing or invalid '{key}'"
                )
        name = str(entry["name"]).strip()
        if any(ch.isspace() for ch in name):
            raise StationConfigError(
                f"{label}: station name '{name}' must not contain whitespace "
                "(it is typed as one command argument)"
            )
        key = name.lower()
        if key in seen:
            raise StationConfigError(f"{label}: duplicate station name '{key}'")
        seen.add(key)
        norm_dir = os.path.normpath(str(entry["state_dir"]))
        if norm_dir in dirs:
            raise StationConfigError(
                f"{label}: stations '{dirs[norm_dir]}' and '{name}' share "
                f"state_dir '{norm_dir}'"
            )
        dirs[norm_dir] = name
        enabled = entry.get("streamers_enabled", False)
        if not isinstance(enabled, bool):
            raise StationConfigError(
                f"{label}: entry {i} 'streamers_enabled' must be true or false"
            )
        stations.append(
            Station(
                name=name,
                state_dir=str(entry["state_dir"]),
                azuracast_station_id=str(entry["azuracast_station_id"]),
                streamers_enabled=enabled,
            )
        )

    if sum(s.streamers_enabled for s in stations) > 1:
        raise StationConfigError(
            f"{label}: only one station may have streamers_enabled true"
        )
    return stations


def find_station(name: str | None, stations: list[Station]) -> Station | None:
    if name is None:
        return None
    wanted = name.strip().lower()
    for s in stations:
        if s.name.lower() == wanted:
            return s
    return None


def streamer_station(stations: list[Station]) -> Station | None:
    for s in stations:
        if s.streamers_enabled:
            return s
    return None


def station_names(stations: list[Station]) -> str:
    return ", ".join(s.name for s in stations)
