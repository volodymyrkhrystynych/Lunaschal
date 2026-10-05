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
HOURLY_VARS = ('temperature_2m,relative_humidity_2m,weather_code,wet_bulb_temperature_2m,'
               'apparent_temperature,wind_speed_10m,wind_gusts_10m,is_day')

# Open-Meteo's forecast endpoint serves at most this many past days; an entry
# captured earlier than that would need the separate archive API.
MAX_PAST_DAYS = 92


def fetch_hourly(lat: float, lon: float, past_days: int = 1) -> list[dict]:
    """Hourly readings around today, oldest first.

    Each item: {hour_ts, weather_code, temperature_c, wet_bulb_c, humidity_pct,
    apparent_c, wind_kmh, gust_kmh, is_day}. `apparent_c` is Open-Meteo's
    feels-like temperature, which folds in wind chill and humidity. `past_days`
    reaches further back for a capture that was uploaded late.

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
            'wind_speed_unit': 'kmh',
            'timezone': 'auto',
            'past_days': max(1, min(past_days, MAX_PAST_DAYS)),
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
    def optional(name):
        return hourly.get(name) or [None] * len(times)

    wet_bulb = optional('wet_bulb_temperature_2m')
    apparent = optional('apparent_temperature')
    wind = optional('wind_speed_10m')
    gusts = optional('wind_gusts_10m')
    is_day = optional('is_day')

    out = []
    for i, t in enumerate(times):
        out.append({
            'hour_ts': int(time.mktime(datetime.fromisoformat(t).timetuple())),
            'weather_code': codes[i],
            'temperature_c': temps[i],
            'wet_bulb_c': wet_bulb[i],
            'humidity_pct': humidity[i],
            'apparent_c': apparent[i],
            'wind_kmh': wind[i],
            'gust_kmh': gusts[i],
            'is_day': is_day[i],
        })
    return out


def fetch_sun_times(lat: float, lon: float) -> dict:
    """day_key -> {sunrise_ts, sunset_ts} for the same multi-day window
    fetch_hourly covers, via Open-Meteo's `daily=sunrise,sunset`. Its own
    request rather than folded into fetch_hourly, so a sun-times outage can't
    take down the hourly forecast that already works.

    Open-Meteo's `daily.time[i]` is a plain `YYYY-MM-DD` string, which matches
    backend.day_boundary.day_key_for()'s format directly.
    """
    resp = requests.get(
        OPEN_METEO_URL,
        params={
            'latitude': lat,
            'longitude': lon,
            'daily': 'sunrise,sunset',
            'timezone': 'auto',
            'past_days': 1,
            'forecast_days': 2,
        },
        timeout=FETCH_TIMEOUT,
    )
    resp.raise_for_status()
    daily = resp.json()['daily']
    times = daily['time']
    sunrise = daily['sunrise']
    sunset = daily['sunset']

    return {
        times[i]: {
            'sunrise_ts': int(time.mktime(datetime.fromisoformat(sunrise[i]).timetuple())),
            'sunset_ts': int(time.mktime(datetime.fromisoformat(sunset[i]).timetuple())),
        }
        for i in range(len(times))
    }
