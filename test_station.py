import json

import pytest

from station import (
    StatePaths,
    Station,
    StationConfigError,
    find_station,
    load_stations,
    state_paths,
    station_names,
    streamer_station,
)


def _write(tmp_path, data):
    p = tmp_path / "stations.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data))
    return str(p)


def _entry(name, **kw):
    d = {"name": name, "state_dir": f"/s/{name}", "azuracast_station_id": "1"}
    d.update(kw)
    return d


def test_state_paths_join_filenames():
    assert state_paths("/s/a") == StatePaths(
        "/s/a/.grace_period_until",
        "/s/a/.auto_suspended_streamers",
        "/s/a/.monitor_state",
    )


def test_load_stations_valid(tmp_path):
    path = _write(tmp_path, [_entry("radiospiral"), _entry("pulse")])
    stations = load_stations(path)
    assert [s.name for s in stations] == ["radiospiral", "pulse"]
    assert all(s.streamers_enabled is False for s in stations)
    assert stations[0].paths == state_paths("/s/radiospiral")


def test_find_station_ignores_case_and_whitespace():
    stations = [Station("radiospiral", "/a", "1"), Station("pulse", "/b", "2")]
    assert find_station("  PULSE ", stations).name == "pulse"


def test_find_station_none_and_unknown_return_none():
    stations = [Station("pulse", "/b", "2")]
    assert find_station(None, stations) is None
    assert find_station("nope", stations) is None


@pytest.mark.parametrize(
    "data,msg",
    [
        ("{not json", "invalid JSON"),
        ({"name": "x"}, "list"),
        ([], "empty"),
        ([{"name": "a", "azuracast_station_id": "1"}], "state_dir"),
        ([_entry("Pulse"), _entry("pulse")], "duplicate station name"),
        (
            [_entry("a", streamers_enabled=True), _entry("b", streamers_enabled=True)],
            "streamers_enabled",
        ),
        (
            [_entry("pulse", state_dir="/s/x"), _entry("inner", state_dir="/s/x/")],
            "pulse.*inner.*/s/x|inner.*pulse.*/s/x",
        ),
        ([_entry("my station")], "whitespace"),
    ],
)
def test_load_rejects(tmp_path, data, msg):
    with pytest.raises(StationConfigError, match=msg):
        load_stations(_write(tmp_path, data))


def test_streamer_station_and_names():
    stations = [
        Station("radiospiral", "/a", "1", streamers_enabled=True),
        Station("pulse", "/b", "2"),
        Station("inner", "/c", "3"),
        Station("outer", "/d", "4"),
    ]
    assert streamer_station(stations).name == "radiospiral"
    assert streamer_station(stations[1:]) is None
    assert station_names(stations) == "radiospiral, pulse, inner, outer"
