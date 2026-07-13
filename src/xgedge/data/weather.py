"""Point-in-time weather forecasts for official match venues."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping

import requests

from xgedge.data.point_in_time import available_snapshot, unavailable_snapshot

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
UEFA_MATCH_URL = "https://match.uefa.com/v5/matches/{match_id}"

VENUE_CITY_FALLBACK = {
    "Dallas Stadium": "Dallas",
    "Atlanta Stadium": "Atlanta",
}

WEATHER_CODES = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность",
    3: "пасмурно", 45: "туман", 48: "изморозь", 51: "слабая морось",
    53: "морось", 55: "сильная морось", 61: "слабый дождь",
    63: "дождь", 65: "сильный дождь", 71: "слабый снег",
    73: "снег", 75: "сильный снег", 80: "ливни", 81: "ливни",
    82: "сильные ливни", 95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом",
}


def _utc(value: str | datetime) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("weather timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _uefa_coordinates(payload: Mapping[str, Any]) -> tuple[float, float] | None:
    stadium = payload.get("stadium")
    geo = stadium.get("geolocation") if isinstance(stadium, Mapping) else None
    if not isinstance(geo, Mapping):
        return None
    lat, lon = _number(geo.get("latitude")), _number(geo.get("longitude"))
    return (lat, lon) if lat is not None and lon is not None else None


def resolve_coordinates(
    fixture: Mapping[str, Any], *, session: requests.Session, timeout: float
) -> tuple[float, float, str]:
    lat, lon = _number(fixture.get("latitude")), _number(fixture.get("longitude"))
    if lat is not None and lon is not None:
        return lat, lon, "official_fixture"
    if fixture.get("source") == "uefa" and fixture.get("id") is not None:
        response = session.get(
            UEFA_MATCH_URL.format(match_id=fixture["id"]),
            headers={"Accept": "application/json", "User-Agent": "xgedge-weather/1"},
            timeout=timeout,
        )
        response.raise_for_status()
        coordinates = _uefa_coordinates(response.json())
        if coordinates:
            return *coordinates, "official_uefa_stadium"
    query = fixture.get("venue_city") or VENUE_CITY_FALLBACK.get(str(fixture.get("venue")))
    if not query:
        raise ValueError("venue coordinates and verified city are unavailable")
    response = session.get(
        OPEN_METEO_GEOCODING_URL,
        params={"name": query, "count": 1, "language": "en", "format": "json"},
        headers={"Accept": "application/json", "User-Agent": "xgedge-weather/1"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") if isinstance(payload, Mapping) else None
    if not isinstance(results, list) or not results:
        raise ValueError("venue city was not found by geocoder")
    lat, lon = _number(results[0].get("latitude")), _number(results[0].get("longitude"))
    if lat is None or lon is None:
        raise ValueError("geocoder returned invalid coordinates")
    return lat, lon, "open_meteo_geocoding_verified_city"


def fetch_fixture_weather(
    fixture: Mapping[str, Any],
    *,
    snapshot_at: str | datetime,
    session: requests.Session | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch the hourly forecast nearest kickoff or return explicit unavailable."""
    captured, kickoff = _utc(snapshot_at), _utc(str(fixture["kickoff_utc"]))
    if captured >= kickoff:
        raise ValueError("weather snapshot must be captured before kickoff")
    client = session or requests.Session()
    try:
        latitude, longitude, coordinate_source = resolve_coordinates(
            fixture, session=client, timeout=timeout
        )
        response = client.get(
            OPEN_METEO_FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": "temperature_2m,precipitation,weather_code,wind_speed_10m",
                "timezone": "UTC",
                "start_date": kickoff.date().isoformat(),
                "end_date": kickoff.date().isoformat(),
            },
            headers={"Accept": "application/json", "User-Agent": "xgedge-weather/1"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        hourly = payload.get("hourly") if isinstance(payload, Mapping) else None
        if not isinstance(hourly, Mapping) or not isinstance(hourly.get("time"), list):
            raise ValueError("Open-Meteo hourly forecast is missing")
        times = [_utc(f"{value}:00Z" if len(str(value)) == 16 else str(value)) for value in hourly["time"]]
        if not times:
            raise ValueError("Open-Meteo hourly forecast is empty")
        index = min(range(len(times)), key=lambda i: abs((times[i] - kickoff).total_seconds()))

        def at(key: str) -> Any:
            values = hourly.get(key)
            return values[index] if isinstance(values, list) and index < len(values) else None

        code_value = at("weather_code")
        code = int(code_value) if _number(code_value) is not None else None
        record = {
            "fixture_id": str(fixture["id"]),
            "forecast_for": times[index].isoformat(timespec="seconds").replace("+00:00", "Z"),
            "temperature_c": _number(at("temperature_2m")),
            "precipitation_mm": _number(at("precipitation")),
            "wind_kph": _number(at("wind_speed_10m")),
            "weather_code": code,
            "condition": WEATHER_CODES.get(code, "код погоды не классифицирован") if code is not None else None,
            "latitude": latitude,
            "longitude": longitude,
            "coordinate_source": coordinate_source,
            "provider": "open_meteo",
        }
        if record["temperature_c"] is None or record["wind_kph"] is None:
            raise ValueError("Open-Meteo forecast contains incomplete core fields")
        return available_snapshot("open_meteo", [record], snapshot_at=captured)
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        return unavailable_snapshot(
            "open_meteo", f"{type(exc).__name__}: {exc}", snapshot_at=captured
        )
