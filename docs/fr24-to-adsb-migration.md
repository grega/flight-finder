# Migration plan: FR24 → adsb.lol + adsbdb

Status: planned, not yet implemented. Authored 2026-05-09.

## Context

The current flight-finder service depends on a fork of `FlightRadarAPI` routed through a self-hosted Cloudflare Worker (a workaround for FR24's TLS-fingerprinting bot protection that landed 2026-05-01). We want to evaluate "genuinely free + supported" alternatives without touching production code yet — experiment in a new directory, prove the response shape works for the existing Interstate75 LED display, then plan a swap-in later.

Hard requirements for the new response (inventoried from `examples/interstate75/flight_display.py`):

- `found`, `distance_km`, `flight.number`, `flight.aircraft.model`, `flight.airline.iata`, `flight.route.origin_iata`, `flight.route.destination_iata`, `flight.position.latitude`, `flight.position.longitude` — all preserved.
- `heading`, `ground_speed`, `vertical_speed`, `callsign`, `icao_24bit`, `id`, `airline.icao`, `aircraft.code`, `aircraft.registration`, `route.origin_name`, `route.destination_name` — preserved when source data permits, null otherwise.

Decisions:
- **Aircraft model**: vendor a small ICAO-type → friendly-name JSON table (`B738` → "Boeing 737-800") rather than passing through adsbdb's raw manufacturer + variant strings.
- **Layout**: copy current files into a new directory; iterate there before any production migration.

## Approach

Create `experiments/adsb-service/` containing a self-contained second service that runs on a different port (`7479`) so it can run side-by-side with the still-deployed FR24-backed prod service for response-shape diffing. Keep the experimental code in a single `flight_service.py` (matches current style) plus a vendored aircraft-types data file.

### Data sources

- **Bulk feed** — `https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{nm}`. Free, no auth, BSD-3 (https://github.com/adsblol/api). Returns array of aircraft with `hex`, `flight` (callsign, may be padded with whitespace or empty), `r` (registration), `t` (ICAO type code), `lat`, `lon`, `alt_baro` (numeric or string `"ground"`), `gs`, `track`, `dst` (server-computed distance in **nautical miles** from search center). **Distance is in nm — must convert ×1.852 → km.**
- **Enrichment** — `https://api.adsbdb.com/v0/aircraft/{hex}?callsign={callsign}`. Free, no auth, MIT (https://github.com/mrjackwills/adsbdb). Returns aircraft + flightroute in **one** request (collapses what would have been 2N+1 lookups to N+1). Quirks:
  - 404/"unknown aircraft" returns `{"response": "unknown aircraft"}` — `response` is a **string**, not an object. Defensive: `isinstance(data.get("response"), dict)`.
  - Known aircraft + unknown callsign: `flightroute` key may be missing entirely.

No API keys, no per-second rate limits documented; both projects reserve the right to require keys eventually. Send a polite `User-Agent: flight-finder/2.0-experimental`.

### Why these two, not RadarBox / ADS-B Exchange / OpenSky?

| Source                  | Free?                                  | Auth?       | Returns ICAO type? | Returns route?      | Notes                                                                 |
|---                      |---                                     |---          |---                 |---                  |---                                                                    |
| **adsb.lol**            | Yes, BSD-3                             | No          | Yes (`t` field)    | No                  | Bulk lat/lon/radius. Compatible with adsb.fi & airplanes.live (drop-in) |
| **adsbdb**              | Yes, MIT                               | No          | Yes (manufacturer + variant) | **Yes** (callsign → IATA route + airline) | Single-maintainer; the only widely-known free callsign-route source |
| OpenSky Network         | Yes (free tier 4000 credits/day auth)  | Optional    | **No**             | **No**              | `/states/all` returns position + ICAO24 + callsign + 18 fields, but no aircraft type. Type/registration require offline join against [OpenSky aircraft DB CSV](https://opensky-network.org/datasets/metadata/) (~5-10 MB, monthly refresh). Route still needs adsbdb. Institutional (Bauhaus-Luftfahrt) — most stable bulk feed, but doubles complexity. |
| ADS-B Exchange (post-Jetnet) | "Community API" via RapidAPI; non-commercial | RapidAPI key | Yes        | Unclear             | No genuinely-free no-auth tier. Paid tiers have "minimum annual commitment." |
| AirNav RadarBox         | **No** (paid only, "minimum annual commitment") | Yes | Yes        | Yes                 | Out of scope for "genuinely free"                                     |
| FlightAware AeroAPI     | Free tier ~250 calls/month             | Yes         | Yes                | Yes                 | Free tier too small for a once-a-minute display poll (43,200/month)   |
| airplanes.live, adsb.fi | Yes, no auth                           | No          | Yes                | No                  | Same v2 API shape as adsb.lol — drop-in alternates                    |

**Why not OpenSky alone**: it's the most institutionally-stable option, but the absence of aircraft type in `/states/all` means we'd need to download and join the aircraft DB CSV, *and* still call adsbdb for route info. That's three moving parts versus two.

**Why adsb.lol over airplanes.live / adsb.fi**: functionally interchangeable. Pick one; if it goes away, hostname swap is the entire migration. Plan mitigates by reading the bulk-feed base URL from `ADSB_BASE_URL` env var (default `https://api.adsb.lol`). Same `/v2/lat/{lat}/lon/{lon}/dist/{nm}` path on all three.

**Single point of failure**: adsbdb has no equivalent free competitor for callsign-to-route lookup. If it goes away, the experimental service degrades to "no route, no airline IATA, no friendly model" while keeping position + type. Acceptable graceful degradation — the Pico display already uses `.get(..., "N/A")` defaults.

### Load profile: 3-5 Interstate75 devices polling every 30 s

Anticipated steady-state load is 3-5 devices × every 30 s = **8,640-14,400 service requests/day** (≈0.10-0.17 RPS sustained).

| Bulk source             | Daily budget                     | Fit at this load                                           |
|---                      |---                               |---                                                         |
| adsb.lol                | No documented per-second cap; "dynamic, casual-use friendly" | 0.17 RPS is well inside casual-use — fine                  |
| OpenSky (free auth)     | 4,000 credits/day                | **Over** at 3+ devices (216% at 3, 360% at 5)              |
| OpenSky (feeder tier)   | 8,000 credits/day                | **Over** at 3+ devices (108% at 3, 180% at 5) — and requires running an ADS-B feeder ≥30% uptime |

OpenSky becomes viable only if all devices share lat/lon (so Flask-side bulk-feed caching with a ~20 s TTL collapses N polls into 1 outbound call per cycle) **or** the operator becomes a feeder *and* drops polling to ≥60 s. Both add complexity for the privilege of the more institutional source.

adsbdb enrichment cost is independent of bulk-source choice and scales sub-linearly: with the 6h route cache and 30d aircraft cache, multi-device traffic to the same overhead aircraft collapses to one cold call + N hits. Estimate ~50-500 adsbdb calls/day total across all devices — comfortably within unstated limits.

**Conclusion**: load profile reinforces the adsb.lol choice. OpenSky stays documented as a fallback for users at lower scale or with a feeder rig, but isn't a fit for the actual deployment shape.

### Caching (cachetools.TTLCache, in-memory per gunicorn worker)

- `aircraft_cache` — keyed by uppercased hex. TTL 30 days. Aircraft type/registration is essentially immutable.
- `route_cache` — keyed by callsign. TTL 6 hours. Route assignments are stable across a flight schedule.
- `negative_cache` — keyed by `(hex, callsign)`. TTL 1 hour. Avoids hammering adsbdb for known-unknown lookups while letting transient outages recover.

### File layout

```
experiments/adsb-service/
├── README.md                  # what this is, how to run it side-by-side with prod
├── requirements.txt           # flask, requests, cachetools, python-dotenv, pytest
├── flight_service.py          # single-file rewrite (derived from root flight_service.py)
├── test_flight_service.py     # adapted tests
└── data/
    └── aircraft_types.json    # ~80-150 starter ICAO types → friendly model
```

Root `flight_service.py`, `requirements.txt`, `README.md`, `docs/dokku.md`, `examples/` — **untouched**. The experiment is fully isolated.

## Implementation detail

### `experiments/adsb-service/flight_service.py`

Single file. Functions:

- `fetch_aircraft_in_radius(lat, lon, radius_km) -> list[dict]` — converts km→nm, calls `{ADSB_BASE_URL}/v2/lat/{lat}/lon/{lon}/dist/{nm}` (default `ADSB_BASE_URL=https://api.adsb.lol`; swappable to airplanes.live or adsb.fi via env var), returns `ac` array. Raises `requests.RequestException` on network failure (caller maps to 503).
- `lookup_aircraft_and_route(hex_id, callsign) -> dict | None` — combined adsbdb endpoint. Returns `{"aircraft": dict|None, "flightroute": dict|None}` or `None` on 404/error. Backed by the three caches.
- `friendly_model(icao_type, manufacturer, raw_type) -> str` — checks vendored table first (e.g. `B738` → "Boeing 737-800"), falls back to `manufacturer + " " + raw_type`, then to `raw_type` or `icao_type`, then `"Unknown"`.
- `serialize_flight(ac, enrichment) -> dict` — builds the existing response shape from raw adsb.lol entry + enrichment. See mapping table below.
- Routes: `/health`, `/closest-flight`, `/flights-in-radius`, `/`. Same query params as today (`lat`, `lon`, `radius`). Same auth header (`X-API-Key`, optional via `SERVICE_API_KEY`).
- New optional query param on `/flights-in-radius`: `enrich=false` skips per-flight adsbdb calls (useful for the python-aircraft-monitor consumer that doesn't need model/route data).
- Per-request enrichment cap: `MAX_ENRICHMENT_PER_REQUEST = 25` — flights past the cap get null `aircraft.model`/`airline`/`route` but full position data.
- Default port: `int(os.getenv("PORT", 7479))` — different from prod's 7478, so they coexist.

### Field mapping

| Response key                | Source                                                                 |
|---                          |---                                                                     |
| `found`, `distance_km`      | top-level (computed from `dst * 1.852`)                                |
| `flight.id`                 | `ac["hex"]` lowercased                                                 |
| `flight.number`             | `flightroute.callsign_iata` if present (e.g. "AA2550"); else stripped raw `ac["flight"]` |
| `flight.callsign`           | stripped raw `ac["flight"]`                                            |
| `flight.icao_24bit`         | `ac["hex"]` uppercased                                                 |
| `flight.position.*`         | `lat`, `lon`, `alt_baro` (numeric only — filter `"ground"`), `track`, `gs`, `baro_rate` if present |
| `flight.aircraft.code`      | `ac["t"]`                                                              |
| `flight.aircraft.model`     | `friendly_model(...)` (uses vendored table)                            |
| `flight.aircraft.registration` | `enrichment.aircraft.registration` ?? `ac["r"]`                     |
| `flight.airline.iata/icao`  | `flightroute.airline.iata` / `.icao`                                   |
| `flight.route.origin_iata`  | `flightroute.origin.iata_code`                                         |
| `flight.route.destination_iata` | `flightroute.destination.iata_code`                                |
| `flight.route.origin_name`  | `flightroute.origin.name`                                              |
| `flight.route.destination_name` | `flightroute.destination.name`                                     |

When enrichment is `None` (404 or error), all enrichment-derived fields collapse to `null` — the Pico display already uses `.get(..., "N/A")` defaults so it degrades gracefully.

### Filtering

In `/closest-flight` and `/flights-in-radius`:
- Skip aircraft where `lat is None or lon is None` (Mode-S only, no position).
- Skip `alt_baro == "ground"` (matches today's `on_ground` filter).
- For `/closest-flight`: pick the entry with smallest `dst`.

### `experiments/adsb-service/data/aircraft_types.json`

Starter dictionary of ~80-150 entries covering common commercial + regional + GA types: 737/747/757/767/777/787 family, A220/A319/A320/A321/A330/A340/A350/A380 family, CRJ7/CRJ9/CRJX, E170/E175/E190/E195, Q400, ATR42/ATR72, B190, common GA (C172, C182, P28A, SR22, etc.), business jets (CL60, GLF5, GLF6, F2TH, etc.). Format:

```json
{
  "B738": "Boeing 737-800",
  "A321": "Airbus A321",
  "E75L": "Embraer E175LR"
}
```

Lifted from the Wikipedia ICAO Doc 8643 listing (factual data, license-clean). Unknown types fall through to `manufacturer + " " + raw_type`.

### Error handling

| Failure                                | HTTP / behavior                                                                |
|---                                     |---                                                                              |
| adsb.lol timeout / 5xx / network err   | 503 + `{"error": "Upstream feed unavailable"}`                                  |
| adsb.lol returns empty `ac`            | 200 `{"found": false, "message": "No flights found in search area"}`            |
| All aircraft on-ground or position-less| 200 `{"found": false, "message": "No airborne flights found in search area"}`   |
| adsbdb timeout / 5xx                   | log `app.logger.warning`; return aircraft with null enrichment fields. Cache nothing. |
| adsbdb 404 ("unknown aircraft")        | negative-cache for 1 h; return aircraft with null enrichment fields             |
| `requests.RequestException` general    | 5s timeout; caller decides 503 (bulk) or null-enrichment (lookup)               |

### `experiments/adsb-service/test_flight_service.py`

Adapted from root `test_flight_service.py`:
- Drop `DummyFlight` class (FR24-shaped).
- Define real-shape fixtures: `SAMPLE_ADSB_AC_AIRBORNE`, `SAMPLE_ADSB_AC_GROUNDED`, `SAMPLE_ADSBDB_FULL`, `SAMPLE_ADSBDB_NO_ROUTE`.
- Replace `@patch("flight_service.fr_api")` with `@patch("flight_service.fetch_aircraft_in_radius")` and `@patch("flight_service.lookup_aircraft_and_route")`.
- Update assertions to match new shape (e.g. `data["flight"]["aircraft"]["model"] == "Airbus A321"`).
- New tests:
  - 404 enrichment → response still `found: true` with null `aircraft.model` / null route fields.
  - Cache hit on second identical call (`mock.call_count == 1`).
  - `enrich=false` query param produces zero enrichment calls.
  - Empty-callsign aircraft enriched by hex only (no `?callsign=` in URL).
- Drop `test_calculate_bounds_basic` — `calculate_bounds` is not needed (adsb.lol takes lat/lon/radius directly).

### `experiments/adsb-service/requirements.txt`

```
Flask==3.1.2
requests>=2.32
cachetools>=5.5.0
python-dotenv>=1.2.1
pytest==9.0.0
```

No `gunicorn` (not running this in prod yet); no `FlightRadarAPI` deps.

### `experiments/adsb-service/README.md`

Short — explain it's an experimental rewrite, that it runs on port 7479 alongside prod's 7478, and how to run + test it. No deployment instructions yet.

## Verification

1. `cd experiments/adsb-service && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. `pytest -q` — all tests pass.
3. `python flight_service.py` — starts on `:7479`.
4. `curl "http://127.0.0.1:7479/closest-flight?lat=33.0118884&lon=-97.0558339&radius=25" | jq` — expect `found: true` with populated `aircraft.model`, `airline.iata`, `route.origin_iata`, `route.destination_iata`.
5. **Side-by-side diff**: hit the prod service on `:7478` with the same coordinates and visually compare:
   - `aircraft.model` strings should be similar (vendored table for known types; raw fallback for rare types — minor cosmetic differences expected).
   - `airline.iata` / `route.*_iata` should match.
   - `distance_km` should match within ~0.5 km.
   - `id`, `icao_24bit` may differ in casing.
6. Repeat the same `/closest-flight` curl twice within 30 s; check Flask logs — only the first call should produce an outbound adsbdb request (cache hit).
7. Test edge cases: middle-of-Atlantic (`lat=0&lon=0`) → `found: false`; `enrich=false` on `/flights-in-radius` → no enrichment fields populated.
8. Optional: point the Interstate75 emulator at `:7479` and confirm the LED layout still renders correctly (it should — only the response-shape contract matters).

If validation passes, a follow-up task migrates root `flight_service.py` and removes the FR24/Worker setup. That swap is **out of scope** for this plan.

## Critical files

- [flight_service.py](../flight_service.py) — source for the experimental copy (read-only during experiment).
- [test_flight_service.py](../test_flight_service.py) — source for the experimental copy (read-only during experiment).
- [examples/interstate75/flight_display.py](../examples/interstate75/flight_display.py) — consumer contract being preserved.
- New: `experiments/adsb-service/{flight_service.py,test_flight_service.py,requirements.txt,README.md,data/aircraft_types.json}`.
