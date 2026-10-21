# Weather map layers — sources, URLs, refresh (WEATHER-MAPS-2, first version, 17 Oct 2026)

Free sources only. No sign-ups and no keys anywhere below. Re-read each licence in full before relying on it.
Bucket `njw-maps` (its own; the GitHub key can write only there), public at https://maps.notjustweather.com. All tiles are 256 px Web Mercator (EPSG:3857), the scheme `MKTileOverlay` reads.

## 1. Forecast: rain, wind, temperature (ours, on R2)

- **Manifest:** `https://maps.notjustweather.com/maps/gfs/latest.json` (5 minute cache). Read it first; it names the run,
  the 48 steps (`fh` 1 … 48, every hour since 18 Oct 2026; 3-hourly before), each with its `valid` time in UTC and its `key`), and per layer the tile
  template, zoom range, units and legend:
  `https://maps.notjustweather.com/maps/gfs/<run>/<layer>/<key>/{z}/{x}/{y}.png` with `<layer>` = `rain` | `wind` | `temp`.
  ⚠️ The template's `{fh}` is filled with the step's `key`, three digits (`"003"` … `"048"`), never the plain `fh` (3): a
  plain number is a 404 on every tile (measured 17 Oct 2026). Manifests before 17 Oct ~14:00 UTC have no
  `key`; pad `fh` to three digits.
- **Zoom:** rain 0 … 4, wind and temperature 0 … 3 (`maxZoom` in the manifest); above that, over-zoom (scale the parent
  tile). The model grid is 0.25° (~28 km), so more zoom would add nothing.
- **Rain:** a missing tile (404) means no rain anywhere in it (never written, to save writes): draw nothing.
- **Data for the app to draw itself** (18 Oct 2026), one PNG per step beside the tiles, named in the manifest:
  `layers.wind.field` (1°, 360 × 181, R = u, G = v), and `layers.temp.grid` / `layers.rain.grid`: 0.5°, 720 × 361,
  x = lon -180 … 179.5, row 0 = 90° N, point samples of the 0.25° grid. Temperature grey, °C = byte / 2 - 64 (0.5 °C
  steps). Rain RGB: R = 0 dry (under 0.1 mm/h), else mm/h = 10 ^ (R / 100) - 1 (within 3 %); G = snow share, B = mix share
  of 255, rain the rest. Measured on the 17 Oct 18z run: ~77 kB (temperature) and ~65 kB (rain) per hour.
  Cloud has a grid and no tiles: top-level `grids.cloud` (same 0.5° geometry), total cloud cover % = byte / 2.5, ~130 kB
  per hour. A layer under `grids` never appears under `layers`.
- **Source:** NOAA GFS 0.25°, public domain, read from NOAA's open-data bucket on AWS. Credit "NOAA GFS, processed" (21 Oct 2026: NOAA asks that modified data not be presented as original, as the radar layers already say "processed").
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
- **Finland** (FMI radar composite, reflectivity dBZ, layer `Radar:suomi_dbz_eureffin`), every 5 minutes, a week of history in the
  time dimension (`&time=`, PT5M), coverage lon 15-35 / lat 56.8-71.3 (Finland and surroundings), keyless; capabilities "Fees: NONE,
  AccessConstraints: NONE". Licence: CC BY 4.0 - "The Creative Commons Attribution 4.0 International license (CC BY 4.0) shall be used
  by the Finnish Meteorological Institute's open data service" (https://en.ilmatieteenlaitos.fi/open-data-licence). Credit:
  "Contains data from the Finnish Meteorological Institute (CC BY 4.0)" (wording ours; the licence asks for appropriate credit and a licence link):
  `https://openwms.fmi.fi/geoserver/wms?service=WMS&version=1.3.0&request=GetMap&layers=Radar:suomi_dbz_eureffin&styles=&crs=EPSG:3857&bbox={bbox}&width=256&height=256&format=image/png&transparent=true`
- **Belgium** (RMI/KMI rainfall radar composite, layer `radar:belgian_rainfall_composite`), every 5 minutes, ~13 h of history in the
  time dimension (PT5M), coverage lon -0.93..9.66 / lat 47.42..53.80, keyless; capabilities "Fees: none, AccessConstraints: none".
  Licence (opendata.meteo.be, "Agreement Terms and Conditions"): "All content published on the open data platform of the Royal
  Meteorological Institute of Belgium (RMI) ... may be re-used, distributed or presented publicly, midst the user explicitly refers to
  the RMI as the official source of the raw data. All open data of the RMI that are defined as high-value datasets (HVD) may be re-used
  under the Creative Commons licence conditions CC BY 4.0 provided that the source is acknowledged." (Only the registered-user climate
  gridded data is excluded from public re-use; radar is not that.) Credit: "Source: Royal Meteorological Institute of Belgium (RMI/KMI)":
  `https://opendata.meteo.be/geoserver/wms?service=WMS&version=1.3.0&request=GetMap&layers=radar:belgian_rainfall_composite&styles=&crs=EPSG:3857&bbox={bbox}&width=256&height=256&format=image/png&transparent=true`
- **Not added (18 Oct 2026):** Austria GeoSphere (CC BY 4.0, "© GeoSphere Austria"): INCA/nowcast 15 min, 1 km, only as NetCDF/GeoJSON via dataset.api.hub.geosphere.at, no WMS found, we would have to render it. Denmark DMI (CC BY 4.0, opendataapi.dmi.dk/v1/radardata, composite every 5 min): HDF5 files only, no WMS (the answer was keyless on 18 Oct, older docs say key). Sweden SMHI (opendata-download-radar.smhi.se): h5/png/tif files, no WMS.
- Canada and the Netherlands each answered a real 256 px request with drawn rain and no key (17 Oct 2026 ~14:45 UTC).
- ⚠️ Direct requests send the viewed area and the phone's IP address to NOAA, DWD, ECCC, KNMI and NASA. A Cloudflare proxy would hide that
  (not built; free up to 100,000 requests a day).

- **Austria** (ours, on R2; GeoSphere Austria's INCA nowcast `nowcast-v1-15min-1km`, parameter `rr`, a radar + gauge + model
  blend at 1 km, every 15 min, ~5-20 min behind): manifest `https://maps.notjustweather.com/maps/radar-at/frames.json`
  (`frames` and `keys` oldest first for the last 2 h, `url` = `.../maps/radar-at/{key}/{z}/{x}/{y}.png`, z5-z9, `bounds`
  [9.45, 46.3, 17.25, 49.15], the GFS rain palette and legend, a missing tile = no rain). Drawn by `render_radar_at.py` in
  `.github/workflows/radar-at.yml` every 15 min. Licence CC BY 4.0: "All data that is publicly accessible without
  authentication is licensed under Creative Commons Attribution 4.0" (https://dataset.api.hub.geosphere.at/v1/docs).
  Credit: "GeoSphere Austria". ~40 tiles and ~100 kB per frame; ~120k storage writes a month (~$0.55).
- **Sweden** and **Denmark** (ours, on R2, same manifest shape as Austria): `.../maps/radar-se/frames.json` (SMHI composite
  DBZH, ODIM HDF5, 2 km, stereographic on Bessel) and `.../maps/radar-dk/frames.json` (DMI composite DBZH, 500 m, oblique
  stereographic on WGS84), reflectivity -> rain rate by Marshall-Palmer (Z = 200 R^1.6), isolated clutter specks/streaks
  removed (a rain pixel survives only inside a 3x3 block of rain), the GFS rain palette, frames on the quarter hours, last
  2 h. `.github/workflows/radar-nordic.yml` every 15 min. Licences: SMHI "Creative commons Erkännande 4.0 SE ... även för
  kommersiella ändamål ... ange SMHI som källa och även ange om du har ändrat i licensmaterialet"
  (https://www.smhi.se/data/om-smhis-data/villkor-for-anvandning); DMI "DMI's Open Data are distributed under the Creative
  Commons License CC BY 4.0" (https://www.dmi.dk/friedata/dokumentation/terms-of-use). Credits: "SMHI (CC BY 4.0), processed",
  "DMI (CC BY 4.0), processed". Writes ~250k (SE) + ~66k (DK) a month (~$1.4).
- **Czechia** (ours, on R2, same manifest shape): `.../maps/radar-cz/frames.json` (CHMI/ČHMÚ composite PseudoCAPPI 2 km
  DBZH, ODIM HDF5, 1.56 km spherical Mercator, radars Brdy-Praha and Skalky, from
  https://opendata.chmi.cz/meteorology/weather/radar/composite/pseudocappi2km/hdf5/), Marshall-Palmer, the same clutter
  filter and palette, z5-z9 over Czechia, last 2 h; a step in `.github/workflows/radar-at.yml`. Position checked against
  DWD's radar on 17 Oct 2026 15:00 UTC: 98.5% of the Czech rain pixels are rain in DWD too, best fit within one source pixel.
  Licence CC BY 4.0: "Otevřená data ČHMÚ můžete využívat bezplatně při respektování licence Creative Commons BY 4.0"
  (https://www.chmi.cz/-/jak-mohu-pou%C5%BE%C3%ADvat-otev%C5%99en%C3%A1-data-%C4%8Dhm%C3%BA-), and the national catalogue's
  "Radarová data ČHMÚ" record says CC BY 4.0. Credit: "ČHMÚ (CC BY 4.0), processed". Up to 135 tiles a frame (33-80 on rainy
  frames, ~0.5-0.9 MB); ~90k writes a month typical (~$0.40), 390k if it rained everywhere all month (~$1.75).
- **Norway** (not added, 18 Oct 2026): api.met.no radar 2.0 says "There is no usage restrictions on any of the radar images",
  but it serves only PNG/GIF pictures in a Lambert conformal projection whose parameters and extent are not published, so
  they cannot be placed on the map without guessing.
- **Ireland** and **Poland** (not added, 18 Oct 2026): both publish ODIM HDF5 radar files, but not live. Met Éireann
  (CC BY 4.0, https://opendata2.met.ie/radar/latest/): at 09:40 UTC the newest file was from 23:50 UTC the night before
  (published in a morning batch). IMGW (danepubliczne.imgw.pl, product COMPO_CMAX_250.comp.cmax): at 09:40 UTC the newest
  file was stamped 04:25, and it is column-maximum reflectivity, not rain near the ground.

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
- Outside the US: Copernicus CAMS, section 5b (ours, on R2).

## 5b. Air quality (Europe and the world) and European pollen (Copernicus CAMS, ours, on R2)

- **Manifests:** `https://maps.notjustweather.com/maps/cams/europe/latest.json` and `…/maps/cams/world/latest.json`
  (5 minute cache), same shape as GFS's: `run`, `steps[]` (`fh`, `key`, `valid`), `layers.<name>.url` with `{fh}` = `key`.
  The app draws Europe over the world (Europe tiles are transparent outside its domain, and missing outside it).
- **Europe** (`cams-europe-air-quality-forecasts`, the ensemble of the regional models, 0.1°, domain 25° W – 45° E,
  30 – 72° N): the 00 UTC run, every hour 0 … 48 h. Layers `aqi` and `pollen`, z0–4 (the app draws z4 larger beyond). Plus `layers.pollen.grids`: one grey
  PNG per species and hour on the model's own grid (alder, birch, grass, mugwort, olive, ragweed), byte → pollen/m³ with
  the manifest's `decode` line, for the numbers and the species at a place.
- **World** (`cams-global-atmospheric-composition-forecasts`, 0.4°): the 00 and 12 UTC runs, every 3 hours 0 … 48 h.
  Layer `aqi`, z0–3. PM2.5 and PM10 at the surface; O3 and NO2 on the lowest model level (137), kg/kg × air density from
  surface pressure and 2 m temperature.
- **`aqi`** = the European Air Quality Index with the EEA's revised bands (hourly concentrations, µg/m³; ETC HE Report
  2024/17, executive summary table, https://www.eionet.europa.eu/etcs/etc-he/products/etc-he-products/etc-he-reports/etc-he-report-2024-17-eeas-revision-of-the-european-air-quality-index-bands):
  PM2.5 0-5 · 6-15 · 16-50 · 51-90 · 91-140 · >140; PM10 0-15 · 16-45 · 46-120 · 121-195 · 196-270 · >270;
  O3 0-60 · 61-100 · 101-120 · 121-160 · 161-180 · >180; NO2 0-10 · 11-25 · 26-60 · 61-100 · 101-150 · >150 =
  Good · Fair · Moderate · Poor · Very poor · Extremely poor. Each cell takes the worst pollutant. The tile value is the band
  (0 … 5) plus how far into the band the worst pollutant is; the tiles draw it STEPPED (`legendMode: "step"`), each band one
  flat EEA colour: Good #50F0E6, Fair #50CCAA, Moderate #F0E641, Poor #FF5050, Very poor #960032, Extremely poor #7D2181
  (the alpha byte is the app's: lighter for clean air).
  ⚠️ Over stormy oceans the global model's PM10 includes sea salt, so Fair to Moderate there is the model, not a fault.
- **`pollen`** = the highest level among the six species. Level 0 = the pollen-season threshold, 1 = the peak threshold,
  log scale between: alder, birch, olive, mugwort 10 and 100 pollen/m³; grass, ragweed 3 and 50 (EAACI, Pfaar et al. 2017,
  https://doi.org/10.1111/all.13092, and 2020, https://doi.org/10.1111/all.14111, as cited by the EEA's Climate-ADAPT page
  "CAMS ground level pollen forecast"). Below the season threshold is transparent, and an empty tile is not written.
  Colours are continuous between the legend's stops (`legendMode: "linear"`).
- **Data for the app to draw itself** (18 Oct 2026): `layers.aqi.grid` and `layers.pollen.grid`, one grey PNG per hour on
  the model's own grid (Europe 0.1°, world 0.4°; `width`, `height`, `lat0`, `lon0`, `dlat`, `dlon` in the manifest, row 0
  north), 255 = no data. aqi: value = byte / 40, band = floor(byte / 40). pollen: level = byte / 80 (0 = at or below the
  season threshold). The per-species pollen grids (`layers.pollen.grids`) stay as they are.
- **Licence:** CAMS data licence (CC-BY 4.0 based), accepted on the ADS account 17 Oct 2026. The manifests' `attribution`
  must be shown on the map, visibly: "Contains modified Copernicus Atmosphere Monitoring Service information <year>.
  Neither the European Commission nor ECMWF is responsible for any use that may be made of the information it contains."
- **Refresh:** `cams.yml`, started by the Cloudflare cron at 10:30, 13:30 and 22:30 UTC; each region checks its own
  manifest and asks the ADS for the run's last hour before drawing ("not yet" is green; still missing 16 h after its hour is
  red). Palettes are the app's exact stops (left, 18 Oct 2026). Public repo: $0 GitHub minutes. R2 writes ≈ 4 k a day for
  Europe in pollen season (36 tiles per layer and hour at z0–4, plus the grids) and ≈ 3 k for the world (≈ 0.2 M a month,
  ≈ $1).

## 6. Points

- **Earthquakes:** USGS GeoJSON, public domain, updated every minute, credit "USGS":
  `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson` (also `4.5_week`, `significant_month`).
- **Weather alerts:** WeatherKit gives alerts per place, not per map area, so they are drawn at the person's own places.
  Credit as on the warnings screen (Apple Weather and the issuing agency).
