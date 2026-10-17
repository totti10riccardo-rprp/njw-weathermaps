#!/usr/bin/env python3
"""render_cams.py — air quality (Europe and the world) and European pollen from Copernicus CAMS, drawn into map tiles on R2.

Free source, CC-BY: the Copernicus Atmosphere Monitoring Service, read through the Atmosphere Data Store (ADS, key in
~/.cdsapirc). The credit the licence asks for travels in each manifest's `attribution` and must be shown on the map.

  europe   cams-europe-air-quality-forecasts, ensemble, surface, 0.1°, the 00 UTC run, every hour 0 … 48 h
             aqi     the European Air Quality Index (EEA): the worst of PM2.5, PM10, O3 and NO2 per cell
             pollen  the highest pollen level of alder, birch, grass, mugwort, olive and ragweed
             + one value grid per species and hour (pollen/m³, see POLLEN_GRID), for the app's numbers and the species
  world    cams-global-atmospheric-composition-forecasts, 0.4°, the 00 and 12 UTC runs, every 3 hours 0 … 48 h
             aqi     the same index from the global model (PM at the surface; O3 and NO2 on the lowest model level,
                     converted from kg/kg with the air density from surface pressure and 2 m temperature)

The index (the EEA's revised bands, hourly concentrations, µg/m³; ETC HE Report 2024/17, executive summary table):
  PM2.5  0-5 6-15 16-50 51-90 91-140 >140 · PM10 0-15 16-45 46-120 121-195 196-270 >270
  O3     0-60 61-100 101-120 121-160 161-180 >180 · NO2 0-10 11-25 26-60 61-100 101-150 >150
  Good · Fair · Moderate · Poor · Very poor · Extremely poor = 0 … 5. The tiles draw a continuous value (the band plus how
  far into it the worst pollutant is), so band edges sit exactly at the EEA limits and the map is smooth in between.
Pollen levels (EAACI, Pfaar et al. 2017; 2020, as the EEA's Climate-ADAPT page for the CAMS pollen forecast cites them):
  alder, birch, olive, mugwort: season from 10 pollen/m³, peak from 100 · grass, ragweed: season from 3, peak from 50.
  Level 0 = the season threshold, 1 = the peak threshold, log scale between (so 2 = as far again above the peak).

Tiles are 256 px Web Mercator {z}/{x}/{y}, bilinear from the model grid. Europe z0–5, world z0–3. Outside the European
domain a Europe tile is transparent, and an all-transparent tile is not written (the app draws Europe over the world).

Layout on R2 (bucket njw-maps, public at https://maps.notjustweather.com):
  maps/cams/<region>/<YYYYMMDDHH>/<layer>/<fh>/<z>/<x>/<y>.png       fh = lead hour, three digits
  maps/cams/europe/<YYYYMMDDHH>/pollengrid/<species>/<fh>.png
  maps/cams/<region>/latest.json        the manifest, written LAST; older runs than the previous one are then deleted
  maps/cams/<region>/latest-test.json   instead, when --steps picks some lead hours (a test never replaces the live one)

usage:
  render_cams.py --region europe|world [--run YYYYMMDDHH] [--out DIR] [--upload] [--steps 0,1,…]
  render_cams.py --region R --check   exit 0 and print the run when a newer run is ready to draw, 3 when not (yet)
Upload needs R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY (never printed).
"""
import argparse, datetime as dt, io, json, math, os, sys, tempfile, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

np = Image = eccodes = None   # loaded in main() after --check's quick answer

R2_BUCKET = "njw-maps"
PUBLIC = "https://maps.notjustweather.com"
REGIONS = {
    "europe": {"dataset": "cams-europe-air-quality-forecasts", "runs": (0,), "ready_after_h": 10, "maxz": 5,
               "steps": list(range(0, 49)), "model": "CAMS European ensemble 0.1°"},
    "world": {"dataset": "cams-global-atmospheric-composition-forecasts", "runs": (0, 12), "ready_after_h": 10, "maxz": 3,
              "steps": list(range(0, 49, 3)), "model": "CAMS global 0.4°"},
}
# A run not ready this long after its hour is a fault (red), not "not yet" (green).
LATE_H = 16

# EEA EAQI upper limits per band, µg/m³ (the 6th band is everything above the last)
EAQI = {"pm25": [5, 15, 50, 90, 140], "pm10": [15, 45, 120, 195, 270], "o3": [60, 100, 120, 160, 180], "no2": [10, 25, 60, 100, 150]}
EAQI_NAMES = ["Good", "Fair", "Moderate", "Poor", "Very poor", "Extremely poor"]
POLLEN = {"alder": (10, 100), "birch": (10, 100), "grass": (3, 50), "mugwort": (10, 100), "olive": (10, 100), "ragweed": (3, 50)}

# ADS variable names, one request each (the identity of a field is then known by construction)
VARS = {
    "europe": {"pm25": "particulate_matter_2.5um", "pm10": "particulate_matter_10um", "o3": "ozone", "no2": "nitrogen_dioxide",
               **{s: f"{s}_pollen" for s in POLLEN}},
    "world": {"pm25": "particulate_matter_2.5um", "pm10": "particulate_matter_10um", "sp": "surface_pressure",
              "t2m": "2m_temperature", "o3": "ozone", "no2": "nitrogen_dioxide"},
}
MODEL_LEVEL = {"o3", "no2"}   # world: on model level 137 (the lowest)

# Colour ramps (value, (r, g, b, a)), linear between stops. PROVISIONAL until the app sends its exact stops; the
# manifest's legends are these stops, so the app's linear blend reproduces the tiles either way.
# aqi: 0 … 6 (the band index plus the position inside it); pollen: the level (0 = season, 1 = peak).
RAMPS = {
    "aqi": [(0, (80, 200, 230, 150)), (1, (80, 200, 170, 170)), (2, (240, 225, 70, 190)), (3, (245, 150, 50, 200)),
            (4, (220, 50, 60, 210)), (5, (150, 20, 80, 220)), (6, (110, 30, 120, 230))],
    "pollen": [(-0.3, (230, 230, 120, 0)), (0, (230, 225, 110, 150)), (0.5, (245, 175, 60, 190)), (1, (230, 90, 50, 210)),
               (2, (160, 30, 110, 225))],
}
FLOOR = {"pollen": -0.3}
# pollen value grid: grey PNG on the model's own 0.1° grid, byte = round(log10(pollen/m³ + 1) × 50), 0 … 255
POLLEN_GRID_SCALE = 50


def attribution(year):
    return (f"Contains modified Copernicus Atmosphere Monitoring Service information {year}. Neither the European "
            "Commission nor ECMWF is responsible for any use that may be made of the information it contains.")


def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "njw-weathermaps"}), timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404: raise
            time.sleep(2 * (i + 1))
        except urllib.error.URLError:
            time.sleep(2 * (i + 1))
    raise urllib.error.URLError(f"{url}: failed {tries} times")


def expected_run(region, now=None):
    """The newest run whose usual release time has passed (YYYYMMDDHH)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg = REGIONS[region]
    for back in range(0, 3):
        day = (now - dt.timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0)
        for h in sorted(cfg["runs"], reverse=True):
            t = day + dt.timedelta(hours=h)
            if now >= t + dt.timedelta(hours=cfg["ready_after_h"]):
                return t.strftime("%Y%m%d%H")
    raise SystemExit("no run found")


def published(region):
    try:
        cur = json.loads(get(f"{PUBLIC}/maps/cams/{region}/latest.json?t={int(time.time())}", tries=2))
        return cur.get("run", "").replace("-", "").replace("T", "")[:10] or None
    except (urllib.error.URLError, ValueError):
        return None


def request(region, key, run, steps, area=None):
    cfg, var = REGIONS[region], VARS[region][key]
    r = {"variable": [var], "date": [f"{run[:4]}-{run[4:6]}-{run[6:8]}/{run[:4]}-{run[4:6]}-{run[6:8]}"],
         "time": [f"{run[8:10]}:00"], "leadtime_hour": [str(s) for s in steps], "type": ["forecast"], "data_format": "grib"}
    if region == "europe":
        r.update({"model": ["ensemble"], "level": ["0"]})
    elif key in MODEL_LEVEL:
        r["model_level"] = ["137"]
    if area:
        r["area"] = area
    return cfg["dataset"], r


def ready(region, run):
    """A tiny request for the run's last lead hour: the ADS answers it only once the run is in."""
    import cdsapi
    ds, req = request(region, "pm25", run, [REGIONS[region]["steps"][-1]], area=[48, 16, 47, 17])
    try:
        with tempfile.TemporaryDirectory() as d:
            cdsapi.Client(quiet=True, progress=False).retrieve(ds, req).download(os.path.join(d, "probe.grib"))
        return True, ""
    except Exception as e:  # the ADS's own reason, never the key
        return False, str(e)[:300]


def download(region, key, run, steps, folder):
    import cdsapi
    ds, req = request(region, key, run, steps)
    p = os.path.join(folder, f"{key}.grib")
    for i in range(3):
        try:
            cdsapi.Client(quiet=True, progress=False).retrieve(ds, req).download(p)
            return key, p
        except Exception as e:
            print(f"  {key}: attempt {i + 1} failed: {str(e)[:200]}", flush=True)
            time.sleep(30 * (i + 1))
    raise SystemExit(f"{key}: download failed")


def read_grib(path):
    """{lead hour: 2-D array (rows north → south)}, plus the grid (lat of row 0, lon of column 0, steps, shape) and units."""
    out, grid, units = {}, None, None
    with open(path, "rb") as f:
        while True:
            h = eccodes.codes_grib_new_from_file(f)
            if h is None: break
            try:
                ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
                lat1 = eccodes.codes_get(h, "latitudeOfFirstGridPointInDegrees")
                lat2 = eccodes.codes_get(h, "latitudeOfLastGridPointInDegrees")
                lon1 = eccodes.codes_get(h, "longitudeOfFirstGridPointInDegrees")
                lon2 = eccodes.codes_get(h, "longitudeOfLastGridPointInDegrees")
                step = int(eccodes.codes_get(h, "endStep"))
                units = eccodes.codes_get(h, "units")
                v = eccodes.codes_get_values(h).reshape(nj, ni).astype(np.float32)
                if lat1 < lat2:              # stored south → north: flip to north → south
                    v = v[::-1]; lat1, lat2 = lat2, lat1
                g = {"lat0": lat1, "lat1": lat2, "lon0": lon1 if lon1 <= 180 else lon1 - 360, "ni": ni, "nj": nj,
                     "dlat": (lat1 - lat2) / (nj - 1), "dlon": ((lon2 - lon1) % 360) / (ni - 1)}
                g["global"] = abs(g["dlon"] * ni - 360) < 1e-3
                grid = grid or g
                out[step] = v
            finally:
                eccodes.codes_release(h)
    return out, grid, units


def to_ugm3(a, units):
    """Mass concentration to µg/m³ from whatever the GRIB says it is."""
    u = (units or "").replace(" ", "").lower()
    if u in ("kgm**-3", "kgm-3"): return a * 1e9
    if u in ("µgm**-3", "ugm**-3", "µgm-3", "ugm-3"): return a
    # the European ensemble's GRIB says "unknown" (17 Oct 2026); its values are kg/m³ (PM2.5 median ~4e-9 = 4 µg/m³).
    # Anything bigger than 1e-3 cannot be kg/m³ at the surface, so a change of unit stops the run instead of drawing it.
    if u in ("unknown", "~") and np.nanmax(a) < 1e-3: return a * 1e9
    raise SystemExit(f"unexpected units {units!r} (max {np.nanmax(a):.3g})")


def sub_index(v, limits):
    """Continuous EAQI sub-index: band i plus the fraction of the way through it (the 6th band runs to 2 × its floor)."""
    edges = np.array([0.0] + limits + [limits[-1] * 2.0], np.float32)
    i = np.clip(np.searchsorted(edges, v, side="right") - 1, 0, len(edges) - 2)
    lo, hi = edges[i], edges[i + 1]
    return np.clip(i + (v - lo) / (hi - lo), 0, 6).astype(np.float32)


def pollen_level(c, season, peak):
    return (np.log10(np.maximum(c, 1e-3) / season) / math.log10(peak / season)).astype(np.float32)


def sample(a, g, z, x, y):
    """Bilinear sample of a regular lat/lon grid at a tile's 256×256 pixel centres; NaN outside a regional grid."""
    n = 256 * (1 << z)
    px = (x * 256 + np.arange(256) + 0.5) / n
    py = (y * 256 + np.arange(256) + 0.5) / n
    lon = px * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * py))))
    fi = (g["lat0"] - lat) / g["dlat"]
    if g["global"]:
        fj = np.mod(lon - g["lon0"], 360.0) / g["dlon"]
    else:
        fj = (lon - g["lon0"]) / g["dlon"]
    i0 = np.clip(np.floor(fi).astype(int), 0, a.shape[0] - 2); ti = np.clip(fi - i0, 0, 1)[:, None]
    if g["global"]:
        j0 = np.floor(fj).astype(int) % a.shape[1]; j1 = (j0 + 1) % a.shape[1]; tj = (fj - np.floor(fj))[None, :]
    else:
        j0 = np.clip(np.floor(fj).astype(int), 0, a.shape[1] - 2); j1 = j0 + 1; tj = np.clip(fj - j0, 0, 1)[None, :]
    out = (a[np.ix_(i0, j0)] * (1 - tj) + a[np.ix_(i0, j1)] * tj) * (1 - ti) + \
          (a[np.ix_(i0 + 1, j0)] * (1 - tj) + a[np.ix_(i0 + 1, j1)] * tj) * ti
    if not g["global"]:
        inside = ((fi >= 0) & (fi <= a.shape[0] - 1))[:, None] & ((fj >= 0) & (fj <= a.shape[1] - 1))[None, :]
        out = np.where(inside, out, np.nan)
    return out


def tile_range(g, z):
    """The tiles at zoom z that touch the grid (all of them for a global grid)."""
    n = 1 << z
    if g["global"]:
        return [(x, y) for x in range(n) for y in range(n)]
    def tx(lon): return min(n - 1, max(0, int((lon + 180) / 360 * n)))
    def ty(lat):
        lat = max(min(lat, 85.0511), -85.0511)
        return min(n - 1, max(0, int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)))
    lon1 = g["lon0"] + g["dlon"] * (g["ni"] - 1)
    return [(x, y) for x in range(tx(g["lon0"]), tx(lon1) + 1) for y in range(ty(g["lat0"]), ty(g["lat1"]) + 1)]


def lut(layer, lo, hi, n=2048):
    stops = RAMPS[layer]
    xs = np.linspace(lo, hi, n)
    vs = np.array([s[0] for s in stops], float); cs = np.array([s[1] for s in stops], float)
    return xs, np.stack([np.interp(xs, vs, cs[:, k]) for k in range(4)], axis=1).astype(np.uint8)


def tile_png(layer, grid, g, z, x, y, table):
    xs, colours = table
    v = sample(grid, g, z, x, y)
    nan = np.isnan(v)
    floor = FLOOR.get(layer)
    if nan.all() or (floor is not None and not (v[~nan] > floor).any()):
        return None
    k = np.clip(((np.nan_to_num(v, nan=xs[0]) - xs[0]) / (xs[-1] - xs[0]) * (len(xs) - 1)).round().astype(int), 0, len(xs) - 1)
    rgba = colours[k]
    rgba[nan] = 0
    if floor is not None:
        rgba[np.nan_to_num(v, nan=floor) <= floor] = 0
    buf = io.BytesIO(); Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def grey_png(a):
    buf = io.BytesIO(); Image.fromarray(a, "L").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def r2():
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"], aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto",
                        config=Config(retries={"max_attempts": 6, "mode": "adaptive"}, max_pool_connections=32))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True, choices=REGIONS); ap.add_argument("--run")
    ap.add_argument("--out"); ap.add_argument("--upload", action="store_true"); ap.add_argument("--steps")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    region, cfg = a.region, REGIONS[a.region]
    run = a.run or expected_run(region)
    prefix = f"maps/cams/{region}"
    if a.check:
        if published(region) == run:
            print(f"{region}: run {run} is already published"); sys.exit(3)
        ok, why = ready(region, run)
        if ok:
            print(run); sys.exit(0)
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)).total_seconds() / 3600
        print(f"{region}: run {run} not ready ({age:.1f} h after its hour): {why}")
        sys.exit(1 if age > LATE_H else 3)
    steps = [int(s) for s in a.steps.split(",")] if a.steps else cfg["steps"]
    global np, Image, eccodes
    import numpy as np
    from PIL import Image
    import eccodes
    run_t = dt.datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    print(f"CAMS {region} run {run} · steps {steps[0]}…{steps[-1]} ({len(steps)})", flush=True)
    t0 = time.time()
    folder = tempfile.mkdtemp(prefix=f"cams-{region}-")
    with ThreadPoolExecutor(5) as pool:
        files = dict(pool.map(lambda k: download(region, k, run, steps, folder), VARS[region]))
    data, grid = {}, None
    for k, p in files.items():
        fields, g, units = read_grib(p)
        missing = [s for s in steps if s not in fields]
        if missing:
            raise SystemExit(f"{k}: lead hours missing from the download: {missing[:8]}")
        grid = grid or g
        if (g["ni"], g["nj"]) != (grid["ni"], grid["nj"]):
            raise SystemExit(f"{k}: grid {g['ni']}x{g['nj']} differs from {grid['ni']}x{grid['nj']}")
        data[k] = (fields, units)
        sample_v = fields[steps[0]]
        print(f"  {k}: {os.path.getsize(p) / 1e6:.1f} MB · units {units!r} · grid {g['ni']}x{g['nj']} from "
              f"{g['lat0']:.2f},{g['lon0']:.2f} step {g['dlat']:.3f}/{g['dlon']:.3f} · f{steps[0]:03d} "
              f"min {np.nanmin(sample_v):.3g} median {np.nanmedian(sample_v):.3g} max {np.nanmax(sample_v):.3g}", flush=True)
        if k in POLLEN and max(float(np.nanmax(v)) for v in fields.values()) > 1e6:
            raise SystemExit(f"{k}: pollen counts above 1e6 per m³ ({units!r}): not pollen/m³, stopping")
    print(f"  downloaded in {time.time() - t0:.0f} s", flush=True)

    def ugm3(key, fh):
        f, units = data[key]
        v = f[fh]
        if region == "world" and key in MODEL_LEVEL:     # kg/kg × air density (kg/m³) → kg/m³ → µg/m³
            rho = data["sp"][0][fh] / (287.05 * data["t2m"][0][fh])
            return v * rho * 1e9
        return to_ugm3(v, units)

    s3 = r2() if a.upload else None
    layers = ["aqi"] + (["pollen"] if region == "europe" else [])
    tables = {"aqi": lut("aqi", 0, 6), "pollen": lut("pollen", -0.3, 2)}
    written = empty = bytes_out = grid_bytes = 0
    peaks = {}

    def put(key, body, ctype="image/png"):
        if s3:
            s3.put_object(Bucket=R2_BUCKET, Key=key, Body=body, ContentType=ctype, CacheControl="public, max-age=86400, immutable")
        if a.out:
            p = os.path.join(a.out, key); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(body)

    with ThreadPoolExecutor(16) as pool:
        for fh in steps:
            grids = {"aqi": np.maximum.reduce([sub_index(ugm3(p, fh), lim) for p, lim in EAQI.items()])}
            if region == "europe":
                conc = {s: np.maximum(data[s][0][fh], 0) for s in POLLEN}   # pollen/m³ as delivered
                grids["pollen"] = np.maximum.reduce([pollen_level(conc[s], *POLLEN[s]) for s in POLLEN])
            peaks[fh] = {l: float(np.nanmax(v)) for l, v in grids.items()}
            jobs = []
            for layer in layers:
                for z in range(cfg["maxz"] + 1):
                    for x, y in tile_range(grid, z):
                        png = tile_png(layer, grids[layer], grid, z, x, y, tables[layer])
                        if png is None:
                            empty += 1; continue
                        written += 1; bytes_out += len(png)
                        jobs.append(pool.submit(put, f"{prefix}/{run}/{layer}/{fh:03d}/{z}/{x}/{y}.png", png))
            if region == "europe":
                for s in POLLEN:
                    b = np.clip(np.rint(np.log10(conc[s] + 1) * POLLEN_GRID_SCALE), 0, 255).astype(np.uint8)
                    png = grey_png(b); grid_bytes += len(png)
                    jobs.append(pool.submit(put, f"{prefix}/{run}/pollengrid/{s}/{fh:03d}.png", png))
            for j in jobs: j.result()
            print(f"  f{fh:03d} · {written} tiles · {empty} empty · {bytes_out / 1e6:.1f} MB · grids {grid_bytes / 1e6:.1f} MB · "
                  + " ".join(f"{l} max {v:.2f}" for l, v in peaks[fh].items()) + f" · {time.time() - t0:.0f} s", flush=True)

    def legend(l): return [[v, "#%02x%02x%02x%02x" % c] for v, c in RAMPS[l]]
    manifest = {
        "model": cfg["model"], "source": cfg["dataset"], "attribution": attribution(run_t.year),
        "run": run_t.isoformat().replace("+00:00", "Z"),
        "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "steps": [{"fh": fh, "key": f"{fh:03d}", "valid": (run_t + dt.timedelta(hours=fh)).isoformat().replace("+00:00", "Z")}
                  for fh in steps],
        "layers": {
            "aqi": {"url": f"{PUBLIC}/{prefix}/{run}/aqi/{{fh}}/{{z}}/{{x}}/{{y}}.png", "minZoom": 0, "maxZoom": cfg["maxz"],
                    "units": "European Air Quality Index (0 = Good … 6)", "legend": legend("aqi"),
                    "bands": [{"from": i, "to": i + 1, "name": n} for i, n in enumerate(EAQI_NAMES)],
                    "limits_ugm3": EAQI, "standard": "EEA European Air Quality Index, revised bands (ETC HE Report 2024/17), hourly",
                    **({"missingTileMeans": "outside the European domain"} if region == "europe" else {})},
        },
        "tiles": written,
    }
    if region == "europe":
        manifest["layers"]["pollen"] = {
            "url": f"{PUBLIC}/{prefix}/{run}/pollen/{{fh}}/{{z}}/{{x}}/{{y}}.png", "minZoom": 0, "maxZoom": cfg["maxz"],
            "units": "pollen level of the highest species: 0 = season threshold, 1 = peak threshold (log scale)",
            "legend": legend("pollen"), "missingTileMeans": "below the pollen-season threshold, or outside the European domain",
            "thresholds": {s: {"season": se, "peak": pk} for s, (se, pk) in POLLEN.items()},
            "standard": "EAACI pollen season and peak thresholds (Pfaar et al. 2017; 2020), pollen/m³",
            "grids": {"url": f"{PUBLIC}/{prefix}/{run}/pollengrid/{{species}}/{{fh}}.png", "species": list(POLLEN),
                      "width": grid["ni"], "height": grid["nj"], "lat0": round(grid["lat0"], 4), "lon0": round(grid["lon0"], 4),
                      "dlat": round(grid["dlat"], 4), "dlon": round(grid["dlon"], 4),
                      "decode": f"pollen/m³ = 10 ^ (byte / {POLLEN_GRID_SCALE}) - 1; row 0 = lat0 (north), column 0 = lon0"},
        }
    body = json.dumps(manifest, indent=1, ensure_ascii=False).encode()
    if a.out:
        os.makedirs(os.path.join(a.out, prefix), exist_ok=True)
        open(os.path.join(a.out, prefix, "latest.json"), "wb").write(body)
    if s3 and a.steps:
        # a test with some lead hours only: its own manifest, so the live one (and the "already published" check) stay
        s3.put_object(Bucket=R2_BUCKET, Key=f"{prefix}/latest-test.json", Body=body, ContentType="application/json",
                      CacheControl="no-store")
    elif s3:
        s3.put_object(Bucket=R2_BUCKET, Key=f"{prefix}/latest.json", Body=body, ContentType="application/json",
                      CacheControl="public, max-age=300")
        runs = sorted({p["Prefix"].split("/")[3] for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=R2_BUCKET, Prefix=f"{prefix}/", Delimiter="/") for p in page.get("CommonPrefixes", [])})
        old = [r for r in runs if r.isdigit() and len(r) == 10 and r < run][:-1]
        for r in old:
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=R2_BUCKET, Prefix=f"{prefix}/{r}/"):
                objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                for i in range(0, len(objs), 1000):
                    s3.delete_objects(Bucket=R2_BUCKET, Delete={"Objects": objs[i:i + 1000], "Quiet": True})
            print(f"  deleted old run {r}")
    print(f"done: {region} run {run} · {written} tiles · {bytes_out / 1e6:.1f} MB · pollen grids {grid_bytes / 1e6:.1f} MB · "
          f"{time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
