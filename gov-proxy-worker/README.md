# njw-gov-proxy

Cloudflare Worker on `maps.notjustweather.com/gov/*` (the only route; `/maps/*` is never touched). The app asks only our server for government map layers; NOAA, DWD, ECCC, KNMI and FMI see Cloudflare, not the phone.
Deploy (by hand): `(cd gov-proxy-worker && wrangler deploy)`. Local test: `wrangler dev --local --port 8799 --var DEBUG_UPSTREAM:1`.

## URL map and TTLs
The client sends only `bbox` (EPSG:3857 metres `minX,minY,maxX,maxY`) plus `width`/`height` (WMS layers) or `size=W,H` (NHC, air quality), each at most 1024, and optionally `time`. Everything else (service, layers, crs, format) is fixed in the Worker; any other query parameter gives 404. Only GET/HEAD/OPTIONS (POST etc. gives 405).

| Path | Upstream | TTL | Why |
|---|---|---|---|
| `/gov/noaa-radar/{conus,alaska,hawaii,carib,guam}` | opengeo.ncep.noaa.gov/geoserver/R/R_bref_qcd/ows (layer R_bref_qcd) | 120 s | SOURCES.md s3: every 2 min |
| `/gov/dwd-radar` | maps.dwd.de/geoserver/dwd/wms, dwd:Niederschlagsradar | 300 s | SOURCES.md: every 5 min |
| `/gov/eccc-radar` | geo.weather.gc.ca/geomet, RADAR_1KM_RRAI | 360 s | SOURCES.md: every 6 min |
| `/gov/knmi-radar` | geoservices.knmi.nl/wms?dataset=RADAR, RAD_NL25_PCP_CM | 300 s | SOURCES.md: every 5 min |
| `/gov/fmi-radar` | openwms.fmi.fi/geoserver/wms, Radar:suomi_dbz_eureffin (Finland, dBZ) | 300 s | SOURCES.md s3: every 5 min |
| `/gov/rmi-radar` | opendata.meteo.be/geoserver/wms, radar:belgian_rainfall_composite (Belgium, rain rate) | 300 s | SOURCES.md s3: every 5 min |
| `/gov/noaa-nhc` | mapservices.weather.noaa.gov/.../NHC_tropical_weather/MapServer/export (the layers list from SOURCES.md s4) | 600 s | advisories every 3-6 h; 10 min is a safe margin (chosen, not a documented interval) |
| `/gov/noaa-airquality/{pm25,ozone}` | mapservices.weather.noaa.gov/raster/.../ndgd_apm25_hr01 and ndgd_ozone_8hr_avg_time /ImageServer/exportImage | 1800 s | two model runs a day, hourly maps (SOURCES.md s5) |
| `/gov/nasa-goes-east/{z}/{y}/{x}.png`, `/gov/nasa-goes-west/{z}/{y}/{x}.png` | gibs.earthdata.nasa.gov GOES-East/West ABI Band 13 infrared, `default` (newest), Level6 | 600 s | every 10 min (SOURCES.md s2); zoom 0-6, row before column, no query string |
| `/gov/nasa-viirs/{YYYY-MM-DD}/{z}/{y}/{x}.jpg` | GIBS VIIRS NOAA-20 true colour, Level9 | 86400 s | one dated picture per day never changes; zoom 0-9; a date in the last 31 days (use yesterday) |

`time`: radar layers take an ISO instant `2026-10-18T12:00:00Z` (optional); air quality requires epoch milliseconds (12-13 digits).

## Example requests (one tile each, a tile box over the US Midwest / Europe)
- `https://maps.notjustweather.com/gov/noaa-radar/conus?bbox=-10018754,5009377,-9392582,5635549&width=256&height=256`
- `.../gov/dwd-radar?bbox=1113194,6446275,1565430,6800125&width=256&height=256`
- `.../gov/eccc-radar?bbox=-10018754,5009377,-9392582,5635549&width=256&height=256&time=2026-10-18T12:00:00Z`
- `.../gov/knmi-radar?bbox=445277,6800125,556597,6911445&width=256&height=256`
- `.../gov/fmi-radar?bbox=2504688,8400000,3130860,9000000&width=256&height=256`
- `.../gov/noaa-nhc?bbox=-10018754,5009377,-9392582,5635549&size=256,256`
- `.../gov/noaa-airquality/pm25?bbox=-10018754,5009377,-9392582,5635549&size=256,256&time=1792300000000`

Responses: image/png, `Cache-Control: public, max-age=<TTL>`, `Access-Control-Allow-Origin: *`, `X-NJW-Cache: HIT|MISS`. Upstream 404 passes through (cached 60 s); any other upstream failure, timeout (10 s) or a 200 that is not an image (WMS error XML) gives a bare 502 "Bad gateway". Draw nothing on 404/502.
Tip for the app: use the exact bbox numbers the tile grid produces and do not add extra parameters; the cache key is the rebuilt canonical URL, so identical tiles share one entry.

## Privacy
- The upstream request is built fresh: only `Accept` and `User-Agent: njw-maps-proxy (+https://notjustweather.com)`. No client IP header (X-Forwarded-For, CF-Connecting-IP, True-Client-IP are never copied), no cookies, no Referer, no client headers at all. Cloudflare itself may add its own edge headers on a subrequest outside Worker code; that is Cloudflare's, not copied from the phone, and not checked locally (local dev has no edge).
- Upstream gets only the tile box, size and time. Set-Cookie and every other upstream header are dropped (the response headers are rebuilt).
- Redirects are not followed.
- Verified in dev: with `DEBUG_UPSTREAM` set the Worker logs the outgoing headers; a request sent with X-Forwarded-For, Cookie and Referer produced upstream headers of only accept and user-agent.

## Limits
Free plan: 100,000 Worker requests/day. Every request to `/gov/*` is a Worker invocation, including cache HITs from `caches.default`, so caching saves upstream load and latency, not the daily quota. A map session of a few dozen tiles per view makes this a few thousand views a day at most; if it grows, the app should cache tiles on the phone (the max-age header allows it).
Caveat: `caches.default` is per Cloudflare data centre, so a HIT needs a previous request through the same one.

## Frame times: `GET /gov/radar-frames`
For the Radar slider. No parameters (any query string gives 404). Cached 120 s. JSON, read from each source's WMS GetCapabilities time Dimension for its one layer (ISO lists and start/end/PTnM intervals are expanded):

```
{"generated":"2026-10-18T00:46:30Z","sources":{"noaa-radar/conus":{"times":["2026-10-17T22:47:59Z", ... ,"2026-10-18T00:43:52Z"],"step_s":120},
 "noaa-radar/alaska":{...},"noaa-radar/hawaii":{...},"noaa-radar/carib":{...},"noaa-radar/guam":{...},
 "dwd-radar":{"times":[...],"step_s":300},"eccc-radar":{...,"step_s":360},"knmi-radar":{...,"step_s":300},"fmi-radar":{...,"step_s":300}}}
```
- `times`: oldest first, the frames from the last 2 h up to now (future nowcast frames, e.g. DWD's, are left out); if none are in the window, just the newest past frame. All in `YYYY-MM-DDTHH:MM:SSZ` (milliseconds dropped), which is exactly what the tile endpoint takes: `.../gov/dwd-radar?bbox=...&width=256&height=256&time=2026-10-18T00:45:00Z`.
- `step_s`: median gap between frames (NOAA's frames are irregular, about 2 min).
- A source that fails or times out (10 s) comes back as `{"times":[],"step_s":0}`; the others are kept. If every source fails the answer is not cached beyond 30 s.
- NOAA opengeo (all five regions) does have a time dimension (about 2 h of frames, `nearestValue`), so frames are listed for all.
