from __future__ import annotations

from xgedge.data.weather import fetch_fixture_weather


class Response:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): return None
    def json(self): return self.payload


class Session:
    def __init__(self, payloads): self.payloads = iter(payloads); self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(next(self.payloads))


def test_weather_uses_official_uefa_coordinates_and_nearest_hour() -> None:
    session = Session([
        {"stadium": {"geolocation": {"latitude": 62.895, "longitude": 27.666}}},
        {"hourly": {
            "time": ["2026-07-14T14:00", "2026-07-14T15:00", "2026-07-14T16:00"],
            "temperature_2m": [18.0, 19.0, 20.0],
            "precipitation": [0.0, 0.4, 1.0],
            "weather_code": [1, 61, 63],
            "wind_speed_10m": [7.0, 8.0, 9.0],
        }},
    ])
    fixture = {
        "source": "uefa", "id": "1", "kickoff_utc": "2026-07-14T15:00:00Z",
        "venue": "Kuopio",
    }
    result = fetch_fixture_weather(
        fixture, snapshot_at="2026-07-13T00:00:00Z", session=session
    )

    assert result["status"] == "available"
    record = result["records"][0]
    assert record["temperature_c"] == 19.0
    assert record["condition"] == "слабый дождь"
    assert record["coordinate_source"] == "official_uefa_stadium"


def test_weather_fails_closed_when_location_is_unknown() -> None:
    result = fetch_fixture_weather(
        {"source": "fifa", "id": "x", "kickoff_utc": "2026-07-14T15:00:00Z", "venue": "Unknown"},
        snapshot_at="2026-07-13T00:00:00Z", session=Session([]),
    )
    assert result["status"] == "unavailable"
    assert result["records"] is None
