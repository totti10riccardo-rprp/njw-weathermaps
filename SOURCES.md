# Weather map layers — sources, URLs, refresh (WEATHER-MAPS-2, first version, 17 Oct 2026)

Free sources only. No sign-ups and no keys anywhere below. Re-read each licence in full before relying on it.
Bucket `njw-maps` (its own; the GitHub key can write only there), public at https://maps.notjustweather.com. All tiles are 256 px Web Mercator (EPSG:3857), the scheme `MKTileOverlay` reads.

## 1. Forecast: rain, wind, temperature (ours, on R2)

- **Manifest:** `https://maps.notjustweather.com/maps/gfs/latest.json` (5 minute cache). Read it first; it names the run,
  the 16 steps (`fh` 3 … 48, every 3 hours, each with its `valid` time in UTC and its `key`), and per layer the tile
  template, zoom range, units and legend:
  `https://maps.notjustweather.com/maps/gfs/<run>/<layer>/<key>/{z}/{x}/{y}.png` with `<layer>` = `rain` | `wind` | `temp`.
  ⚠️ The template's `{fh}` is filled with the step's `key`, three digits (`"003"` … `"048"`), never the plain `fh` (3): a
  plain number is a 404 on every tile (measured 17 Oct 2026). Manifests before 17 Oct ~14:00 UTC have no
  `key`; pad `fh` to three digits.
- **Zoom:** rain 0 … 4, wind and temperature 0 … 3 (`maxZoom` in the manifest); above that, over-zoom (scale the parent
  tile). The model grid is 0.25° (~28 km), so more zoom would add nothing.
- **Rain:** a missing tile (404) means no rain anywhere in it (never written, to save writes): draw nothing.
- **Source:** NOAA GFS 0.25°, public domain, read from NOAA's open-data bucket on AWS. Credit "NOAA GFS".
- **Refresh:** four runs a day (00, 06, 12, 18 UTC). Each is drawn when its last file lands (~3 h 40 min after the run
  hour), so the newest tiles are ~4 hours old at publication. Tile objects for one run never change (cached a day);
  the manifest moves to the new run when it is fully uploaded. The current run and the one before stay on R2.
- **Runs on:** `render_gfs.py` in GitHub Actions (`.github/workflows/maps.yml`), started by a Cloudflare cron at the
  forecast release times (`cron-worker/`). The layers in sections 2–5 come straight from their providers.

## 2. Satellite clouds (NASA GIBS, straight from NASA)

- **Live infrared, the Americas only** (GOES-East and GOES-West, NOAA, public domain), every 10 minutes, ~1 hour behind:
  `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-East_ABI_Band13_Clean_Infrared/default/default/GoogleMapsCompatible_Level6/{z}/{y}/{x}.png`
  `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-West_ABI_Band13_Clean_Infrared/default/default/GoogleMapsCompatible_Level6/{z}/{y}/{x}.png`
  ⚠️ `{z}/{y}/{x}` (row before column). Zoom 0 … 6. `default` as the time is the newest picture.
- **Everywhere, once a day** (VIIRS NOAA-20 true colour, daytime pass): use YESTERDAY's date, because today's is incomplete until
  the satellite has passed:
  `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_NOAA20_CorrectedReflectance_TrueColor/default/<YYYY-MM-DD>/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`
- **Not used:** Himawari (Japan/Australia, JMA terms restrict commercial use) and Meteosat (not in GIBS, not free in real time).
- **UV: not available from NASA.** GIBS lists `CERES_Combined_Surface_UV_Index_All_Sky_Daily`, but it holds no dates at all
  (DescribeDomains: size 0, every tile 404, 17 Oct 2026), and the monthly layer ends July 2021. UV stays per place from
  WeatherKit until a keyless UV map is found (CAMS needs an account).
  Europe, Africa and Asia therefore have no live satellite layer in this version.
- **Credit:** "Imagery: NASA GIBS, NOAA GOES" (NASA asks for an acknowledgement).

## 3. Radar (governments, straight from them)

WMS: the app asks per tile with the tile's box in EPSG:3857 metres (`minX,minY,maxX,maxY`) and 256 × 256 px.

- **United States** (NOAA MRMS base reflectivity, quality-controlled), every 2 minutes, 2 hours of history (`&time=` for a loop),
  public domain, credit "NOAA":
  `https://opengeo.ncep.noaa.gov/geoserver/conus/conus_bref_qcd/ows?service=WMS&version=1.3.0&request=GetMap&layers=conus_bref_qcd&styles=&crs=EPSG:3857&bbox={bbox}&width=256&height=256&format=image/png&transparent=true`
  Same shape for Alaska, Hawaii, Puerto Rico and Guam: replace both `conus` with `alaska`, `hawaii`, `carib`, `guam`.
- **Germany** (DWD precipitation radar), every 5 minutes, with a 2-hour nowcast ahead (`&time=`), CC BY 4.0,
  credit "© Deutscher Wetterdienst":
  `https://maps.dwd.de/geoserver/dwd/wms?service=WMS&version=1.3.0&request=GetMap&layers=dwd:Niederschlagsradar&styles=&crs=EPSG:3857&bbox={bbox}&width=256&height=256&format=image/png&transparent=true`
- **Canada** (ECCC radar, rain rate, 1 km), every 6 minutes, 3 hours of history (`&time=`, PT6M), keyless; "Fees: None,
  AccessConstraints: None". Credit, required wording: "Contains information licenced under the Data Server End-use Licence
  of Environment and Climate Change Canada":
  `https://geo.weather.gc.ca/geomet?service=WMS&version=1.3.0&request=GetMap&layers=RADAR_1KM_RRAI&styles=&crs=EPSG:3857&bbox={bbox}&width=256&height=256&format=image/png&transparent=true`
- **Netherlands** (KNMI precipitation radar), every 5 minutes, keyless; capabilities say "Fees: no conditions apply",
  "AccessConstraints: None". Credit "KNMI" (KNMI's own wording not confirmed):
  `https://geoservices.knmi.nl/wms?dataset=RADAR&service=WMS&version=1.3.0&request=GetMap&layers=RAD_NL25_PCP_CM&styles=&crs=EPSG:3857&bbox={bbox}&width=256&height=256&format=image/png&transparent=true`
- Canada and the Netherlands each answered a real 256 px request with drawn rain and no key (17 Oct 2026 ~14:45 UTC).
- ⚠️ Direct requests send the viewed area and the phone's IP address to NOAA, DWD, ECCC, KNMI and NASA. A Cloudflare proxy would hide that
  (not built; free up to 100,000 requests a day).

## 4. Hurricanes and tropical storms (NOAA NHC, straight from NOAA)

- Forecast cone, track, points and coastal watches/warnings for every active storm in the Atlantic, East and Central
  Pacific (slots AT1–5, EP1–5, CP1–5), updated with each advisory (every 6 hours, 3 when a coast is warned). Public
  domain, keyless; credit "NOAA National Hurricane Center". ArcGIS export, per tile, same `{bbox}` as the radar:
  `https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/NHC_tropical_weather/MapServer/export?bbox={bbox}&bboxSR=3857&imageSR=3857&size=256,256&layers=show:6,7,8,9,32,33,34,35,58,59,60,61,84,85,86,87,110,111,112,113,136,137,138,139,162,163,164,165,188,189,190,191,214,215,216,217,240,241,242,243,266,267,268,269,292,293,294,295,318,319,320,321,344,345,346,347,370,371,372,373&format=png32&transparent=true&f=image`
- With no storm active the tile is transparent (seen 17 Oct: AT1 had 0 features). NHC says the service is "provided as a
  convenience": draw nothing on an error, never an error message.
- Not covered: the West Pacific and Indian Ocean (JMA, JTWC, GDACS terms not read yet).

## 5. Air quality, United States (NOAA NWS, straight from NOAA)

- Hourly forecast maps for the lower 48 states from NOAA's National Air Quality Forecast Capability: PM2.5 (µg/m³) and
  ozone (8-hour average, ppb), two runs a day (06 and 12 UTC), 48 hours ahead plus the hours before, keyless. US
  government work; credit "NOAA National Weather Service". ArcGIS image export per tile, same `{bbox}`; `time` = the valid
  hour in epoch milliseconds (the service lists its own range: `?f=json` → `timeInfo.timeExtent`):
  `https://mapservices.weather.noaa.gov/raster/rest/services/air_quality/ndgd_apm25_hr01/ImageServer/exportImage?bbox={bbox}&bboxSR=3857&imageSR=3857&size=256,256&time={epoch_ms}&format=png32&transparent=true&f=image`
  Ozone: replace `ndgd_apm25_hr01` with `ndgd_ozone_8hr_avg_time`. Smoke (`ndgd_smoke_sfc_1hr_avg_time`) and dust exist too.
- Seen 17 Oct 2026: 200 and a drawn PNG for both (PM2.5 also with `time` = the current hour). ⚠️ It is opaque over the whole US (clean air is
  green, not transparent): draw it at partial opacity. The legend: `…/ImageServer/legend?f=json`.
- No licence statement on the service itself; NWS's site-wide one says its information "is in the public domain, unless
  specifically noted otherwise" (https://www.weather.gov/disclaimer). +0 GitHub minutes.
- Outside the US: Copernicus CAMS (planned).

## 6. Points

- **Earthquakes:** USGS GeoJSON, public domain, updated every minute, credit "USGS":
  `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson` (also `4.5_week`, `significant_month`).
- **Weather alerts:** WeatherKit gives alerts per place, not per map area, so they are drawn at the person's own places.
  Credit as on the warnings screen (Apple Weather and the issuing agency).
