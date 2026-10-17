# NOT JUST WEATHER: weather map tiles

Draws global forecast map tiles from NOAA's GFS model (public domain) after each model run and publishes them to
Cloudflare R2, served at https://maps.notjustweather.com. The NOT JUST WEATHER iPhone app draws them over Apple Maps.

- `render_gfs.py`: reads GFS 0.25° fields from NOAA's open-data bucket on AWS (byte ranges, no account) and writes
  256 px Web Mercator PNG tiles for rain (coloured rain, snow or mix by GFS's precipitation type), 10 m wind and 2 m
  temperature, a compact wind-field PNG per step, and `maps/gfs/latest.json`, the manifest the app reads first.
- `SOURCES.md`: every map source the app uses, with its URL, refresh and required credit.
- `.github/workflows/maps.yml`: the job. `cron-worker/`: the Cloudflare cron that starts it at the forecast release times.

Data: NOAA GFS (public domain). Contributions are not accepted; workflows run only from this repository's own branches.
