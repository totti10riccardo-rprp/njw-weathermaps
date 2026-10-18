#!/usr/bin/env python3
"""render_radar_dk.py — Denmark's radar layer from DMI's national radar composite, drawn into map tiles on R2.

Source: https://opendataapi.dmi.dk/v1/radardata/collections/composite/items (no key; STAC-style list, then /download/<id>).
ODIM_H5 file, DBZH (reflectivity, dBZ: dBZ = 0.5 * raw - 32, raw 0 = undetect, raw 255 = nodata) on a 500 m stereographic grid
(+proj=stere +ellps=WGS84 +lat_0=56 +lon_0=10.5666), 1984 x 1728 px, a new file every 5 minutes, ~5 minutes behind (the
file's own /how carries zr-a = 200, zr-b = 1.6, i.e. Marshall-Palmer Z = 200 R^1.6, which is what is used here).
Licence: DMI open data, CC BY 4.0, commercial use allowed — "You must give appropriate credit, provide a link to the license,
and indicate if changes were made" (https://www.dmi.dk/friedata/dokumentation/terms-of-use). Credit: "DMI" (say the data
was processed into tiles).

Each run draws the NEWEST frame on the STEP_MIN grid (:00/:15/:30/:45) and adds it to the manifest; frames older than 2 hours
are dropped from the manifest and their tiles deleted. A first run (no manifest) back-fills the last 2 hours. Palette and
floor: the GFS rain tiles' (render_gfs.py RAMPS["rain"], FLOOR). Tiles 256 px Web Mercator z5-z9 over Denmark; a tile with no
rain is not written (a 404 means clear). Pixels outside the composite's footprint or flagged nodata are also not drawn.

Layout on R2 (bucket njw-maps, public at https://maps.notjustweather.com):
  maps/radar-dk/<YYYYMMDDTHHMMZ>/<z>/<x>/<y>.png   (immutable, cached a day)
  maps/radar-dk/frames.json                        the manifest, written LAST (cached 60 s)
usage: render_radar_dk.py [--upload] [--out DIR]
"""
import argparse, datetime as dt, io, json, math, os, sys, tempfile, time, urllib.request, urllib.error
import numpy as np
from PIL import Image

API = "https://opendataapi.dmi.dk/v1/radardata"
DK = (54.5, 7.9, 57.9, 15.3)        # Denmark's box (Bornholm included) for tiles: south, west, north, east
ZMIN, ZMAX, KEEP_H = 5, 9, 2
STEP_MIN = 15                       # the source is 5-minute; one frame per STEP_MIN is drawn
R2_BUCKET, PREFIX, PUBLIC = "njw-maps", "maps/radar-dk", "https://maps.notjustweather.com"
RAMP = [(0.1, (159, 211, 255, 0)), (0.25, (159, 211, 255, 128)), (0.5, (140, 200, 255, 184)), (2.5, (60, 140, 240, 200)),
        (5, (79, 95, 220, 208)), (10, (122, 63, 208, 212)), (12, (245, 210, 60, 216)), (25, (245, 160, 50, 220)),
        (50, (230, 60, 50, 224)), (80, (180, 20, 140, 230))]   # = render_gfs.RAMPS["rain"]
FLOOR = 0.1                                                     # = render_gfs.FLOOR["rain"], mm/h
MAX_MMH = 80.0                                                  # reflectivity above ~53 dBZ (hail) is capped at the top colour

# Oblique stereographic on WGS84 (the file's projdef), origin 56 N 10.5666 E, k0 = 1 (Snyder 21-24..21-27 on the conformal
# sphere). Verified against the file's four lat/lon corner attributes: they are the CENTRES of the corner pixels, and all four
# land on the 1983 x 1727 (x 500 m) rectangle to < 0.001 m.
_A = 6378137.0; _F = 1 / 298.257223563; _E2 = _F * (2 - _F); _E = _E2 ** .5
_P0 = np.radians(56.0); _L0 = np.radians(10.5666)
def _chi(p): s = np.sin(p); return 2 * np.arctan(np.tan(np.pi / 4 + p / 2) * ((1 - _E * s) / (1 + _E * s)) ** (_E / 2)) - np.pi / 2
_C1 = _chi(_P0); _M1 = np.cos(_P0) / np.sqrt(1 - _E2 * np.sin(_P0) ** 2)
def stere(lon, lat):
    c = _chi(np.radians(lat)); d = np.radians(lon) - _L0
    A = 2 * _A * _M1 / (np.cos(_C1) * (1 + np.sin(_C1) * np.sin(c) + np.cos(_C1) * np.cos(c) * np.cos(d)))
    return A * np.cos(c) * np.sin(d), A * (np.cos(_C1) * np.sin(c) - np.sin(_C1) * np.cos(c) * np.cos(d))
# Grid, from /where: LL_lon/LL_lat is the centre of the lower-left pixel of the 1984 x 1728 grid of 500 m pixels, row 0 on top.
NX, NY, DXY = 1984, 1728, 500.0
XC0, Y0 = stere(4.3790827, 52.29427206)
YC0 = Y0 + (NY - 1) * DXY                                        # centre of pixel (row 0, col 0)

def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "NotJustWeather-maps/1 (render_radar_dk.py)"}), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or i == tries - 1: raise
        except Exception:
            if i == tries - 1: raise
        time.sleep(3 * (i + 1))

def listing(since):
    """valid-time keys (YYYYMMDDTHHMMZ) -> download URL, for composite files since `since` on the STEP_MIN grid."""
    d = json.loads(get(f"{API}/collections/composite/items?limit=500&sortorder=datetime,DESC&datetime={since:%Y-%m-%dT%H:%M:%SZ}/.."))
    out = {}
    for f in d.get("features", []):
        t = dt.datetime.strptime(f["properties"]["datetime"][:16], "%Y-%m-%dT%H:%M")
        if t.minute % STEP_MIN == 0: out[t.strftime("%Y%m%dT%H%MZ")] = f["asset"]["data"]["href"]
    return out

def fetch(url):
    import h5py
    raw = get(url)
    with tempfile.NamedTemporaryFile(suffix=".h5") as f:
        f.write(raw); f.flush(); h = h5py.File(f.name, "r")
        d = h["dataset1/data1/data"][:]; w = h["what"].attrs; gain, off, nodata, undet = (float(w[k]) for k in ("gain", "offset", "nodata", "undetect"))
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
        for X in range(tx(DK[1]), tx(DK[3]) + 1):
            for Y in range(ty(DK[2]), ty(DK[0]) + 1):
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
    urls = listing(now - dt.timedelta(hours=KEEP_H + 1))
    if not urls: sys.exit("no composite files listed")
    avail = sorted(urls); newest = dt.datetime.strptime(avail[-1], "%Y%m%dT%H%MZ")
    want = [k for k in avail if dt.datetime.strptime(k, "%Y%m%dT%H%MZ") >= newest - dt.timedelta(hours=KEEP_H)]
    todo = want if not have else want[-1:]                  # first run: back-fill the last KEEP_H hours
    drawn = []
    for key in todo:
        if key in have: continue
        try: mmh = fetch(urls[key])
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
    out = {"source": "DMI radar composite (reflectivity, 500 m, converted to rain rate)", "attribution": "DMI (CC BY 4.0), processed",
           "licence": "CC BY 4.0", "frames": [iso(k) for k in keep], "keys": keep, "step_s": STEP_MIN * 60,
           "url": f"{PUBLIC}/{PREFIX}/{{key}}/{{z}}/{{x}}/{{y}}.png", "minZoom": ZMIN, "maxZoom": ZMAX,
           "bounds": [DK[1], DK[0], DK[3], DK[2]], "missingTileMeans": "no rain", "units": "mm/h",
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
