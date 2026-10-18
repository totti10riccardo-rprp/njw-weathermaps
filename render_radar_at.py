#!/usr/bin/env python3
"""render_radar_at.py — Austria's precipitation "radar" layer from GeoSphere Austria's INCA nowcast, drawn into map tiles on R2.

Source: https://dataset.api.hub.geosphere.at/v1/grid/forecast/nowcast-v1-15min-1km, parameter rr (kg/m² = mm per 15 min),
netCDF on GeoSphere's 1 km Lambert grid (EPSG:31287). A radar, gauge and model blend at 1 km, every 15 minutes, ~0-20 min
behind. Licence: CC BY 4.0 — "All data that is publicly accessible without authentication is licensed under Creative Commons
Attribution 4.0" (https://dataset.api.hub.geosphere.at/v1/docs). Credit: "GeoSphere Austria".

Each run draws the NEWEST frame (the newest run's first step, valid 15 min after its reference time) and adds it to the
manifest; frames older than 2 hours are dropped from the manifest and their tiles deleted. A first run (no manifest)
back-fills the 6 frames the API keeps. Palette and floor: the GFS rain tiles' (render_gfs.py RAMPS["rain"], FLOOR), so
the layer reads like the forecast's rain. Tiles 256 px Web Mercator z5-z9 over Austria; a tile with no rain is not
written (a 404 means clear).

Layout on R2 (bucket njw-maps, public at https://maps.notjustweather.com):
  maps/radar-at/<YYYYMMDDTHHMMZ>/<z>/<x>/<y>.png   (immutable, cached a day)
  maps/radar-at/frames.json                        the manifest, written LAST (cached 60 s)
usage: render_radar_at.py [--upload] [--out DIR]
"""
import argparse, datetime as dt, io, json, math, os, sys, tempfile, time, urllib.request, urllib.error
import numpy as np
from PIL import Image

API = "https://dataset.api.hub.geosphere.at/v1/grid/forecast/nowcast-v1-15min-1km"
BBOX = (45.5, 8.1, 49.5, 17.8)      # south, west, north, east: the dataset's domain
AT = (46.30, 9.45, 49.15, 17.25)    # Austria's box for tiles: south, west, north, east
ZMIN, ZMAX, KEEP_H = 5, 9, 2
R2_BUCKET, PREFIX, PUBLIC = "njw-maps", "maps/radar-at", "https://maps.notjustweather.com"
RAMP = [(0.1, (159, 211, 255, 0)), (0.25, (159, 211, 255, 128)), (0.5, (140, 200, 255, 184)), (2.5, (60, 140, 240, 200)),
        (5, (79, 95, 220, 208)), (10, (122, 63, 208, 212)), (12, (245, 210, 60, 216)), (25, (245, 160, 50, 220)),
        (50, (230, 60, 50, 224)), (80, (180, 20, 140, 230))]   # = render_gfs.RAMPS["rain"]
FLOOR = 0.1                                                     # = render_gfs.FLOOR["rain"], mm/h

# Lambert conformal conic on the Bessel ellipsoid (EPSG:31287, MGI / Austria Lambert); the file's grid is in it. DX/DY: the
# mean residual of the plain forward projection against the file's own WGS84 lat/lon (datum shift), ~100 m after.
_A = 6377397.155; _F = 1 / 299.1528128; _E2 = _F * (2 - _F); _E = _E2 ** .5
def _m(p): s = np.sin(p); return np.cos(p) / np.sqrt(1 - _E2 * s * s)
def _t(p): s = np.sin(p); return np.tan(np.pi / 4 - p / 2) / (((1 - _E * s) / (1 + _E * s)) ** (_E / 2))
_P1, _P2, _P0, _L0 = map(np.radians, (49, 46, 47.5, 13.33333333333333))
_N = (np.log(_m(_P1)) - np.log(_m(_P2))) / (np.log(_t(_P1)) - np.log(_t(_P2))); _G = _m(_P1) / (_N * _t(_P1) ** _N); _R0 = _A * _G * _t(_P0) ** _N
DX, DY = 52.0, 59.0
def lcc(lon, lat):
    lon = np.radians(lon); lat = np.radians(lat); r = _A * _G * _t(lat) ** _N; th = _N * (lon - _L0)
    return 400000 + r * np.sin(th) + DX, 400000 + _R0 - r * np.cos(th) + DY

def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "NotJustWeather-maps/1 (render_radar_at.py)"}), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or i == tries - 1: raise
        except Exception:
            if i == tries - 1: raise
        time.sleep(3 * (i + 1))

def fetch(age, ref):
    import netCDF4
    valid = ref + dt.timedelta(minutes=15); t = valid.strftime("%Y-%m-%dT%H:%M")
    raw = get(f"{API}?parameters=rr&bbox={','.join(map(str, BBOX))}&forecast_offset={age}&start={t}&end={t}&output_format=netcdf")
    with tempfile.NamedTemporaryFile(suffix=".nc") as f:
        f.write(raw); f.flush(); d = netCDF4.Dataset(f.name)
        rr = np.ma.filled(d["rr"][0].astype(np.float32), 0.0); x = np.array(d["x"][:], float); y = np.array(d["y"][:], float)
    return valid, np.maximum(rr, 0) * 4.0, x, y          # mm/h

def sample(mmh, x, y, lon, lat):
    X, Y = lcc(lon, lat); fx = (X - x[0]) / (x[1] - x[0]); fy = (Y - y[0]) / (y[1] - y[0])
    out = (fx < 0) | (fy < 0) | (fx > len(x) - 1) | (fy > len(y) - 1)
    fx = np.clip(fx, 0, len(x) - 1.001); fy = np.clip(fy, 0, len(y) - 1.001); i, j = fy.astype(int), fx.astype(int); a, b = fy - i, fx - j
    v = mmh[i, j] * (1 - a) * (1 - b) + mmh[i, j + 1] * (1 - a) * b + mmh[i + 1, j] * a * (1 - b) + mmh[i + 1, j + 1] * a * b
    v[out] = 0
    return v

XS = np.linspace(0, 80.0, 8193); VS = np.array([s[0] for s in RAMP]); CS = np.array([s[1] for s in RAMP], float)
TAB = np.stack([np.interp(XS, VS, CS[:, k]) for k in range(4)], 1).astype(np.uint8)
def colour(v):
    rgba = TAB[np.clip(np.rint(v / XS[-1] * (len(XS) - 1)).astype(int), 0, len(XS) - 1)]; rgba[v < FLOOR] = 0; return rgba

def tiles(mmh, x, y):
    for z in range(ZMIN, ZMAX + 1):
        n = 1 << z
        tx = lambda lo: int((lo + 180) / 360 * n); ty = lambda la: int((1 - math.asinh(math.tan(math.radians(la))) / math.pi) / 2 * n)
        for X in range(tx(AT[1]), tx(AT[3]) + 1):
            for Y in range(ty(AT[2]), ty(AT[0]) + 1):
                px = (X + (np.arange(256) + .5) / 256) / n; py = (Y + (np.arange(256) + .5) / 256) / n
                lon = np.broadcast_to(px * 360 - 180, (256, 256)); lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * py))))[:, None] * np.ones((1, 256))
                v = sample(mmh, x, y, lon, lat)
                if not (v >= FLOOR).any(): continue
                b = io.BytesIO(); Image.fromarray(colour(v), "RGBA").save(b, "PNG", optimize=True); yield f"{z}/{X}/{Y}", b.getvalue()

def r2():
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"], aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto", config=Config(retries={"max_attempts": 6, "mode": "adaptive"}))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--upload", action="store_true"); ap.add_argument("--out"); a = ap.parse_args()
    t0 = time.time(); s3 = r2() if a.upload else None
    try:
        man = json.loads(get(f"{PUBLIC}/{PREFIX}/frames.json?t={int(time.time())}")) if a.upload else {"keys": []}
    except urllib.error.HTTPError:
        man = {"keys": []}
    have = set(man.get("keys", []))
    meta = json.loads(get(API + "/metadata"))
    last = dt.datetime.strptime(meta["last_forecast_reftime"][:16], "%Y-%m-%dT%H:%M").replace(tzinfo=dt.timezone.utc)
    ages = range(6) if not have else range(1)               # first run: back-fill what the API keeps
    drawn = []
    for age in ages:
        ref = last - dt.timedelta(minutes=15 * age); key = (ref + dt.timedelta(minutes=15)).strftime("%Y%m%dT%H%MZ")
        if key in have: continue
        valid, mmh, x, y = fetch(age, ref); n = nb = 0
        for path, body in tiles(mmh, x, y):
            k = f"{PREFIX}/{key}/{path}.png"; n += 1; nb += len(body)
            if s3: s3.put_object(Bucket=R2_BUCKET, Key=k, Body=body, ContentType="image/png", CacheControl="public, max-age=86400, immutable")
            if a.out: p = os.path.join(a.out, k); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(body)
        drawn.append(key); print(f"  {key}: {n} tiles, {nb / 1e3:.0f} kB, max {mmh.max():.1f} mm/h", flush=True)
    keys = sorted(have | set(drawn)); newest = dt.datetime.strptime(keys[-1], "%Y%m%dT%H%MZ") if keys else None
    keep = [k for k in keys if newest and dt.datetime.strptime(k, "%Y%m%dT%H%MZ") >= newest - dt.timedelta(hours=KEEP_H)]
    iso = lambda k: dt.datetime.strptime(k, "%Y%m%dT%H%MZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {"source": "GeoSphere Austria INCA nowcast (radar, gauges and model, 1 km)", "attribution": "GeoSphere Austria",
           "licence": "CC BY 4.0", "frames": [iso(k) for k in keep], "keys": keep, "step_s": 900,
           "url": f"{PUBLIC}/{PREFIX}/{{key}}/{{z}}/{{x}}/{{y}}.png", "minZoom": ZMIN, "maxZoom": ZMAX,
           "bounds": [AT[1], AT[0], AT[3], AT[2]], "missingTileMeans": "no rain", "units": "mm/h",
           "legend": [[v, "#%02x%02x%02x%02x" % c] for v, c in RAMP],
           "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")}
    body = json.dumps(out, indent=1).encode()
    if a.out: open(os.path.join(a.out, PREFIX, "frames.json") if os.path.isdir(os.path.join(a.out, PREFIX)) else os.path.join(a.out, "frames.json"), "wb").write(body)
    if s3:
        s3.put_object(Bucket=R2_BUCKET, Key=f"{PREFIX}/frames.json", Body=body, ContentType="application/json", CacheControl="public, max-age=60")
        for old in sorted(set(keys) - set(keep)):            # the pipeline's own tiles only
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=R2_BUCKET, Prefix=f"{PREFIX}/{old}/"):
                objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if objs: s3.delete_objects(Bucket=R2_BUCKET, Delete={"Objects": objs, "Quiet": True})
            print(f"  deleted {old}")
    print(f"done: drew {drawn or 'nothing new'}, {len(keep)} frames kept, {time.time() - t0:.0f} s")

if __name__ == "__main__":
    main()
