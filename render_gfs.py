#!/usr/bin/env python3
"""weathermaps/render_gfs.py — the forecast map layers (rain, wind, temperature) from NOAA GFS, drawn into map tiles on R2.

Free sources only. GFS is NOAA's global model, public domain, read from NOAA's
open-data bucket on AWS (noaa-gfs-bdp-pds, no account, no key). Each run is read by byte range (four fields per step, from
the .idx), so a run costs ~60 MB of download, not the full 500 MB files.

  rain   PRATE:surface (instantaneous), mm/h; under 0.1 mm/h is transparent and an all-transparent tile is not written
  wind   UGRD/VGRD:10 m above ground, speed in m/s
  temp   TMP:2 m above ground, degrees C

Tiles are 256 px Web Mercator (the {z}/{x}/{y} scheme MapKit's MKTileOverlay reads), bilinear from the 0.25 degree grid.
Temperature and wind are smooth fields and stop at zoom 3 (the app over-zooms); rain stops at zoom 4.
Beside the tiles, data for the app to draw itself: the wind FIELD (1°, u/v) and the temperature and rain GRIDS (0.5°).

Layout on R2 (bucket njw-maps, its own: the GitHub key can write only there; public at https://maps.notjustweather.com):
  maps/gfs/<YYYYMMDDHH>/<layer>/<fh>/<z>/<x>/<y>.png     fh = forecast hour 001 … 048, every hour
  maps/gfs/<YYYYMMDDHH>/windfield/<fh>.png, grid/<temp|rain>/<fh>.png   (see FIELD and GRID below)
  maps/gfs/latest.json                                   the manifest, written LAST, so it never names a run half uploaded
Older runs than the previous one are deleted after the manifest moves (the pipeline's own output only).

usage:
  render_gfs.py [--run YYYYMMDDHH] [--out DIR] [--upload] [--steps 3,6,…] [--layers rain,wind,temp]
  render_gfs.py --check      exit 0 and print the run when a newer complete run waits to be drawn, exit 3 when not
Upload needs R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY in the environment (never printed).
"""
import argparse, datetime as dt, io, json, math, os, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

# numpy, Pillow and eccodes load in main() after --check, so the check needs nothing installed (the scheduler runs it
# before pip, which keeps a fire with nothing new to about a minute of GitHub's free time)
np = Image = eccodes = None

BUCKET_URL = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
R2_BUCKET = "njw-maps"
PREFIX = "maps/gfs"
PUBLIC = "https://maps.notjustweather.com"
STEPS = list(range(1, 49))   # hourly to 48 h (18 Oct 2026; was 3-hourly while the job lived in a private repo's free minutes)
MAXZ = {"rain": 4, "wind": 3, "temp": 3}
FIELDS = {
    "temp": [":TMP:2 m above ground:"],
    "wind": [":UGRD:10 m above ground:", ":VGRD:10 m above ground:"],
    "rain": [":PRATE:surface:"],
}

# ── Colour ramps: (value, (r, g, b, a)), linear between stops, clamped at the ends ─────────────────────────────────────
# The palettes (18 Oct 2026), exact stops shared with the app: smooth, slightly transparent (the app draws the tiles at
# 78 % under MapKit's borders and names), no red next to green. The manifest's legends are these stops, so the app's linear
# blend reproduces the tiles. Wind in m/s (= 0, 20, 39, 40, 62, 89, 120 km/h). Snow and mix use the rain scale (mm/h of water).
RAMPS = {
    "temp": [(-40, (75, 30, 120, 200)), (-20, (60, 90, 210, 200)), (0, (190, 230, 250, 200)), (10, (60, 180, 170, 200)), (20, (245, 220, 70, 200)), (30, (245, 140, 50, 200)), (40, (220, 40, 50, 200)), (45, (180, 20, 60, 200))],
    "wind": [(0, (100, 165, 235, 176)), (5.56, (50, 180, 180, 192)), (10.83, (200, 220, 110, 196)), (11.11, (240, 210, 60, 200)), (17.22, (245, 150, 45, 204)), (24.72, (230, 60, 50, 212)), (33.33, (200, 40, 140, 220))],
    "rain": [(0.1, (159, 211, 255, 0)), (0.25, (159, 211, 255, 128)), (0.5, (140, 200, 255, 184)), (2.5, (60, 140, 240, 200)), (5, (79, 95, 220, 208)), (10, (122, 63, 208, 212)), (12, (245, 210, 60, 216)), (25, (245, 160, 50, 220)), (50, (230, 60, 50, 224)), (80, (180, 20, 140, 230))],
    "snow": [(0.1, (255, 255, 255, 0)), (0.25, (255, 255, 255, 144)), (1, (244, 242, 255, 192)), (5, (217, 204, 245, 208)), (20, (185, 163, 236, 220))],
    "mix": [(0.1, (255, 180, 210, 0)), (0.25, (255, 180, 210, 144)), (2.5, (247, 140, 200, 200)), (10, (230, 90, 180, 212)), (50, (200, 50, 158, 224))],
}
FLOOR = {"rain": 0.1}
# GFS's categorical precipitation type (1 = yes per cell, instantaneous): read with the rain, so snow and mix (sleet,
# freezing rain, ice pellets) get their own colours on the same intensity scale. Several can be 1 at once.
PTYPE = [":CRAIN:surface:", ":CSNOW:surface:", ":CFRZR:surface:", ":CICEP:surface:"]


def lut(layer, lo, hi, n=1024):
    """A lookup table from value to RGBA across [lo, hi]."""
    stops = RAMPS[layer]
    xs = np.linspace(lo, hi, n)
    vs = np.array([s[0] for s in stops], float)
    cs = np.array([s[1] for s in stops], float)
    out = np.stack([np.interp(xs, vs, cs[:, k]) for k in range(4)], axis=1)
    return xs, out.astype(np.uint8)


def get(url, rng=None, tries=4):
    for i in range(tries):
        try:
            # a named agent: Cloudflare refuses Python's default one on our own host (the manifest read)
            req = urllib.request.Request(url, headers={"User-Agent": "NotJustWeather-maps/1 (weathermaps/render_gfs.py)",
                                                       **({"Range": f"bytes={rng[0]}-{rng[1]}"} if rng else {})})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError) as e:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def run_exists(run):
    d, h = run[:8], run[8:]
    try:
        get(f"{BUCKET_URL}/gfs.{d}/{h}/atmos/gfs.t{h}z.pgrb2.0p25.f{max(STEPS):03d}.idx")
        return True
    except urllib.error.HTTPError:
        return False


def latest_run(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    t = now.replace(minute=0, second=0, microsecond=0, hour=now.hour - now.hour % 6)
    for _ in range(8):
        run = t.strftime("%Y%m%d%H")
        if run_exists(run):
            return run
        t -= dt.timedelta(hours=6)
    raise SystemExit("no complete GFS run in the last 48 hours")


def fetch_fields(run, fh, keys):
    """Each requested field as a (721, 1440) array, lat 90 → -90, lon 0 → 359.75."""
    d, h = run[:8], run[8:]
    base = f"{BUCKET_URL}/gfs.{d}/{h}/atmos/gfs.t{h}z.pgrb2.0p25.f{fh:03d}"
    idx = get(base + ".idx").decode().splitlines()
    offs = [(int(l.split(":")[1]), l) for l in idx]
    rngs = {}
    for key in keys:
        # the instantaneous field ("N hour fcst"), never an average ("0-N hour ave fcst")
        hits = [i for i, (_, l) in enumerate(offs) if key in l and l.rstrip(":").endswith("hour fcst")]
        if not hits:
            raise SystemExit(f"{run} f{fh:03d}: no {key!r} in the index")
        i = hits[0]
        rngs[key] = (offs[i][0], offs[i + 1][0] - 1 if i + 1 < len(offs) else "")
    # the fields of one step download side by side (each is a separate byte range of the same file)
    with ThreadPoolExecutor(len(rngs)) as pool:
        msgs = dict(zip(rngs, pool.map(lambda k: get(base, rngs[k]), rngs)))
    out = {}
    for key, msg in msgs.items():
        gid = eccodes.codes_new_from_message(msg)
        try:
            ni, nj = eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")
            vals = eccodes.codes_get_values(gid).reshape(nj, ni)
        finally:
            eccodes.codes_release(gid)
        out[key] = vals
    return out


def to_value(layer, f):
    if layer == "temp":
        return f[FIELDS["temp"][0]] - 273.15
    if layer == "wind":
        return np.hypot(f[FIELDS["wind"][0]], f[FIELDS["wind"][1]])
    return f[FIELDS["rain"][0]] * 3600.0  # kg m-2 s-1 → mm/h


def sample(grid, z, x, y):
    """Bilinear sample of the global 0.25° grid at a tile's 256×256 pixel centres."""
    n = 256 * (1 << z)
    px = (x * 256 + np.arange(256) + 0.5) / n
    py = (y * 256 + np.arange(256) + 0.5) / n
    lon = px * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * py))))
    fi = (90.0 - lat) / 0.25                      # rows
    fj = np.mod(lon, 360.0) / 0.25                # columns, wrapping
    i0 = np.clip(np.floor(fi).astype(int), 0, grid.shape[0] - 2); ti = (fi - i0)[:, None]
    j0 = np.floor(fj).astype(int) % grid.shape[1]; j1 = (j0 + 1) % grid.shape[1]; tj = (fj - np.floor(fj))[None, :]
    a, b = grid[np.ix_(i0, j0)], grid[np.ix_(i0, j1)]
    c, d = grid[np.ix_(i0 + 1, j0)], grid[np.ix_(i0 + 1, j1)]
    return (a * (1 - tj) + b * tj) * (1 - ti) + (c * (1 - tj) + d * tj) * ti


# The wind FIELD for the app's particle lines (the app's spec, 18 Oct 2026): one PNG per step, 1° equirectangular,
# 360 × 181 px, x = lon -180 … 179, y = lat 90 … -90 (row 0 = 90 N); R = u, G = v at 10 m, byte = round((m/s + 40) × 255 / 80)
# clamped (-40 … +40 m/s), B 0, A 255. Point samples of the 0.25° grid at whole degrees, no smoothing.
FIELD = {"width": 360, "height": 181, "lon0": -180, "lat0": 90, "step": 1, "min": -40, "max": 40}


def wind_field_png(u, v):
    cols = (np.arange(-180, 180) % 360) * 4                  # lon -180 … 179 → the 0.25° columns (lon 0 → 359.75)
    rows = np.arange(0, 721, 4)                              # lat 90 … -90
    def enc(a):
        return np.clip(np.rint((a[np.ix_(rows, cols)] + 40) * 255 / 80), 0, 255).astype(np.uint8)
    rgba = np.zeros((181, 360, 4), np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 3] = enc(u), enc(v), 255
    buf = io.BytesIO(); Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


# The data GRIDS, for the app to draw temperature and rain itself (18 Oct 2026): one PNG per layer and step, 0.5°
# equirectangular, 720 × 361 px, x = lon -180 … 179.5, y = lat 90 … -90 (row 0 = 90 N), point samples of the 0.25° grid.
#   temp  grey: byte = round((°C + 64) × 2), clamped; °C = byte / 2 - 64 (0.5 °C steps, -64 … +63.5)
#   rain  RGB: R = 0 under 0.1 mm/h (dry), else round(log10(mm/h + 1) × 100), at least 1; mm/h = 10 ^ (R / 100) - 1
#         G = the snow share, B = the mix share (0 … 255; rain = the rest), both 0 where R is 0
GRID = {"width": 720, "height": 361, "lon0": -180, "lat0": 90, "step": 0.5}
GRID_DECODE = {"temp": "grey PNG: °C = byte / 2 - 64",
               "rain": "RGB PNG: R = 0 dry (under 0.1 mm/h), else mm/h = 10 ^ (R / 100) - 1; G = snow share, B = mix share "
                       "(of 255; rain is the rest)"}


def grid_png(layer, value, ptype=None):
    cols = (np.arange(-360, 360) % 720) * 2                  # lon -180 … 179.5 → the 0.25° columns
    rows = np.arange(0, 721, 2)                              # lat 90 … -90
    v = value[np.ix_(rows, cols)]
    if layer == "temp":
        img = Image.fromarray(np.clip(np.rint((v + 64) * 2), 0, 255).astype(np.uint8), "L")
    else:
        r = np.where(v < FLOOR["rain"], 0, np.clip(np.rint(np.log10(np.maximum(v, 0) + 1) * 100), 1, 255)).astype(np.uint8)
        snow, mix = (np.where(r > 0, np.rint(g[np.ix_(rows, cols)] * 255), 0).astype(np.uint8) for g in ptype)
        img = Image.fromarray(np.stack([r, snow, mix], -1), "RGB")
    buf = io.BytesIO(); img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def ptype_grid(f):
    """(snow, mix) as 0/1 float grids per 0.25° cell; the rest is rain. Freezing rain or ice pellets, or snow together with
    rain, is mix."""
    rain, snow, frzr, icep = (f[k] > 0.5 for k in PTYPE)
    mix = frzr | icep | (snow & rain)
    return (snow & ~mix).astype(np.float32), mix.astype(np.float32)


def tile_png(layer, grid, z, x, y, table, ptype=None, kinds=None):
    xs, colours = table
    v = sample(grid, z, x, y)
    if layer in FLOOR and not (v >= FLOOR[layer]).any():
        return None
    k = np.clip(((v - xs[0]) / (xs[-1] - xs[0]) * (len(xs) - 1)).round().astype(int), 0, len(xs) - 1)
    rgba = colours[k]
    if ptype is not None:
        # blend across a cell edge (bilinear share of snow and of mix) so a rain/snow line is a gradient, not 0.25° steps
        # most tiles hold no snow or mix: look at the tile's own cells first (one cell of margin) before sampling
        n = 1 << z
        lat = [math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n)))) for yy in (y, y + 1)]
        i0, i1 = max(int((90 - lat[0]) / 0.25) - 1, 0), min(int((90 - lat[1]) / 0.25) + 2, 721)
        j0 = int(((x / n * 360 - 180) % 360) / 0.25) - 1
        cols = np.arange(j0, j0 + int(360 / n / 0.25) + 3) % 1440
        if any(g[i0:i1][:, cols].any() for g in ptype):
            ws, wm = (sample(g, z, x, y) for g in ptype)
            wr = 1.0 - ws - wm
            rgba = np.rint(rgba * wr[..., None] + kinds["snow"][1][k] * ws[..., None]
                           + kinds["mix"][1][k] * wm[..., None]).astype(np.uint8)
    if layer in FLOOR:
        rgba[v < FLOOR[layer]] = 0
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def published():
    """The run the public manifest names (YYYYMMDDHH), or None when it cannot be read."""
    try:
        cur = json.loads(get(f"{PUBLIC}/{PREFIX}/latest.json?t={int(time.time())}", tries=2))
        return cur.get("run", "").replace("-", "").replace("T", "")[:10] or None
    except (urllib.error.URLError, ValueError):
        return None


def r2():
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"], aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto",
                        config=Config(retries={"max_attempts": 6, "mode": "adaptive"}, max_pool_connections=32))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run"); ap.add_argument("--out"); ap.add_argument("--upload", action="store_true")
    ap.add_argument("--steps"); ap.add_argument("--layers", default="rain,wind,temp")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    steps = [int(s) for s in a.steps.split(",")] if a.steps else STEPS
    layers = a.layers.split(",")
    run = a.run or latest_run()
    if a.check:
        sys.exit(3 if published() == run else (print(run) or 0))
    if a.upload and not a.run:
        # the scheduler fires more often than a new run lands: a run already published is not drawn again
        if published() == run:
            print(f"GFS run {run} is already published; nothing to do"); return
    global np, Image, eccodes
    import numpy as np
    from PIL import Image
    import eccodes
    run_t = dt.datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    print(f"GFS run {run} · steps {steps[0]}…{steps[-1]} · layers {layers}", flush=True)
    s3 = r2() if a.upload else None
    # rain's stops start at 0.1, 0.25, 0.5 mm/h: 8,192 steps over 0 … 80 is ~0.01 mm/h, fine enough for that soft edge
    tables = {"temp": lut("temp", -45, 45), "wind": lut("wind", 0, 40), "rain": lut("rain", 0, 80, 8192)}
    kinds = {"snow": lut("snow", 0, 80, 8192), "mix": lut("mix", 0, 80, 8192)}   # same xs as rain, so one index serves all three
    keys = [k for l in layers for k in FIELDS[l]] + (PTYPE if "rain" in layers else [])
    written = empty = bytes_out = field_bytes = 0
    t0 = time.time()

    def put(key, body):
        if s3:
            s3.put_object(Bucket=R2_BUCKET, Key=key, Body=body, ContentType="image/png",
                          CacheControl="public, max-age=86400, immutable")
        if a.out:
            p = os.path.join(a.out, key); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(body)

    with ThreadPoolExecutor(16) as pool:
        for fh in steps:
            fields = fetch_fields(run, fh, keys)
            ptype = ptype_grid(fields) if "rain" in layers else None
            jobs = []
            for layer in layers:
                grid = to_value(layer, fields)
                for z in range(MAXZ[layer] + 1):
                    for x in range(1 << z):
                        for y in range(1 << z):
                            png = (tile_png(layer, grid, z, x, y, tables[layer], ptype, kinds) if layer == "rain"
                                   else tile_png(layer, grid, z, x, y, tables[layer]))
                            if png is None:
                                empty += 1; continue
                            written += 1; bytes_out += len(png)
                            jobs.append(pool.submit(put, f"{PREFIX}/{run}/{layer}/{fh:03d}/{z}/{x}/{y}.png", png))
                if layer in GRID_DECODE:
                    png = grid_png(layer, grid, ptype)
                    field_bytes += len(png)
                    jobs.append(pool.submit(put, f"{PREFIX}/{run}/grid/{layer}/{fh:03d}.png", png))
            if "wind" in layers:
                png = wind_field_png(fields[FIELDS["wind"][0]], fields[FIELDS["wind"][1]])
                field_bytes += len(png)
                jobs.append(pool.submit(put, f"{PREFIX}/{run}/windfield/{fh:03d}.png", png))
            for j in jobs: j.result()
            print(f"  f{fh:03d} done · {written} tiles · {empty} empty skipped · {bytes_out / 1e6:.1f} MB · fields+grids {field_bytes / 1e3:.0f} kB · {time.time() - t0:.0f} s", flush=True)

    manifest = {
        "model": "NOAA GFS 0.25°", "attribution": "NOAA GFS", "run": run_t.isoformat().replace("+00:00", "Z"),
        "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        # `key` is what fills {fh} in a layer's url: the tile folders are three digits ("003"), `fh` is the plain number (3).
        "steps": [{"fh": fh, "key": f"{fh:03d}", "valid": (run_t + dt.timedelta(hours=fh)).isoformat().replace("+00:00", "Z")}
                  for fh in steps],
        "layers": {l: {"url": f"{PUBLIC}/{PREFIX}/{run}/{l}/{{fh}}/{{z}}/{{x}}/{{y}}.png", "minZoom": 0, "maxZoom": MAXZ[l],
                       "units": {"temp": "°C", "wind": "m/s", "rain": "mm/h"}[l],
                       "legend": [[v, "#%02x%02x%02x%02x" % c] for v, c in RAMPS[l]],
                       **({"missingTileMeans": "no rain",
                           "legendSnow": [[v, "#%02x%02x%02x%02x" % c] for v, c in RAMPS["snow"]],
                           "legendMix": [[v, "#%02x%02x%02x%02x" % c] for v, c in RAMPS["mix"]]} if l == "rain" else {}),
                       **({"field": {"url": f"{PUBLIC}/{PREFIX}/{run}/windfield/{{fh}}.png", **FIELD}} if l == "wind" else {}),
                       **({"grid": {"url": f"{PUBLIC}/{PREFIX}/{run}/grid/{l}/{{fh}}.png", **GRID, "decode": GRID_DECODE[l]}}
                          if l in GRID_DECODE else {})}
                   for l in layers},
        "tiles": written,
    }
    body = json.dumps(manifest, indent=1, ensure_ascii=False).encode()
    if a.out:
        open(os.path.join(a.out, PREFIX, "latest.json"), "wb").write(body)
    if s3:
        s3.put_object(Bucket=R2_BUCKET, Key=f"{PREFIX}/latest.json", Body=body, ContentType="application/json",
                      CacheControl="public, max-age=300")
        # keep this run and the one before it; delete older runs (the pipeline's own tiles only)
        runs = sorted({p["Prefix"].split("/")[2] for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=R2_BUCKET, Prefix=f"{PREFIX}/", Delimiter="/") for p in page.get("CommonPrefixes", [])})
        old = [r for r in runs if r.isdigit() and len(r) == 10 and r < run][:-1]
        for r in old:
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=R2_BUCKET, Prefix=f"{PREFIX}/{r}/"):
                objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                for i in range(0, len(objs), 1000):
                    s3.delete_objects(Bucket=R2_BUCKET, Delete={"Objects": objs[i:i + 1000], "Quiet": True})
            print(f"  deleted old run {r}")
    print(f"done: run {run} · {written} tiles · {bytes_out / 1e6:.1f} MB · fields+grids {field_bytes / 1e3:.0f} kB · {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
