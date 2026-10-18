// njw-gov-proxy: allowlist proxy for government map layers. Not an open proxy:
// every upstream is a named entry below; the client supplies ONLY bbox, width/height (or size), time.
// Everything else (service, layers, crs, format ...) is fixed here and the upstream query is rebuilt from scratch.

const UA = "njw-maps-proxy (+https://notjustweather.com)";
const NEG_TTL = 60;
const TIMEOUT_MS = 10000;
const MAX_PX = 1024;
const MAX_BBOX = 21000000; // a bit over the EPSG:3857 world extent (20037508)

const NHC_LAYERS =
  "show:6,7,8,9,32,33,34,35,58,59,60,61,84,85,86,87,110,111,112,113,136,137,138,139,162,163,164,165,188,189,190,191,214,215,216,217,240,241,242,243,266,267,268,269,292,293,294,295,318,319,320,321,344,345,346,347,370,371,372,373";

function wms(base, layer, ttl, extra = {}) {
  return {
    kind: "wms", base, ttl, timeParam: "iso",
    fixed: { service: "WMS", version: "1.3.0", request: "GetMap", layers: layer, styles: "", crs: "EPSG:3857", format: "image/png", transparent: "true", ...extra },
  };
}
function noaaRadar(region) {
  return wms(`https://opengeo.ncep.noaa.gov/geoserver/${region}/${region}_bref_qcd/ows`, `${region}_bref_qcd`, 120);
}
const NOAA_AQ = "https://mapservices.weather.noaa.gov/raster/rest/services/air_quality";

// path (after /gov/) -> source
const SOURCES = {
  "noaa-radar/conus": noaaRadar("conus"),
  "noaa-radar/alaska": noaaRadar("alaska"),
  "noaa-radar/hawaii": noaaRadar("hawaii"),
  "noaa-radar/carib": noaaRadar("carib"),
  "noaa-radar/guam": noaaRadar("guam"),
  "dwd-radar": wms("https://maps.dwd.de/geoserver/dwd/wms", "dwd:Niederschlagsradar", 300),
  "eccc-radar": wms("https://geo.weather.gc.ca/geomet", "RADAR_1KM_RRAI", 360),
  "knmi-radar": wms("https://geoservices.knmi.nl/wms", "RAD_NL25_PCP_CM", 300, { dataset: "RADAR", transparent: "true" }),
  "noaa-nhc": {
    kind: "arcgis", base: "https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/NHC_tropical_weather/MapServer/export",
    ttl: 600, timeParam: null,
    fixed: { bboxSR: "3857", imageSR: "3857", layers: NHC_LAYERS, format: "png32", transparent: "true", f: "image" },
  },
  "noaa-airquality/pm25": {
    kind: "arcgis", base: `${NOAA_AQ}/ndgd_apm25_hr01/ImageServer/exportImage`, ttl: 1800, timeParam: "epoch",
    fixed: { bboxSR: "3857", imageSR: "3857", format: "png32", transparent: "true", f: "image" },
  },
  "noaa-airquality/ozone": {
    kind: "arcgis", base: `${NOAA_AQ}/ndgd_ozone_8hr_avg_time/ImageServer/exportImage`, ttl: 1800, timeParam: "epoch",
    fixed: { bboxSR: "3857", imageSR: "3857", format: "png32", transparent: "true", f: "image" },
  },
};

const NUM = /^-?\d{1,9}(\.\d{1,6})?$/;
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?Z$/;
const EPOCH = /^\d{12,13}$/;

function parseBbox(s) {
  if (!s) return null;
  const p = s.split(",");
  if (p.length !== 4 || !p.every((x) => NUM.test(x))) return null;
  const n = p.map(Number);
  if (n.some((x) => Math.abs(x) > MAX_BBOX) || n[0] >= n[2] || n[1] >= n[3]) return null;
  return p.join(",");
}
function parseDim(s) {
  if (!/^\d{1,4}$/.test(s || "")) return null;
  const n = Number(s);
  return n >= 1 && n <= MAX_PX ? n : null;
}

// Returns {upstream: URL, canonical: URL} or null (-> 404). Unknown query params => null.
function build(src, qs, origin, path) {
  const allowed = new Set(["bbox"]);
  if (src.kind === "wms") { allowed.add("width"); allowed.add("height"); } else allowed.add("size");
  if (src.timeParam) allowed.add("time");
  for (const k of qs.keys()) if (!allowed.has(k) || qs.getAll(k).length !== 1) return null;

  const bbox = parseBbox(qs.get("bbox"));
  if (!bbox) return null;
  const c = new URL(`${origin}/gov/${path}`); // canonical key and what the phone should send
  c.searchParams.set("bbox", bbox);
  const u = new URL(src.base);
  let time = qs.get("time");
  if (time !== null) {
    if (src.timeParam === "iso" ? !ISO.test(time) : !EPOCH.test(time)) return null;
  } else if (src.timeParam === "epoch") return null; // air quality needs its hour
  if (src.kind === "wms") {
    const w = parseDim(qs.get("width")), h = parseDim(qs.get("height"));
    if (!w || !h) return null;
    c.searchParams.set("width", w); c.searchParams.set("height", h);
    for (const [k, v] of Object.entries(src.fixed)) u.searchParams.set(k, v);
    u.searchParams.set("bbox", bbox); u.searchParams.set("width", w); u.searchParams.set("height", h);
  } else {
    const m = /^(\d{1,4}),(\d{1,4})$/.exec(qs.get("size") || "");
    if (!m || !parseDim(m[1]) || !parseDim(m[2])) return null;
    c.searchParams.set("size", `${m[1]},${m[2]}`);
    for (const [k, v] of Object.entries(src.fixed)) u.searchParams.set(k, v);
    u.searchParams.set("bbox", bbox); u.searchParams.set("size", `${m[1]},${m[2]}`);
  }
  if (time !== null) { c.searchParams.set("time", time); u.searchParams.set("time", time); }
  return { upstream: u, canonical: c };
}

function reply(status, body, headers) {
  const h = new Headers(headers);
  h.set("Access-Control-Allow-Origin", "*");
  h.set("X-Content-Type-Options", "nosniff");
  return new Response(body, { status, headers: h });
}
const notFound = () => reply(404, "Not found", { "Content-Type": "text/plain", "Cache-Control": "no-store" });
const badGateway = () => reply(502, "Bad gateway", { "Content-Type": "text/plain", "Cache-Control": "no-store" });

// ---- NASA GIBS satellite tiles (SOURCES.md section 2), Web Mercator {z}/{y}/{x} as GIBS names them (row before column).
// No query string at all. GOES-East/West infrared: the newest picture ("default"), every 10 minutes, zoom 0 … 6.
// VIIRS NOAA-20 true colour: one picture per day, zoom 0 … 9, a date within the last 31 days (a dated tile never
// changes, so it is cached a day).
const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";
const GOES = { "nasa-goes-east": "GOES-East_ABI_Band13_Clean_Infrared", "nasa-goes-west": "GOES-West_ABI_Band13_Clean_Infrared" };
function tileOk(z, y, x, maxZ) {
  if (z > maxZ) return false;
  const n = 2 ** z;
  return y < n && x < n;
}
function gibsTile(path, url) {
  let m = /^(nasa-goes-east|nasa-goes-west)\/(\d{1,2})\/(\d{1,4})\/(\d{1,4})\.png$/.exec(path);
  if (m) {
    const [z, y, x] = [+m[2], +m[3], +m[4]];
    if (url.search || !tileOk(z, y, x, 6)) return { src: null, built: null };
    const upstream = new URL(`${GIBS}/${GOES[m[1]]}/default/default/GoogleMapsCompatible_Level6/${z}/${y}/${x}.png`);
    return { src: { ttl: 600 }, built: { upstream, canonical: new URL(`${url.origin}/gov/${path}`) } };
  }
  m = /^nasa-viirs\/(\d{4}-\d{2}-\d{2})\/(\d{1,2})\/(\d{1,4})\/(\d{1,4})\.jpg$/.exec(path);
  if (m) {
    const [d, z, y, x] = [m[1], +m[2], +m[3], +m[4]];
    const t = Date.parse(`${d}T00:00:00Z`), now = Date.now();
    if (url.search || !tileOk(z, y, x, 9) || isNaN(t) || t > now || now - t > 31 * 86400000) return { src: null, built: null };
    const upstream = new URL(`${GIBS}/VIIRS_NOAA20_CorrectedReflectance_TrueColor/default/${d}/GoogleMapsCompatible_Level9/${z}/${y}/${x}.jpg`);
    return { src: { ttl: 86400 }, built: { upstream, canonical: new URL(`${url.origin}/gov/${path}`) } };
  }
  return null;
}

// ---- /gov/radar-frames: the frame times each radar source really has (from its GetCapabilities time Dimension) ----
const FRAMES_TTL = 120;
const WINDOW_MS = 2 * 3600 * 1000;
const FRAME_SOURCES = {};
for (const r of ["conus", "alaska", "hawaii", "carib", "guam"]) {
  FRAME_SOURCES[`noaa-radar/${r}`] = { url: `https://opengeo.ncep.noaa.gov/geoserver/${r}/${r}_bref_qcd/ows`, layer: `${r}_bref_qcd`, params: {} };
}
FRAME_SOURCES["dwd-radar"] = { url: "https://maps.dwd.de/geoserver/dwd/Niederschlagsradar/ows", layer: "Niederschlagsradar", params: {} };
FRAME_SOURCES["eccc-radar"] = { url: "https://geo.weather.gc.ca/geomet", layer: "RADAR_1KM_RRAI", params: { layer: "RADAR_1KM_RRAI" } };
FRAME_SOURCES["knmi-radar"] = { url: "https://geoservices.knmi.nl/wms", layer: "RAD_NL25_PCP_CM", params: { dataset: "RADAR" } };

const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z"); // no milliseconds: the form /gov tiles accept
function periodMs(p) {
  const m = /^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/.exec(p || "");
  if (!m) return 0;
  return ((+m[1] || 0) * 3600 + (+m[2] || 0) * 60 + (+m[3] || 0)) * 1000;
}
// Expand a WMS time list (items "t" or "start/end/PTnM", comma separated) into ms values inside [lo, hi].
function expandTimes(text, lo, hi) {
  const out = [];
  for (const raw of text.split(",")) {
    const it = raw.trim();
    if (!it) continue;
    const p = it.split("/");
    if (p.length === 1) { const t = Date.parse(it); if (t >= lo && t <= hi) out.push(t); continue; }
    const a = Date.parse(p[0]), b = Date.parse(p[1]), step = periodMs(p[2]);
    if (isNaN(a) || isNaN(b) || !step) continue;
    let t = a < lo ? a + Math.ceil((lo - a) / step) * step : a;
    for (let n = 0; t <= Math.min(b, hi) && n < 1000; t += step, n++) out.push(t);
  }
  return out;
}
function allTimes(text) { // newest-in-list fallback: every instant, no window
  return expandTimes(text, 0, Date.now() + 3 * 86400000);
}
async function framesFor(src, now) {
  const u = new URL(src.url);
  for (const [k, v] of Object.entries({ service: "WMS", version: "1.3.0", request: "GetCapabilities", ...src.params })) u.searchParams.set(k, v);
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const r = await fetch(new Request(u.toString(), { method: "GET", headers: { Accept: "text/xml,application/xml", "User-Agent": UA }, redirect: "manual", signal: ctl.signal, cf: { cacheTtl: FRAMES_TTL, cacheEverything: true } }));
    if (r.status !== 200) return { times: [], step_s: 0 };
    const xml = await r.text();
    const at = xml.indexOf(`<Name>${src.layer}</Name>`); // this layer's own block; KNMI lists many layers
    if (at < 0) return { times: [], step_s: 0 };
    const m = /<Dimension[^>]*name="time"[^>]*>([^<]*)</.exec(xml.slice(at));
    if (!m) return { times: [], step_s: 0 };
    let list = [...new Set(expandTimes(m[1], now - WINDOW_MS, now))].sort((x, y) => x - y);
    if (list.length === 0) { // no frame in the window: still offer the newest one that is not in the future
      const past = allTimes(m[1]).filter((t) => t <= now).sort((x, y) => x - y);
      if (past.length) list = [past[past.length - 1]];
    }
    const d = list.slice(1).map((t, i) => t - list[i]).sort((x, y) => x - y);
    const step = d.length ? Math.round(d[Math.floor(d.length / 2)] / 1000) : 0; // median gap
    return { times: list.map(iso), step_s: step };
  } catch (e) {
    return { times: [], step_s: 0 };
  } finally {
    clearTimeout(timer);
  }
}
async function radarFrames(request, ctx, includeBody) {
  const cache = caches.default;
  const key = new Request(new URL("/gov/radar-frames", request.url).toString(), { method: "GET" });
  const hit = await cache.match(key);
  if (hit) { const r = new Response(includeBody ? hit.body : null, hit); r.headers.set("X-NJW-Cache", "HIT"); return r; }
  const now = Date.now();
  const names = Object.keys(FRAME_SOURCES);
  const res = await Promise.all(names.map((n) => framesFor(FRAME_SOURCES[n], now)));
  const sources = {};
  names.forEach((n, i) => (sources[n] = res[i]));
  const ok = res.some((x) => x.times.length);
  const resp = reply(200, JSON.stringify({ generated: iso(now), sources }), {
    "Content-Type": "application/json", "Cache-Control": `public, max-age=${ok ? FRAMES_TTL : 30}`,
  });
  if (ok) ctx.waitUntil(cache.put(key, resp.clone()));
  const r = new Response(includeBody ? resp.body : null, resp);
  r.headers.set("X-NJW-Cache", "MISS");
  return r;
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return reply(204, null, { "Access-Control-Allow-Methods": "GET, HEAD", "Access-Control-Max-Age": "86400" });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return reply(405, "Method not allowed", { "Content-Type": "text/plain", Allow: "GET, HEAD, OPTIONS", "Cache-Control": "no-store" });
    }
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/gov/")) return notFound();
    const path = url.pathname.slice(5);
    if (path === "radar-frames") {
      if (url.search) return notFound();
      return radarFrames(request, ctx, request.method === "GET");
    }
    let src, built;
    const tile = gibsTile(path, url);
    if (tile) {
      ({ src, built } = tile);
    } else {
      src = Object.hasOwn(SOURCES, path) ? SOURCES[path] : null;
      if (!src) return notFound();
      built = build(src, url.searchParams, url.origin, path);
    }
    if (!built) return notFound();

    const cache = caches.default;
    const key = new Request(built.canonical.toString(), { method: "GET" });
    const wrap = (resp, state) => {
      const r = new Response(request.method === "HEAD" ? null : resp.body, resp);
      r.headers.set("X-NJW-Cache", state);
      return r;
    };
    const hit = await cache.match(key);
    if (hit) return wrap(hit, "HIT");

    // Fresh request: only Accept and a fixed User-Agent. Nothing from the client is copied.
    const out = new Headers({ Accept: "image/png,image/*;q=0.8", "User-Agent": UA });
    if (env.DEBUG_UPSTREAM) console.log("UPSTREAM", built.upstream.toString(), JSON.stringify([...out]));
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
    let up;
    try {
      up = await fetch(new Request(built.upstream.toString(), {
        method: "GET", headers: out, redirect: "manual", signal: ctl.signal,
        cf: { cacheTtl: src.ttl, cacheEverything: true },
      }));
    } catch (e) {
      return badGateway();
    } finally {
      clearTimeout(timer);
    }

    let resp;
    if (up.status === 200) {
      const ct = (up.headers.get("Content-Type") || "").toLowerCase();
      if (!ct.startsWith("image/")) return badGateway(); // WMS/ArcGIS errors often arrive as 200 + XML/JSON
      resp = reply(200, up.body, { "Content-Type": ct, "Cache-Control": `public, max-age=${src.ttl}` });
    } else if (up.status === 404) {
      resp = reply(404, "Not found", { "Content-Type": "text/plain", "Cache-Control": `public, max-age=${NEG_TTL}` });
    } else {
      return badGateway();
    }
    ctx.waitUntil(cache.put(key, resp.clone()));
    return wrap(resp, "MISS");
  },
};
