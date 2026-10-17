"""cams_test.py: a first Copernicus CAMS download test (two tiny requests). Reads ~/.cdsapirc; never prints the key.
Europe: PM2.5 and birch pollen at the surface; world: PM2.5. Yesterday's 00 UTC run, lead hour 0, a small box over Austria."""
import cdsapi, datetime as dt, os

day = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
c = cdsapi.Client(quiet=True, progress=False)
jobs = {
    "europe": ("cams-europe-air-quality-forecasts", {
        "variable": ["particulate_matter_2.5um", "birch_pollen"], "model": ["ensemble"], "level": ["0"],
        "date": [f"{day}/{day}"], "type": ["forecast"], "time": ["00:00"], "leadtime_hour": ["0"],
        "data_format": "grib", "area": [50, 10, 45, 17]}),
    "global": ("cams-global-atmospheric-composition-forecasts", {
        "variable": ["particulate_matter_2.5um"], "date": [f"{day}/{day}"], "time": ["00:00"], "leadtime_hour": ["0"],
        "type": ["forecast"], "data_format": "grib", "area": [50, 10, 45, 17]}),
}
failed = 0
for name, (ds, req) in jobs.items():
    try:
        p = c.retrieve(ds, req).download(f"{name}.grib")
        print(f"{name}: OK, {os.path.getsize(p)} bytes ({ds}, {day})")
    except Exception as e:
        failed += 1
        print(f"{name}: FAILED ({ds}): {str(e)[:800]}")
raise SystemExit(1 if failed else 0)
