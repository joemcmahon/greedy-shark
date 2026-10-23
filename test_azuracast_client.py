from unittest.mock import MagicMock, patch

from azuracast_client import AzuracastClient

# Shapes taken from a real Azuracast instance: the now-playing "live" block
# carries the streamer's name but no ID; the streamers list carries both.
LIVE = {"is_live": True, "streamer_name": "Cypress Rosewood",
        "broadcast_start": 1792717205, "art": "https://example.com/art.jpg"}
NOT_LIVE = {"is_live": False, "streamer_name": "", "broadcast_start": None, "art": None}
STREAMERS = [
    {"id": 3, "streamer_username": "tony", "display_name": "Tony", "is_active": True},
    {"id": 15, "streamer_username": "cypress_rosewood", "display_name": "Cypress Rosewood",
     "is_active": True},
    {"id": 22, "streamer_username": "nodisplay", "display_name": "", "is_active": True},
]


def response(status, body):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    return resp


def fake_get(live, streamers=STREAMERS, streamers_status=200):
    def get(url, **kwargs):
        if "/nowplaying/" in url:
            return response(200, {"live": live})
        if url.endswith("/streamers"):
            return response(streamers_status, streamers)
        raise AssertionError(f"unexpected URL {url}")
    return get


def client():
    return AzuracastClient("https://azuracast.example.com", "key", "my_station")


class TestCheckStreamerConnected:
    def test_resolves_live_streamer_id_from_display_name(self):
        with patch("azuracast_client.requests.get", side_effect=fake_get(LIVE)):
            assert client().check_streamer_connected() == (True, "Cypress Rosewood", 15)

    def test_falls_back_to_username_when_display_name_is_empty(self):
        live = dict(LIVE, streamer_name="nodisplay")
        with patch("azuracast_client.requests.get", side_effect=fake_get(live)):
            assert client().check_streamer_connected() == (True, "nodisplay", 22)

    def test_match_is_case_and_whitespace_insensitive(self):
        live = dict(LIVE, streamer_name=" cypress rosewood ")
        with patch("azuracast_client.requests.get", side_effect=fake_get(live)):
            assert client().check_streamer_connected()[2] == 15

    def test_unknown_name_gives_no_id(self):
        live = dict(LIVE, streamer_name="Somebody Else")
        with patch("azuracast_client.requests.get", side_effect=fake_get(live)):
            assert client().check_streamer_connected() == (True, "Somebody Else", None)

    def test_ambiguous_name_gives_no_id(self):
        dupes = STREAMERS + [{"id": 99, "streamer_username": "cr2",
                              "display_name": "Cypress Rosewood", "is_active": True}]
        with patch("azuracast_client.requests.get", side_effect=fake_get(LIVE, dupes)):
            assert client().check_streamer_connected() == (True, "Cypress Rosewood", None)

    def test_streamer_list_failure_still_reports_live(self):
        with patch("azuracast_client.requests.get",
                   side_effect=fake_get(LIVE, streamers_status=500)):
            assert client().check_streamer_connected() == (True, "Cypress Rosewood", None)

    def test_not_live_skips_streamer_lookup(self):
        get = MagicMock(side_effect=fake_get(NOT_LIVE))
        with patch("azuracast_client.requests.get", get):
            assert client().check_streamer_connected() == (False, "", None)
        assert get.call_count == 1
