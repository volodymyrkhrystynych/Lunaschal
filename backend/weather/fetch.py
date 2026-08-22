"""Hourly weather from Open-Meteo — free, keyless, and the source for the
Lifestyle tab's weather card (backend/weather/sync.py).

The base URL is a hardcoded app constant, not attacker/LLM-chosen, so this
uses a plain `requests.get` with a timeout rather than the SSRF-guarded
fetcher in backend/research/web.py (that machinery exists specifically for
URLs an LLM picked; see backend/newspapers/scraper.py for the same plain-fetch
convention against another fixed third-party host).
"""
import time
from datetime import datetime

import requests

OPEN_METEO_URL = 'https://api.open-meteo.com/v1/forecast'
FETCH_TIMEOUT = 15

# past_days=1 + forecast_days=2 covers the widest window a 4am-anchored
# Lunaschal "day" can span: at 04:01 local the day just starting still needs
# hours from Open-Meteo's *previous* calendar date, and at 23:59 it still
# needs hours from the *next* one. sync.py filters this down to the requested
# day_key's own [start, end) window.
HOURLY_VARS = 'temperature_2m,relative_humidity_2m,weather_code,wet_bulb_temperature_2m'


def fetch_hourly(lat: float, lon: float) -> list[dict]:
    """Hourly readings around today, oldest first.

    Each item: {hour_ts, weather_code, temperature_c, wet_bulb_c, humidity_pct}.
    `hour_ts` is a unix second timestamp for the start of that local hour —
    Open-Meteo returns naive local time strings (`timezone=auto` resolves the
    offset from the coordinates), parsed with the same "one user, one
    timezone, naive datetimes" assumption backend/day_boundary.py uses.

    Raises on a non-200 response or a malformed payload; the caller
    (sync.sync_day) catches so one bad fetch doesn't wipe out existing rows.
    """
    resp = requests.get(
        OPEN_METEO_URL,
        params={
            'latitude': lat,
            'longitude': lon,
            'hourly': HOURLY_VARS,
            'temperature_unit': 'celsius',
            'timezone': 'auto',
            'past_days': 1,
            'forecast_days': 2,
        },
        timeout=FETCH_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    hourly = data['hourly']

    times = hourly['time']
    temps = hourly['temperature_2m']
    humidity = hourly['relative_humidity_2m']
    codes = hourly['weather_code']
    wet_bulb = hourly.get('wet_bulb_temperature_2m') or [None] * len(times)

    out = []
    for i, t in enumerate(times):
        out.append({
            'hour_ts': int(time.mktime(datetime.fromisoformat(t).timetuple())),
            'weather_code': codes[i],
            'temperature_c': temps[i],
            'wet_bulb_c': wet_bulb[i],
            'humidity_pct': humidity[i],
        })
    return out
