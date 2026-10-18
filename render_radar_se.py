#!/usr/bin/env python3
"""render_radar_se.py — Sweden's radar layer from SMHI's national radar composite, drawn into map tiles on R2.

Source: https://opendata-download-radar.smhi.se/api/version/latest/area/sweden/product/comp (no key). ODIM_H5 2.2 file,
DBZH (reflectivity, dBZ: dBZ = 0.4 * raw - 30, raw 0 = undetect, raw 255 = nodata) on a 2 km polar-stereographic grid
(+proj=stere +ellps=bessel +lat_0=90 +lon_0=14 +lat_ts=60), 458 x 881 px, a new file every 5 minutes, a few minutes behind.
Rain rate from Marshall-Palmer Z = 200 R^1.6. Licence: SMHI open data, CC BY 4.0 (Creative Commons Erkännande 4.0 SE),
commercial use allowed — "Du ska ange SMHI som källa och även ange om du har ändrat i licensmaterialet"
(https://www.smhi.se/data/om-smhis-data/villkor-for-anvandning). Credit: "SMHI" (state that the data was processed into tiles).

Each run draws the NEWEST frame on the STEP_MIN grid (:00/:15/:30/:45) and adds it to the manifest; frames older than 2 hours
are dropped from the manifest and their tiles deleted. A first run (no manifest) back-fills the last 2 hours. Palette and
floor: the GFS rain tiles' (render_gfs.py RAMPS["rain"], FLOOR). Tiles 256 px Web Mercator z5-z9 over Sweden; a tile with no
rain is not written (a 404 means clear). Pixels outside the composite's footprint or flagged nodata are also not drawn.

Layout on R2 (bucket njw-maps, public at https://maps.notjustweather.com):
  maps/radar-se/<YYYYMMDDTHHMMZ>/<z>/<x>/<y>.png   (immutable, cached a day)
  maps/radar-se/frames.json                        the manifest, written LAST (cached 60 s)
usage: render_radar_se.py [--upload] [--out DIR]
"""
import argparse, datetime as dt, io, json, math, os, sys, tempfile, time, urllib.request, urllib.error
import numpy as np
from PIL import Image

API = "https://opendata-download-radar.smhi.se/api/version/latest/area/sweden/product/comp"
SE = (55.2, 10.9, 69.1, 24.3)       # Sweden's box for tiles: south, west, north, east
ZMIN, ZMAX, KEEP_H = 5, 9, 2
STEP_MIN = 15                       # the source is 5-minute; one frame per STEP_MIN is drawn
R2_BUCKET, PREFIX, PUBLIC = "njw-maps", "maps/radar-se", "https://maps.notjustweather.com"
RAMP = [(0.1, (159, 211, 255, 0)), (0.25, (159, 211, 255, 128)), (0.5, (140, 200, 255, 184)), (2.5, (60, 140, 240, 200)),
        (5, (79, 95, 220, 208)), (10, (122, 63, 208, 212)), (12, (245, 210, 60, 216)), (25, (245, 160, 50, 220)),
        (50, (230, 60, 50, 224)), (80, (180, 20, 140, 230))]   # = render_gfs.RAMPS["rain"]
FLOOR = 0.1                                                     # = render_gfs.FLOOR["rain"], mm/h
MAX_MMH = 80.0                                                  # reflectivity above ~53 dBZ (hail) is capped at the top colour

# Polar stereographic on the Bessel ellipsoid, true scale at 60 N, central meridian 14 E (the file's projdef). Verified
# against the file's four lat/lon corner attributes: all four land within 24 m of the rectangle 458 x 881 x 2 km.
_A = 6377397.155; _F = 1 / 299.1528128; _E2 = _F * (2 - _F); _E = _E2 ** .5
def _t(p): return np.tan(np.pi / 4 - p / 2) / (((1 - _E * np.sin(p)) / (1 + _E * np.sin(p))) ** (_E / 2))
_PC = np.radians(60.0); _L0 = np.radians(14.0)
_K = _A * (np.cos(_PC) / np.sqrt(1 - _E2 * np.sin(_PC) ** 2)) / _t(_PC)
def stere(lon, lat):
    r = _K * _t(np.radians(lat)); d = np.radians(lon) - _L0
    return r * np.sin(d), -r * np.cos(d)
# Grid, from the file's /where: LL_lon/LL_lat is the outer lower-left corner of the 458 x 881 grid of 2 km pixels, row 0 on top.
NX, NY, DXY = 458, 881, 2000.0
X0, Y0 = stere(9.255694381015509, 53.987947441350705)            # lower-left corner, metres
XC0, YC0 = X0 + DXY / 2, Y0 + (NY - 0.5) * DXY                   # centre of pixel (row 0, col 0)

def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "NotJustWeather-maps/1 (render_radar_se.py)"}), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or i == tries - 1: raise
        except Exception:
            if i == tries - 1: raise
        time.sleep(3 * (i + 1))

def listing(day):
    """valid-time keys (YYYYMMDDTHHMMZ) of the day's composite files on the STEP_MIN grid."""
    try: d = json.loads(get(f"{API}/{day:%Y/%m/%d}.json"))
    except urllib.error.HTTPError: return []
    out = []
    for f in d.get("files", []):
        if f["key"].startswith("radar_sweden_comp_") and any(x["key"] == "h5" for x in f["formats"]):
            t = dt.datetime.strptime(f["key"][-12:], "%Y%m%d%H%M")
            if t.minute % STEP_MIN == 0: out.append(t.strftime("%Y%m%dT%H%MZ"))
    return out

def fetch(key):
    import h5py
    t = dt.datetime.strptime(key, "%Y%m%dT%H%MZ")
    raw = get(f"{API}/{t:%Y/%m/%d}/radar_sweden_comp_{t:%Y%m%d%H%M}.h5")
    with tempfile.NamedTemporaryFile(suffix=".h5") as f:
        f.write(raw); f.flush(); h = h5py.File(f.name, "r")
        d = h["dataset1/data1/data"][:]; w = h["dataset1/data1/what"].attrs; gain, off, nodata, undet = (float(w[k]) for k in ("gain", "offset", "nodata", "undetect"))
    dbz = d.astype(np.float32) * gain + off
    z = 10 ** (dbz / 10); mmh = np.minimum((z / 200.0) ** (1 / 1.6), MAX_MMH).astype(np.float32)   # Marshall-Palmer
    mmh[d == undet] = 0.0                                    # undetect: no echo, clear
    mmh[d == nodata] = np.nan                                # nodata: outside the composite, transparent
    # CLUTTER (18 Oct 2026: specks and a 1-2 px streak at 54-80 mm/h on a dry morning): a rain pixel survives only inside a
    # 3x3 block of rain (morphological opening on the rain mask), which drops isolated specks and thin lines; real rain
    # areas are wider than 3 pixels (6 km for SMHI, 1.5 km for DMI) and keep their shape.
    from PIL import Image, ImageFilter
    wet = np.nan_to_num(mmh, nan=0.0) >= 0.1
    keep = np.array(Image.fromarray(np.where(wet, 255, 0).astype(np.uint8)).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))) == 255
    mmh[wet & ~keep] = 0.0
    return mmh

def sample(mmh, lon, lat):
    X, Y = stere(lon, lat); fx = (X - XC0) / DXY; fy = (YC0 - Y) / DXY
    out = (fx < 0) | (fy < 0) | (fx > NX - 1) | (fy > NY - 1)
    fx = np.clip(fx, 0, NX - 1.001); fy = np.clip(fy, 0, NY - 1.001); i, j = fy.astype(int), fx.astype(int); a, b = fy - i, fx - j
    num = np.zeros(lon.shape, np.float32); den = np.zeros(lon.shape, np.float32)
    for di, dj, w in ((0, 0, (1 - a) * (1 - b)), (0, 1, (1 - a) * b), (1, 0, a * (1 - b)), (1, 1, a * b)):
        v = mmh[i + di, j + dj]; ok = ~np.isnan(v); num += np.where(ok, v, 0) * w; den += np.where(ok, w, 0)
    r = num / np.maximum(den, 1e-6); r[(den < 0.5) | out] = 0   # nodata-dominated or outside the grid: not drawn
    return r

XS = np.linspace(0, 80.0, 8193); VS = np.array([s[0] for s in RAMP]); CS = np.array([s[1] for s in RAMP], float)
TAB = np.stack([np.interp(XS, VS, CS[:, k]) for k in range(4)], 1).astype(np.uint8)
def colour(v):
    rgba = TAB[np.clip(np.rint(v / XS[-1] * (len(XS) - 1)).astype(int), 0, len(XS) - 1)]; rgba[v < FLOOR] = 0; return rgba

def tiles(mmh):
    for z in range(ZMIN, ZMAX + 1):
        n = 1 << z
        tx = lambda lo: int((lo + 180) / 360 * n); ty = lambda la: int((1 - math.asinh(math.tan(math.radians(la))) / math.pi) / 2 * n)
        for X in range(tx(SE[1]), tx(SE[3]) + 1):
            for Y in range(ty(SE[2]), ty(SE[0]) + 1):
                px = (X + (np.arange(256) + .5) / 256) / n; py = (Y + (np.arange(256) + .5) / 256) / n
                lon = np.broadcast_to(px * 360 - 180, (256, 256)); lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * py))))[:, None] * np.ones((1, 256))
                v = sample(mmh, lon, lat)
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
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    avail = sorted(set(listing(now.date()) + listing((now - dt.timedelta(days=1)).date())))
    avail = [k for k in avail if dt.datetime.strptime(k, "%Y%m%dT%H%MZ") <= now]
    if not avail: sys.exit("no composite files listed")
    newest = dt.datetime.strptime(avail[-1], "%Y%m%dT%H%MZ")
    want = [k for k in avail if dt.datetime.strptime(k, "%Y%m%dT%H%MZ") >= newest - dt.timedelta(hours=KEEP_H)]
    todo = want if not have else want[-1:]                  # first run: back-fill the last KEEP_H hours
    drawn = []
    for key in todo:
        if key in have: continue
        try: mmh = fetch(key)
        except urllib.error.HTTPError: continue
        n = nb = 0
        for path, body in tiles(mmh):
            k = f"{PREFIX}/{key}/{path}.png"; n += 1; nb += len(body)
            if s3: s3.put_object(Bucket=R2_BUCKET, Key=k, Body=body, ContentType="image/png", CacheControl="public, max-age=86400, immutable")
            if a.out: p = os.path.join(a.out, k); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(body)
        drawn.append(key); print(f"  {key}: {n} tiles, {nb / 1e3:.0f} kB, max {np.nanmax(mmh):.1f} mm/h", flush=True)
    keys = sorted(have | set(drawn)); newest = dt.datetime.strptime(keys[-1], "%Y%m%dT%H%MZ") if keys else None
    keep = [k for k in keys if newest and dt.datetime.strptime(k, "%Y%m%dT%H%MZ") >= newest - dt.timedelta(hours=KEEP_H)]
    iso = lambda k: dt.datetime.strptime(k, "%Y%m%dT%H%MZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {"source": "SMHI radar composite (reflectivity, 2 km, converted to rain rate)", "attribution": "SMHI (CC BY 4.0), processed",
           "licence": "CC BY 4.0", "frames": [iso(k) for k in keep], "keys": keep, "step_s": STEP_MIN * 60,
           "url": f"{PUBLIC}/{PREFIX}/{{key}}/{{z}}/{{x}}/{{y}}.png", "minZoom": ZMIN, "maxZoom": ZMAX,
           "bounds": [SE[1], SE[0], SE[3], SE[2]], "missingTileMeans": "no rain", "units": "mm/h",
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
