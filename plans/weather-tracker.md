# Weather forecast/tracker

A new card in the Lifestyle tab, placed directly above the selfie strip, that shows the day's weather (condition, temperature, wet-bulb temperature) using the device's actual location.

## Motivation

The Lifestyle tab logs workouts, calories, selfies, and progression — daily context that's useful to correlate against conditions (e.g. a rough day matching a grey, rainy one). Weather also isn't tied to a fixed location: the app should use wherever the phone actually is, defaulting to a configured location when it doesn't have a fresher fix.

## Requirements

- At the start of each day, show forecasted conditions (sunny/cloudy/rain/etc), temperature, and wet-bulb temperature, hour by hour, like a normal weather app.
- Location comes from the iPhone via a browser geolocation request; a default location is used when that's unavailable.
- The location in use should be logged over time.
- Once an hour has passed, its stored weather should reflect what conditions actually were (at the most recently known location), not remain a stale forecast.

## Decisions

- **Weather provider: Open-Meteo.** Free, no API key. Confirmed (via its docs) to expose hourly `temperature_2m`, `wet_bulb_temperature_2m`, `relative_humidity_2m`, `weather_code`, and a `past_days` parameter that returns observed conditions for elapsed hours of the current day alongside forecast for the remainder — a direct match for "replace past forecast with what actually happened."
- **Refresh trigger: Lifestyle tab visit, not a background daemon.** Geolocation can only be captured while the frontend is open in the first place, so a scheduler thread polling in the background wouldn't add real freshness — only the on-visit path can ever get a genuine fix.
- **Units: Celsius.**
- **Branch: `feat/weather-tracker`.**

## Data model

Two new tables (`backend/db/schema.sql`), alongside the existing `lifestyle_selfies`/`calorie_logs` tables:

```sql
CREATE TABLE IF NOT EXISTS lifestyle_weather_hours (
    id TEXT PRIMARY KEY,
    day_key TEXT NOT NULL,        -- backend.day_boundary.day_key_for(): the app's 4am-anchored
                                   -- day, same boundary used by Lifestyle/Tasks/Journal/Chat
    hour_ts INTEGER NOT NULL,     -- unix seconds, start of the local hour this row describes
    weather_code INTEGER NOT NULL,
    temperature_c REAL NOT NULL,
    wet_bulb_c REAL,
    humidity_pct REAL,
    is_actual INTEGER NOT NULL DEFAULT 0,  -- 1 = hour_ts <= now at last sync, 0 = still forecast
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    location_source TEXT NOT NULL,  -- 'geolocation' | 'default'
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(day_key, hour_ts)
);
CREATE INDEX IF NOT EXISTS idx_weather_hours_day ON lifestyle_weather_hours(day_key);

CREATE TABLE IF NOT EXISTS lifestyle_weather_locations (
    id TEXT PRIMARY KEY,
    day_key TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    source TEXT NOT NULL,   -- 'geolocation' | 'default'
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weather_locations_day ON lifestyle_weather_locations(day_key, created_at);
```

Both tables are picked up automatically by `init_db()`'s `CREATE TABLE IF NOT EXISTS` executescript — no `_ensure_*` migration needed for the tables themselves. `hour_ts` is added to `TIMESTAMP_COLS` in `backend/db/connection.py` so `row_to_dict` ISO-stringifies it like every other timestamp column.

### Why a 4am day boundary, not a calendar date

`backend/day_boundary.py`'s `day_key_for()`/`day_bounds()` is already the convention `backend/routes/lifestyle.py` uses for "today" (also tasks, journal, paper, chat, briefing). Keying weather by calendar date instead would make "today's forecast" at 1am silently mean tomorrow's Open-Meteo date. The sync fetches a 2-day window from Open-Meteo (`past_days=1&forecast_days=2&timezone=auto`), converts each returned local-time string to `hour_ts`, then tags/filters rows with `day_key_for(hour_ts)`.

### Why upsert, not delete-and-reinsert

`UNIQUE(day_key, hour_ts)` + `INSERT ... ON CONFLICT DO UPDATE` is idempotent per (day, hour) the same way the newspaper sync is idempotent per (paper, date), but overwrites instead of skipping: every resync (a new geolocation fix, or the day's first tab visit) naturally rewrites elapsed hours as `is_actual=1` and remaining hours as fresh forecast, with no separate job needed to flip the flag. It's also crash-safe — an interrupted resync just leaves some hours stale until the next visit, never an emptied table.

### Why past hours get overwritten to the newest location, not preserved per-historical-location

The app has no continuous GPS trail, only "location as of the last tab visit." A resync at 2pm after traveling rewrites _all_ of today's rows — including 9am's — to reflect the new coordinates, because that's the best information available; there's no way to reconstruct what conditions actually were wherever the user was at 9am. `lifestyle_weather_locations` is what actually satisfies "location should be logged": an independent, append-only audit trail (deduped so a re-visit seconds later doesn't spam it) that answers "where did this app think I was at 9am," even after the weather row for 9am has since been overwritten.

## Backend

### `backend/weather/` (new package — pure/DB logic, no Flask, mirrors `backend/lifestyle/` and `backend/newspapers/`)

**`fetch.py`**

- `OPEN_METEO_URL = 'https://api.open-meteo.com/v1/forecast'` — a hardcoded constant, not attacker/LLM-controlled, so a plain `requests.get(..., timeout=15)` per the `backend/newspapers/scraper.py` convention. (The SSRF-guard machinery in `backend/research/web.py` exists specifically for LLM-chosen URLs and doesn't apply here.)
- `fetch_hourly(lat: float, lon: float) -> list[dict]` — params: `latitude`, `longitude`, `hourly='temperature_2m,relative_humidity_2m,weather_code,wet_bulb_temperature_2m'`, `temperature_unit='celsius'`, `timezone='auto'`, `past_days=1`, `forecast_days=2`. Verify the exact hourly variable name (`weather_code` vs `weathercode`) against Open-Meteo's live docs during implementation. Parses `hourly.time[i]` (naive local ISO string) into `hour_ts`. Raises on a non-200/malformed response — the caller catches so one bad fetch doesn't wipe existing data.

**`sync.py`**

- `resolve_location(db) -> tuple[float, float, str] | None` — the most recent `lifestyle_weather_locations` row (any day), else `settings.weather_default_lat/lon` if both are set, else `None`.
- `record_location(db, day_key, lat, lon, source) -> None` — inserts, deduped against the most recent row for that `day_key` (skipped if rounded lat/lon match).
- `sync_day(db, day_key, lat, lon, source) -> list[dict]` — `day_bounds(day_key)` window, `fetch_hourly`, upserts each hour with `is_actual = hour_ts <= time.time()`, returns rows ordered by `hour_ts`.
- `ensure_today(db) -> list[dict]` — the no-daemon entry point for `GET /today`: if rows already exist for the current `day_key_for()`, return them as-is (a GET must not refetch Open-Meteo on every render); otherwise (first visit of a new day) `resolve_location` + `sync_day`. If `resolve_location` returns `None`, return `[]` without calling Open-Meteo.

### `backend/routes/weather.py` (new blueprint)

`Blueprint('weather', __name__, url_prefix='/api/lifestyle/weather')`, registered in `backend/app.py`'s existing blueprint loop.

- **`GET /api/lifestyle/weather/today`** → `sync.ensure_today(db)`. Response: `{hours: [...], location: {latitude, longitude, source} | null}` — always 200; `location` lets the frontend show "using default location" / "not set" without an extra round trip.
- **`POST /api/lifestyle/weather/location`** → body `{latitude, longitude}`, validated via `backend.geo.coord_pair` (400 on a lone/invalid coordinate). On success: `record_location` + `sync_day` for the current `day_key_for()`, returning the same `{hours, location}` shape so the frontend can drop the response straight into its query cache.

Fallback when geolocation is denied or times out is handled **client-side only**: `src/lib/geo.ts`'s `currentPosition()` already resolves `null` rather than throwing, so `WeatherCard` simply skips the `POST /location` call — `GET /today` already guarantees the best available data (last-known location → default → empty).

### Settings — default location

`_ensure_weather_settings(db)` migration in `backend/db/connection.py` adds `weather_default_lat REAL`, `weather_default_lon REAL`, `weather_default_label TEXT` to `settings`, following the existing `_ensure_*`/`PRAGMA table_info` pattern. Raw lat/lon entry (no geocoding dependency), with a free-text label, mirroring the food-entry location fields. Exposed in `GET /api/settings`, added to `PATCH /api/settings/ai`'s `field_map` (validated via `coord_pair`). New `src/components/Settings/WeatherSection.tsx`, registered alongside `NudgeSection`/`BriefingSection`.

## Frontend

- **`src/lib/weather.ts`** (pure, `node`-env testable) — `WMO_CODES` lookup table (WMO code → `{label, icon}`, covering Open-Meteo's 0/1/2/3, 45/48, 51/53/55, 56/57, 61/63/65, 66/67, 71/73/75, 77, 80/81/82, 85/86, 95, 96/99), `describeWeatherCode(code)` with a safe "Unknown" fallback for unmapped codes, `currentHourIndex(hours)` for "now" highlighting/scroll targeting.
- **`src/hooks/api.ts`** — `WeatherHour`/`WeatherToday` interfaces near `interface Selfie`; new `lifestyle.weather = { today, updateLocation }` sub-resource alongside `selfies`/`calories`, using the file's existing `get`/`post` helpers.
- **`src/components/Lifestyle/WeatherCard.tsx`** (new, sibling to `SelfieCard.tsx`) — own `CARD`-shelled section. `useQuery(['lifestyle','weather','today'], api.lifestyle.weather.today)` always runs on mount — this alone satisfies the day-boundary case (below). A separate mount `useEffect`: `currentPosition()` (from `src/lib/geo.ts`, reused as-is) → if non-null, an `updateLocation` mutation whose `onSuccess` writes the response straight into the query cache via `setQueryData` instead of refetching. Renders a current-hour summary (icon/label, temperature, wet-bulb temperature) plus an hourly strip scrolled to "now" on mount, reusing the `scrollLeft`-on-`useLayoutEffect` pattern from `SelfieCard`/`ActivityHeatmap` (commit `6cf4220`). If `location === null`, shows a "set a default location in Settings" prompt instead of a strip.
- **`src/components/Lifestyle/Lifestyle.tsx`** — one-line change: insert `<WeatherCard />` between `<CaloriesCard />` and `<SelfieCard />` in the right-hand column.

## Day-boundary edge case

There is no "start of day" scheduler. The first `GET /today` of a new `day_key` — which happens automatically because `WeatherCard`'s query always fires on mount, and mounting the Lifestyle tab _is_ the trigger — finds no rows for the new day and runs the full `resolve_location` + `sync_day` fetch inline, using whatever location was last logged (possibly from yesterday) or the configured default. The `POST /location` that follows once `currentPosition()` resolves then immediately resyncs with a fresh fix if one's available. So a 4:01am tab visit with geolocation denied still gets a full day's forecast, just anchored to stale/default coordinates until a later visit that day supplies a real fix.

## Tests

**Backend (`backend/tests/test_weather.py`, new)** — mock `fetch_hourly` (monkeypatch, no real network):

- `sync_day` upserts exactly the `day_bounds` window; `is_actual` correct relative to a monkeypatched `time.time()`.
- Resyncing the same day with new coordinates overwrites existing rows (row count unchanged, values changed) rather than duplicating.
- `resolve_location` fallback chain: location log → default settings → `None`.
- `record_location` dedupe: identical rounded coords in the same `day_key` → one row; a materially different coord → a second row.
- Routes via the `client` fixture: `GET /today` on an empty DB → `{hours: [], location: None}`, 200; `POST /location` with an invalid/missing coord → 400; `POST /location` success → hours covering `day_bounds`; `GET /today` called twice does not refetch Open-Meteo a second time.
- `fetch.py`: monkeypatch `requests.get` with a canned Open-Meteo JSON payload, assert parsed `hour_ts`/field mapping.

**Frontend:**

- `src/lib/weather.test.ts` — `describeWeatherCode` for a representative code per WMO family, plus the unknown-code fallback.
- `src/components/Lifestyle/WeatherCard.test.tsx` (`// @vitest-environment jsdom`) — mock the API client and `navigator.geolocation` (per `src/lib/geo.test.ts`'s existing pattern) — assert `updateLocation` fires with resolved coords on mount, is skipped (no crash) when geolocation resolves `null`, and the "set a default location" prompt renders when `location: null`.

## Open questions / risks

- Exact Open-Meteo hourly variable name (`weather_code` vs `weathercode`) and response shape should be confirmed against the live API during implementation.
- Manual on-device verification (actual iPhone geolocation permission prompt, actual Open-Meteo response) hasn't been done as part of this plan — automated tests are the primary safety net per this repo's working conventions, but a real run is worth doing once merged.
