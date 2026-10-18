// Radar pictures: only real precipitation may show. Some sources paint the area outside their radars' range in grey ("no
// data"), and DWD also draws a magenta outline round its coverage. Those sources are asked for a PALETTED PNG (GeoServer
// format=image/png8), and the palette entries that are not precipitation get alpha 0 in the tRNS chunk. The pixel data is
// never decoded, so this costs almost nothing per tile and works at every bbox size. A PNG that is not paletted is passed
// through unchanged (nothing is guessed).

// Each rule gets one palette entry (r, g, b, a) and says whether it is precipitation.
// DWD Niederschlagsradar: the legend (GetLegendGraphic, 18 Oct 2026) is "Keine Daten" #7D7D7D at opacity 0.3, #FFFFFF at
// opacity 0 for no rain, then 15 classes #33FFFF … #0000FE, all opaque. Only those 15 colours are kept; the grey, the white
// and the outline (#FB00FF and its anti-aliased edge, which is not in the legend) go.
const DWD_RAIN = ["33FFFF", "1ACC9A", "019934", "4DB31B", "99CC01", "CCE601", "FFFF01", "FFC401", "FF8901", "FF4501",
  "FE0000", "E5004C", "CC0098", "6600CB", "0000FE"].map((h) => [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)));
const near = (r, g, b, [R, G, B]) => Math.abs(r - R) <= 6 && Math.abs(g - G) <= 6 && Math.abs(b - B) <= 6;
// A light grey or white entry: FMI's nodata #CCCCCC (opacity 0.2) and RMI's #D2D2D2. Neither source draws rain in grey
// or white (FMI legend: #6CEBF3 … #FA51A5; RMI: blues and cyans, checked on the 18 Oct frames).
const lightGrey = (r, g, b) => Math.max(r, g, b) - Math.min(r, g, b) <= 10 && Math.min(r, g, b) >= 150;

export const RULES = {
  dwd: (r, g, b, a) => a >= 250 && DWD_RAIN.some((c) => near(r, g, b, c)),
  greyIsNoData: (r, g, b) => !lightGrey(r, g, b),
};

let CRC;
function crc32(bytes) {
  if (!CRC) {
    CRC = new Uint32Array(256);
    for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; CRC[n] = c >>> 0; }
  }
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const out = new Uint8Array(12 + data.length), dv = new DataView(out.buffer);
  dv.setUint32(0, data.length);
  for (let i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i);
  out.set(data, 8);
  dv.setUint32(8 + data.length, crc32(out.subarray(4, 8 + data.length)));
  return out;
}

// Returns {png, cleared} with the non-precipitation palette entries made transparent, or null when the bytes are not a
// paletted PNG (the caller then passes the original through).
export function clearPalette(buf, isRain) {
  const u = new Uint8Array(buf), dv = new DataView(u.buffer, u.byteOffset, u.byteLength);
  const SIG = [137, 80, 78, 71, 13, 10, 26, 10];
  if (u.length < 33 || SIG.some((v, i) => u[i] !== v)) return null;
  const chunks = [];
  for (let p = 8; p + 12 <= u.length;) {
    const len = dv.getUint32(p), type = String.fromCharCode(u[p + 4], u[p + 5], u[p + 6], u[p + 7]);
    if (p + 12 + len > u.length) return null;
    chunks.push({ type, start: p, len, data: u.subarray(p + 8, p + 8 + len) });
    p += 12 + len;
    if (type === "IEND") break;
  }
  if (chunks[0]?.type !== "IHDR" || chunks[0].data[9] !== 3) return null; // colour type 3 = paletted
  const plte = chunks.find((c) => c.type === "PLTE"), trns = chunks.find((c) => c.type === "tRNS");
  if (!plte || plte.len % 3) return null;
  const n = plte.len / 3, alpha = new Uint8Array(n).fill(255);
  if (trns) alpha.set(trns.data.subarray(0, Math.min(trns.len, n)));
  let cleared = 0;
  for (let i = 0; i < n; i++) {
    if (alpha[i] === 0) continue;
    if (!isRain(plte.data[3 * i], plte.data[3 * i + 1], plte.data[3 * i + 2], alpha[i])) { alpha[i] = 0; cleared++; }
  }
  const parts = [u.subarray(0, 8)];
  for (const c of chunks) {
    if (c.type === "tRNS") continue;
    parts.push(u.subarray(c.start, c.start + 12 + c.len));
    if (c.type === "PLTE") parts.push(chunk("tRNS", alpha));
  }
  const out = new Uint8Array(parts.reduce((s, p) => s + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return { png: out, cleared };
}
