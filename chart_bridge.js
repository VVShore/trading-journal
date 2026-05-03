#!/usr/bin/env node
/**
 * chart_bridge.js — final
 *
 * Two key fixes:
 *   1. Labels: now uses --study filter with exact indicator names.
 *      "data labels" with no filter returns nothing in most tv versions.
 *      We know the names from debug: "DOL Levels" and "ICT Killzones & Pivots [TFO]"
 *
 *   2. Screenshot: bypasses tv CLI entirely. Uses Chrome DevTools Protocol
 *      directly over WebSocket. No extra dependencies — pure Node.js built-ins.
 */

const { spawnSync } = require("child_process");
const http   = require("http");
const net    = require("net");
const crypto = require("crypto");
const path   = require("path");
const fs     = require("fs");

const CDP_PORT = 9222;
const TIMEOUT  = 12000;

// ── Killzone label text (verified from Pine Script source) ──
const KILLZONE_MAP = {
  "AS.H":   { key: "asia_high",    display: "Asia High"    },
  "AS.L":   { key: "asia_low",     display: "Asia Low"     },
  "LO.H":   { key: "london_high",  display: "London High"  },
  "LO.L":   { key: "london_low",   display: "London Low"   },
  "NYAM.H": { key: "nyam_high",    display: "NY AM High"   },
  "NYAM.L": { key: "nyam_low",     display: "NY AM Low"    },
  "NYL.H":  { key: "nylunch_high", display: "NY Lunch High"},
  "NYL.L":  { key: "nylunch_low",  display: "NY Lunch Low" },
  "NYPM.H": { key: "nypm_high",    display: "NY PM High"   },
  "NYPM.L": { key: "nypm_low",     display: "NY PM Low"    },
  "PDH":    { key: "pdh",          display: "Prev Day High" },
  "PDL":    { key: "pdl",          display: "Prev Day Low"  },
};

// ── CDP helpers ───────────────────────────────────────
function getCDPTabs() {
  return new Promise((resolve, reject) => {
    const req = http.get(`http://localhost:${CDP_PORT}/json`, res => {
      let data = "";
      res.on("data", c => data += c);
      res.on("end", () => {
        try { resolve(JSON.parse(data)); }
        catch { reject(new Error("Could not parse CDP tab list")); }
      });
    });
    req.setTimeout(4000, () => { req.destroy(); reject(new Error("CDP timeout — Chrome not running on port " + CDP_PORT)); });
    req.on("error", err => reject(new Error("Chrome not reachable: " + err.message)));
  });
}

function findTVTab(tabs) {
  return (
    tabs.find(t => t.url?.includes("tradingview.com/chart") && t.type === "page") ||
    tabs.find(t => t.url?.includes("tradingview.com") && t.type === "page")
  );
}

// ── CDP WebSocket screenshot (no external deps) ───────
function cdpScreenshot(wsUrl) {
  return new Promise((resolve, reject) => {
    // Parse ws://localhost:9222/devtools/page/XXX
    const m = wsUrl.match(/^ws:\/\/([^:\/]+):(\d+)(.+)$/);
    if (!m) { reject(new Error("Could not parse WebSocket URL: " + wsUrl)); return; }
    const [, host, port, wsPath] = m;

    const socket = net.createConnection(parseInt(port), host);
    const key    = crypto.randomBytes(16).toString("base64");
    let   buf    = Buffer.alloc(0);
    let   ready  = false;

    const timer = setTimeout(() => {
      socket.destroy();
      reject(new Error("Screenshot timed out after 12s"));
    }, 12000);

    socket.on("connect", () => {
      socket.write(
        `GET ${wsPath} HTTP/1.1\r\n` +
        `Host: ${host}:${port}\r\n` +
        `Upgrade: websocket\r\n` +
        `Connection: Upgrade\r\n` +
        `Sec-WebSocket-Key: ${key}\r\n` +
        `Sec-WebSocket-Version: 13\r\n\r\n`
      );
    });

    function sendWS(text) {
      const payload = Buffer.from(text, "utf8");
      const len     = payload.length;
      const mask    = crypto.randomBytes(4);
      let   header;

      if      (len < 126)   { header = Buffer.alloc(6);  header[0]=0x81; header[1]=0x80|len; mask.copy(header,2); }
      else if (len < 65536) { header = Buffer.alloc(8);  header[0]=0x81; header[1]=0xFE; header.writeUInt16BE(len,2); mask.copy(header,4); }
      else                  { header = Buffer.alloc(14); header[0]=0x81; header[1]=0xFF; header.writeBigUInt64BE(BigInt(len),2); mask.copy(header,10); }

      const masked = Buffer.from(payload);
      for (let i = 0; i < len; i++) masked[i] ^= mask[i % 4];
      socket.write(Buffer.concat([header, masked]));
    }

    socket.on("data", chunk => {
      buf = Buffer.concat([buf, chunk]);

      // Handle HTTP → WS upgrade
      if (!ready) {
        const s = buf.toString("ascii");
        if (!s.includes("\r\n\r\n")) return;
        if (!s.includes("101")) { reject(new Error("WebSocket upgrade failed")); socket.destroy(); return; }
        ready = true;
        buf   = buf.slice(Buffer.from(s).indexOf("\r\n\r\n") + 4);
        sendWS(JSON.stringify({
          id: 1,
          method: "Page.captureScreenshot",
          params: { format: "png", captureBeyondViewport: false }
        }));
      }

      // Parse WebSocket frames
      while (buf.length >= 2) {
        const opcode = buf[0] & 0x0F;
        const masked = !!(buf[1] & 0x80);
        let   plen   = buf[1] & 0x7F;
        let   hlen   = 2 + (masked ? 4 : 0);

        if      (plen === 126) { if (buf.length < 4)  return; plen = buf.readUInt16BE(2);              hlen += 2; }
        else if (plen === 127) { if (buf.length < 10) return; plen = Number(buf.readBigUInt64BE(2));   hlen += 8; }

        if (buf.length < hlen + plen) return;

        let payload = Buffer.from(buf.slice(hlen, hlen + plen));
        if (masked) { const mk = buf.slice(hlen-4, hlen); for (let i=0;i<plen;i++) payload[i]^=mk[i%4]; }
        buf = buf.slice(hlen + plen);

        if (opcode === 1 || opcode === 2) { // text or binary
          try {
            const msg = JSON.parse(payload.toString("utf8"));
            if (msg.id === 1 && msg.result?.data) {
              clearTimeout(timer); socket.destroy();
              resolve(msg.result.data); return;
            }
            if (msg.id === 1 && msg.error) {
              clearTimeout(timer); socket.destroy();
              reject(new Error("CDP error: " + JSON.stringify(msg.error))); return;
            }
          } catch {}
        }
      }
    });

    socket.on("error", e => { clearTimeout(timer); reject(e); });
  });
}

// ── tv CLI ────────────────────────────────────────────
function runTV(args, timeoutMs = TIMEOUT) {
  const argArr = typeof args === "string" ? args.trim().split(/\s+/) : args;
  const env    = { ...process.env, TRADINGVIEW_PORT: String(CDP_PORT) };

  let r = spawnSync("tv", argArr, {
    timeout: timeoutMs, encoding: "utf8",
    shell: process.platform === "win32", env,
  });
  if (!r.error && r.status === 0 && r.stdout?.trim()) return parseTVOut(r.stdout);

  const home = process.env.USERPROFILE || process.env.HOME || "";
  for (const p of [
    path.join(home, "tradingview-mcp", "src", "cli", "index.js"),
    path.join(home, "Documents", "tradingview-mcp", "src", "cli", "index.js"),
    path.join(__dirname, "..", "tradingview-mcp", "src", "cli", "index.js"),
  ]) {
    if (fs.existsSync(p)) {
      r = spawnSync("node", [p, ...argArr], { timeout: timeoutMs, encoding: "utf8", env });
      if (!r.error && r.status === 0 && r.stdout?.trim()) return parseTVOut(r.stdout);
    }
  }
  return { _err: ((r.stderr||"")+(r.error?.message||"")).trim().slice(0,300) || "tv CLI not found" };
}

function parseTVOut(stdout) {
  if (!stdout?.trim()) return { _err: "Empty output" };
  const lines = stdout.trim().split("\n");
  for (let i = lines.length-1; i >= 0; i--) {
    const l = lines[i].trim();
    if (l.startsWith("{") || l.startsWith("[")) {
      try { return JSON.parse(l); } catch {}
    }
  }
  try { return JSON.parse(stdout.trim()); } catch {}
  return { _raw: stdout.trim().slice(0, 300) };
}

function safeNum(v) { const n = parseFloat(v); return isNaN(n) ? null : n; }

function extractTF(state) {
  return state.timeframe || state.resolution || state.interval ||
         state.chart?.timeframe || state.chart?.resolution || null;
}

// ── Parse killzone labels (with study filter result) ──
function parseKZLabels(labels) {
  const levels = {}, display = {};
  for (const lbl of (labels || [])) {
    const raw  = (lbl.text || lbl.label || lbl.tooltip || "").trim();
    const base = raw.split(/[\s(]/)[0].toUpperCase();
    const price = safeNum(lbl.price || lbl.y);
    if (!price || !base) continue;
    const entry = KILLZONE_MAP[base];
    if (entry) {
      levels[entry.key]  = price;
      display[entry.key] = { label: entry.display, price };
    }
  }
  return { levels, display };
}

// ── Parse DOL proxy labels ────────────────────────────
function parseDOLLabels(labels) {
  const items = [];
  for (const lbl of (labels || [])) {
    const text  = (lbl.text || lbl.label || "").trim();
    const price = safeNum(lbl.price || lbl.y);
    if (!price || !text) continue;
    // Skip any that look like killzone labels
    const base = text.split(/[\s(]/)[0].toUpperCase();
    if (KILLZONE_MAP[base]) continue;
    items.push({ label: text, price });
  }
  return items;
}

// ── Format price levels for journal display ───────────
function formatPriceLevels(kzDisplay, dolItems) {
  const lines = [];

  // Killzone levels grouped
  const kzEntries = Object.values(kzDisplay);
  if (kzEntries.length) {
    kzEntries.forEach(e => lines.push(`${e.label}: ${e.price.toFixed(2)}`));
  }

  // DOL proxy levels
  if (dolItems && dolItems.length) {
    dolItems.forEach(e => lines.push(`${e.label}: ${e.price.toFixed(2)}`));
  }

  return lines.length ? lines.join("\n") : null;
}

// ── Parse OBs ─────────────────────────────────────────
function parseOBs(boxes, price) {
  const bullish = [], bearish = [];
  for (const b of (boxes || [])) {
    const top  = safeNum(b.top    || b.y2);
    const bot  = safeNum(b.bottom || b.y1);
    if (!top || !bot || !price) continue;
    const mid  = (top + bot) / 2;
    const dist = Math.abs(price - mid) / price * 100;
    if (dist > 2) continue;
    (mid < price ? bullish : bearish).push({
      top, bottom: bot, mid: +mid.toFixed(2), dist_pct: +dist.toFixed(2)
    });
  }
  return {
    bullish: bullish.sort((a,b)=>a.dist_pct-b.dist_pct).slice(0,3),
    bearish: bearish.sort((a,b)=>a.dist_pct-b.dist_pct).slice(0,3),
  };
}

function detectSession(ts) {
  if (!ts) return null;
  const ms = typeof ts === "number" && ts < 2e10 ? ts*1000 : Number(ts);
  const h  = ((new Date(ms).getUTCHours() - 5 + 24) % 24);
  if (h>=20||h<3) return "asia";
  if (h>=3&&h<8)  return "london";
  if (h>=7&&h<9)  return "ny_pre";
  if (h>=9&&h<12) return "ny_am";
  if (h>=12&&h<16)return "ny_pm";
  return null;
}

function emit(obj) { console.log(JSON.stringify(obj)); }

// ── STATUS ────────────────────────────────────────────
async function cmdStatus() {
  try {
    const tabs  = await getCDPTabs();
    const tvTab = findTVTab(tabs);
    if (tvTab) emit({ connected: true, tab_title: tvTab.title, tab_url: tvTab.url });
    else emit({ connected: false, error: "No TradingView tab found.", hint: "Open https://www.tradingview.com/chart/ in the debug Chrome window." });
  } catch (e) {
    emit({ connected: false, error: e.message, hint: "Run launch_chrome_debug.bat" });
  }
}

// ── DEBUG ─────────────────────────────────────────────
async function cmdDebug() {
  const result = {
    step: "start", cdp_connected: false, tv_tab_found: false,
    cli_connected: false, symbol: null, timeframe: null,
    indicators: [], kz_label_count: 0, dol_label_count: 0,
    kz_labels_raw: [], dol_labels_raw: [],
    kz_levels_parsed: {}, dol_items: [],
    box_count: 0, diagnosis: null, error: null,
  };

  let tabs;
  try {
    tabs = await getCDPTabs();
    result.cdp_connected = true;
    result.step = "cdp_ok";
    result.all_tabs = tabs.map(t => (t.url||"").slice(0,80));
  } catch (e) { result.error = "CDP failed: " + e.message; emit(result); return; }

  const tvTab = findTVTab(tabs);
  if (!tvTab) { result.error = "No TradingView tab found."; emit(result); return; }
  result.tv_tab_found = true; result.tv_tab_url = tvTab.url; result.step = "tv_tab_found";

  const state = runTV("state", 9000);
  if (state._err) { result.error = "tv CLI failed: " + state._err; emit(result); return; }
  result.cli_connected = true;
  result.symbol     = state.symbol || state.chart?.symbol;
  result.timeframe  = extractTF(state);
  result.indicators = (state.indicators || state.studies || []).map(i => i.name || i.title).filter(Boolean);
  result.step = "cli_ok";

  // Read killzones labels — pass as array so spaces in name don't get split
  const kzName = result.indicators.find(n => n.includes("Killzones") || n.includes("TFO"));
  if (kzName) {
    const kzLbls = runTV(["data", "labels", "--study", kzName], 10000);
    if (!kzLbls._err) {
      result.kz_label_count = (kzLbls.labels||[]).length;
      result.kz_labels_raw  = (kzLbls.labels||[]).slice(0,10).map(l => ({ text:(l.text||l.label||""), price:safeNum(l.price||l.y) }));
      const parsed = parseKZLabels(kzLbls.labels||[]);
      result.kz_levels_parsed = parsed.levels;
    } else {
      result.kz_error = kzLbls._err;
    }
  } else {
    result.kz_error = "Killzones indicator not found in active indicators.";
  }

  // Read DOL proxy labels — pass as array
  const dolName = result.indicators.find(n => n.includes("DOL"));
  if (dolName) {
    const dolLbls = runTV(["data", "labels", "--study", dolName], 8000);
    if (!dolLbls._err) {
      result.dol_label_count = (dolLbls.labels||[]).length;
      result.dol_labels_raw  = (dolLbls.labels||[]).slice(0,10).map(l => ({ text:(l.text||l.label||""), price:safeNum(l.price||l.y) }));
      result.dol_items       = parseDOLLabels(dolLbls.labels||[]);
    }
  }

  // Boxes
  const bxs = runTV("data boxes", 7000);
  result.box_count = bxs._err ? 0 : (bxs.boxes||[]).length;

  result.step = "complete";

  // Diagnosis
  const tf    = result.timeframe;
  const tfNum = parseInt(tf);
  if (!result.kz_levels_parsed || Object.keys(result.kz_levels_parsed).length === 0) {
    if (!tf || isNaN(tfNum) || tfNum >= 30) {
      result.diagnosis = `NO LEVELS: Timeframe is ${tf||"unknown"} (${isNaN(tfNum)?'could not read':'>=30min'}). Killzones only draws on ≤15min. Switch to 5min, then click Debug again.`;
    } else if (result.kz_label_count === 0) {
      result.diagnosis = `On ${tf}min (correct) but 0 labels returned. Check: is 'Show Pivot Labels' enabled in the Killzones indicator settings in TradingView?`;
    } else {
      result.diagnosis = `${result.kz_label_count} labels visible but none matched killzone text. Labels seen: ${result.kz_labels_raw.map(l=>l.text).join(", ")}`;
    }
  } else {
    result.diagnosis = `OK — ${Object.keys(result.kz_levels_parsed).length} killzone levels + ${result.dol_items.length} DOL levels found.`;
  }

  result.summary = `TF:${tf||"null"} | KZ labels:${result.kz_label_count} | KZ levels:${Object.keys(result.kz_levels_parsed).length} | DOL labels:${result.dol_label_count} | Boxes:${result.box_count}`;
  emit(result);
}

// ── READ ──────────────────────────────────────────────
async function cmdRead() {
  const result = {
    connected: false, error: null,
    chart: null, quote: null, session: null,
    levels: null, levels_display: null, formatted_levels: null,
    custom_levels: null, orderblocks: null,
    warnings: [],
  };

  let tabs, tvTab;
  try {
    tabs  = await getCDPTabs();
    tvTab = findTVTab(tabs);
    if (!tvTab) { result.error = "No TradingView tab found."; emit(result); return; }
    result.connected = true;
  } catch (e) { result.error = e.message; emit(result); return; }

  const state = runTV("state", 9000);
  if (state._err) { result.error = "tv CLI failed: " + state._err; emit(result); return; }

  const tf  = extractTF(state);
  const inds = (state.indicators || state.studies || []).map(i => i.name || i.title).filter(Boolean);
  result.chart = { symbol: state.symbol || state.chart?.symbol, timeframe: tf, indicators: inds };

  const tfNum = parseInt(tf);
  if (!tf || isNaN(tfNum) || tfNum >= 30) {
    result.warnings.push(
      tf ? `On ${tf}min — switch to 5min or 15min to read Killzone levels, then click Read Chart.`
         : "Timeframe not detected — switch to 5min or 15min, then click Read Chart."
    );
  }

  const quote = runTV("quote", 5000);
  if (!quote._err) {
    result.quote   = { price: safeNum(quote.close || quote.last || quote.price), time: quote.time };
    result.session = detectSession(result.quote.time);
  }
  const price = result.quote?.price;

  // Read killzones — array form so spaces in name aren't split
  const kzName  = inds.find(n => n.includes("Killzones") || n.includes("TFO"));
  const dolName = inds.find(n => n.includes("DOL"));
  let kzDisplay = {}, dolItems = [];

  if (kzName) {
    const kzLbls = runTV(["data", "labels", "--study", kzName], 10000);
    if (!kzLbls._err) {
      const parsed = parseKZLabels(kzLbls.labels || []);
      result.levels  = parsed.levels;
      kzDisplay      = parsed.display;
    }
  }

  if (dolName) {
    const dolLbls = runTV(["data", "labels", "--study", dolName], 8000);
    if (!dolLbls._err) {
      dolItems             = parseDOLLabels(dolLbls.labels || []);
      result.custom_levels = dolItems.length ? dolItems : null;
    }
  }

  // Format for journal display
  result.formatted_levels = formatPriceLevels(kzDisplay, dolItems);

  if (!result.levels || !Object.keys(result.levels).length) {
    if (!result.warnings.length) {
      result.warnings.push(
        !tf || isNaN(tfNum) || tfNum >= 30
          ? "Switch to 5min or 15min timeframe to read session levels."
          : "No session labels found — check 'Show Pivot Labels' is ON in Killzones settings."
      );
    }
  }

  // Boxes for OBs
  const bxs = runTV("data boxes", 7000);
  if (!bxs._err && price) {
    const obs = parseOBs(bxs.boxes || [], price);
    if (obs.bullish.length || obs.bearish.length) result.orderblocks = obs;
  }

  emit(result);
}

// ── SCREENSHOT via CDP WebSocket ─────────────────────
async function cmdScreenshot(savePath) {
  const imgDir = path.join(__dirname, "images");
  try { fs.mkdirSync(imgDir, { recursive: true }); } catch {}

  const fname  = savePath || path.join(imgDir, `tv_${Date.now()}.png`);
  const target = fname.endsWith(".png") ? fname : fname + ".png";

  let tvTab;
  try {
    const tabs = await getCDPTabs();
    tvTab = findTVTab(tabs);
    if (!tvTab) { emit({ success: false, error: "No TradingView tab found." }); return; }
  } catch (e) { emit({ success: false, error: e.message }); return; }

  const wsUrl = tvTab.webSocketDebuggerUrl;
  if (!wsUrl) { emit({ success: false, error: "TradingView tab has no WebSocket debugger URL. Try reloading TradingView." }); return; }

  try {
    const base64 = await cdpScreenshot(wsUrl);
    const buf    = Buffer.from(base64, "base64");
    fs.writeFileSync(target, buf);
    emit({ success: true, path: target, filename: path.basename(target) });
  } catch (e) {
    emit({ success: false, error: "CDP screenshot failed: " + e.message });
  }
}

// ── Entry ─────────────────────────────────────────────
const cmd = process.argv[2] || "status";
const arg = process.argv[3];
(async () => {
  switch (cmd) {
    case "status":     await cmdStatus();        break;
    case "debug":      await cmdDebug();         break;
    case "read":       await cmdRead();          break;
    case "screenshot": await cmdScreenshot(arg); break;
    default: emit({ error: "Unknown: " + cmd });
  }
})();
