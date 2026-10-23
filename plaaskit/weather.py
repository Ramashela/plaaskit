"""Weather from Open-Meteo (free, no API key). Responses are cached so the tool works offline."""
from __future__ import annotations

import json
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from .db import data_dir

API = "https://api.open-meteo.com/v1/forecast"
DAILY = ("temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration,"
         "wind_gusts_10m_max,relative_humidity_2m_mean")


class WeatherUnavailable(Exception):
    pass


def parse(raw: dict) -> list[dict]:
    d = raw["daily"]
    n = len(d["time"])

    def col(key):
        return d.get(key) or [None] * n

    return [
        {"date": d["time"][i], "tmax": col("temperature_2m_max")[i], "tmin": col("temperature_2m_min")[i],
         "rain": col("precipitation_sum")[i], "et0": col("et0_fao_evapotranspiration")[i],
         "gust": col("wind_gusts_10m_max")[i], "rh": col("relative_humidity_2m_mean")[i]}
        for i in range(n)
    ]


def fetch(lat: float, lon: float, past_days: int = 7, forecast_days: int = 5, timeout: int = 20):
    """Return (days, stale). stale=True means cached data was used because the network failed."""
    qs = urlencode({"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "daily": DAILY,
                    "timezone": "Africa/Johannesburg", "past_days": past_days, "forecast_days": forecast_days})
    cache = data_dir() / f"weather_{lat:.2f}_{lon:.2f}.json"
    try:
        with urlopen(f"{API}?{qs}", timeout=timeout) as r:
            raw = json.load(r)
        parsed = parse(raw)
        cache.write_text(json.dumps(raw))
        return parsed, False
    except (URLError, OSError, ValueError, KeyError):
        if cache.exists():
            return parse(json.loads(cache.read_text())), True
        raise WeatherUnavailable("No internet and no cached forecast yet. Connect once to download one.") from None
