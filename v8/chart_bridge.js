#!/usr/bin/env node
/**
 * chart_bridge.js — v8 calibrated
 * Calibrated to: "ICT Killzones & Pivots [TFO]"
 *
 * From the Pine Script source, the pivot labels use these exact strings:
 *   Asia High:    ash_str = "AS.H"   (default, user-configurable)
 *   Asia Low:     asl_str = "AS.L"
 *   London High:  loh_str = "LO.H"
 *   London Low:   lol_str = "LO.L"
 *   NY AM High:   nah_str = "NYAM.H"
 *   NY AM Low:    nal_str = "NYAM.L"
 *   NY Lunch H:   nlh_str = "NYL.H"
 *   NY Lunch L:   nll_str = "NYL.L"
 *   NY PM High:   nph_str = "NYPM.H"
 *   NY PM Low:    npl_str = "NYPM.L"
 *
 * IMPORTANT LIMITATIONS:
 *   - Only draws on timeframes <= 30min (tf_limit default in the indicator)
 *   - If you changed the label strings in indicator settings, update LABEL_TEXT below
 *   - Manually drawn trend lines CANNOT be read (different system entirely)
 *   - Standard deviation drawings CANNOT be read (same reason)
 *
 * If your labels don't appear: open TradingView, click the Killzones indicator
 * settings, go to "Killzone Pivots" section, and check what text is in the
 * "Killzone 1 Labels" fields. Update LABEL_TEXT.asia_high etc. to match.
 */

const { spawnSync } = require("child_process");
const path = require("path");
const fs   = require("fs");

const TIMEOUT = 8000;

// ── YOUR LABEL TEXT ───────────────────────────────────
// These match the DEFAULT values in the Pine Script.
// If you changed them in the indicator settings, update these.
const LABEL_TEXT = {
  asia_high:   "AS.H",
  asia_low:    "AS.L",
  london_high: "LO.H",
  london_low:  "LO.L",
  nyam_high:   "NYAM.H",
  nyam_low:    "NYAM.L",
  nylunch_high:"NYL.H",
  nylunch_low: "NYL.L",
  nypm_high:   "NYPM.H",
  nypm_low:    "NYPM.L",
};

// ── Locate tv CLI ─────────────────────────────────────
function runTV(args, timeoutMs = TIMEOUT) {
  const argArr = typeof args === "string" ? args.split(" ") : args;

  // Try npm-linked global first
  let result = spawnSync("tv", argArr, {
    timeout: timeoutMs, encoding: "utf8",
    shell: process.platform === "win32",
  });

  if (!result.error && result.status === 0 && result.stdout) {
    return parseOutput(result.stdout);
  }

  // Try local paths
  const localPaths = [
    path.join(process.env.USERPROFILE || process.env.HOME || "", "tradingview-mcp", "src", "cli", "index.js"),
    path.join(process.env.USERPROFILE || process.env.HOME || "", "Documents", "tradingview-mcp", "src", "cli", "index.js"),
    path.join(__dirname, "..", "tradingview-mcp", "src", "cli", "index.js"),
    path.join(__dirname, "tradingview-mcp", "src", "cli", "index.js"),
  ];

  for (const p of localPaths) {
    if (fs.existsSync(p)) {
      result = spawnSync("node", [p, ...argArr], {
        timeout: timeoutMs, encoding: "utf8",
      });
      if (!result.error && result.status === 0 && result.stdout) {
        return parseOutput(result.stdout);
      }
    }
  }

  const errMsg = (result.stderr || result.error?.message || "tv CLI not found").trim().slice(0, 200);
  return { error: errMsg };
}

function parseOutput(stdout) {
  const text = (stdout || "").trim();
  if (!text) return { error: "Empty output" };
  const lines = text.split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (line.startsWith("{") || line.startsWith("[")) {
      try { return JSON.parse(line); } catch {}
    }
  }
  return { raw: text.slice(0, 200) };
}

function safeNum(v) {
  const n = parseFloat(v);
  return isNaN(n) ? null : n;
}

// ── Indicator name matching ───────────────────────────
const INDICATOR_PATTERNS = {
  killzones:  ["killzone", "kill zone", "ict kill", "tfo", "pivots [tfo]"],
  orderblock: ["order block", "orderblock", "ob ", "mtf s&d", "s&d"],
  fvg:        ["fvg", "fair value", "nephew", "imbalance", "ifvg"],
  smt:        ["smt", "smart money technique", "smart money div"],
  vol_profile:["volume profile", "vol profile", "vpvr"],
};

function categorizeIndicators(indicators) {
  const found = {};
  for (const ind of (indicators || [])) {
    const name = (ind.name || ind.title || ind.id || "").toLowerCase();
    if (!name) continue;
    for (const [type, patterns] of Object.entries(INDICATOR_PATTERNS)) {
      if (patterns.some(p => name.includes(p))) {
        found[type] = ind.name || ind.title || ind.id;
        break;
      }
    }
  }
  return found;
}

// ── Parse killzone labels (calibrated to TFO indicator) ──
function parseKillzoneLabels(labels) {
  const levels = {};

  for (const lbl of (labels || [])) {
    // The label text might include price in parentheses: "AS.H (18245.50)"
    // Strip everything after the first space to get the base label
    const rawText = (lbl.text || lbl.label || lbl.tooltip || "").trim();
    const baseText = rawText.split(" ")[0].split("(")[0].trim();

    const price = safeNum(lbl.price || lbl.y);
    if (!price) continue;

    // Exact match first
    for (const [key, txt] of Object.entries(LABEL_TEXT)) {
      if (baseText === txt || rawText.startsWith(txt)) {
        // Map nylunch and nyam to our standard keys
        const mappedKey = key.replace("nylunch_", "nylunch_").replace("nyam_", "nyam_");
        levels[mappedKey] = price;
        break;
      }
    }
  }

  return levels;
}

// ── Parse killzone lines (fallback — lines don't have text usually) ──
function parseKillzoneLines(lines, indicators_found) {
  // The TFO indicator draws horizontal lines for each pivot
  // Lines don't carry text labels by default, so we can't reliably
  // identify them without the label text. Skip for now.
  return {};
}

// ── Parse OB boxes ────────────────────────────────────
function parseOBs(boxes, currentPrice) {
  const bullish = [], bearish = [];
  for (const box of (boxes || [])) {
    const top = safeNum(box.top || box.y2);
    const bot = safeNum(box.bottom || box.y1);
    if (!top || !bot || !currentPrice) continue;
    const mid = (top + bot) / 2;
    const dist = Math.abs(currentPrice - mid) / currentPrice * 100;
    if (dist > 2) continue;
    const entry = { top, bottom: bot, mid: parseFloat(mid.toFixed(2)), dist_pct: parseFloat(dist.toFixed(2)) };
    if (mid < currentPrice) bullish.push(entry);
    else bearish.push(entry);
  }
  return {
    bullish: bullish.sort((a,b) => a.dist_pct - b.dist_pct).slice(0,3),
    bearish: bearish.sort((a,b) => a.dist_pct - b.dist_pct).slice(0,3),
  };
}

// ── Parse FVG levels ──────────────────────────────────
function parseFVGs(lines, boxes, currentPrice) {
  const fvgs = [];
  for (const item of [...(lines || []), ...(boxes || [])]) {
    const price = safeNum(item.price || item.y || item.top || item.y1);
    if (!price || !currentPrice) continue;
    const dist = Math.abs(currentPrice - price) / currentPrice * 100;
    if (dist > 1.5) continue;
    fvgs.push({ price: parseFloat(price.toFixed(2)), dist_pct: parseFloat(dist.toFixed(2)) });
  }
  return fvgs.sort((a,b) => a.dist_pct - b.dist_pct).slice(0,5);
}

// ── Session detection ─────────────────────────────────
function detectSession(timestamp) {
  if (!timestamp) return null;
  const ms = typeof timestamp === "number" ? timestamp * 1000 : new Date(timestamp).getTime();
  const d  = new Date(ms);
  // Convert to NY time (EST = UTC-5, EDT = UTC-4; rough approximation)
  const h = ((d.getUTCHours() - 5 + 24) % 24);
  if (h >= 20 || h < 3)  return "asia";
  if (h >= 3  && h < 8)  return "london";
  if (h >= 7  && h < 9)  return "ny_pre";
  if (h >= 9  && h < 12) return "ny_am";
  if (h >= 12 && h < 16) return "ny_pm";
  return null;
}

// ── COMMANDS ──────────────────────────────────────────
function cmdStatus() {
  const result = runTV("status", 4000);
  const connected = !result.error && (result.connected || result.status === "ok" || !!result.chart);
  console.log(JSON.stringify({
    connected,
    error: result.error || null,
    hint: connected ? null : "Run launch_tradingview_debug.bat, then wait for TradingView to fully load."
  }));
}

function cmdRead() {
  const out = {
    connected: false, error: null,
    chart: null, quote: null, session: null,
    indicators_found: {},
    levels: null, orderblocks: null, fvgs: null,
    custom_levels: null,  // from DOL proxy indicator if active
    warnings: [],
  };

  // 1. Chart state
  const state = runTV("state", 6000);
  if (state.error) {
    out.error = "TradingView not connected. " + state.error;
    console.log(JSON.stringify(out)); return;
  }

  out.connected = true;
  out.chart = {
    symbol:    state.symbol    || state.chart?.symbol,
    timeframe: state.timeframe || state.chart?.timeframe,
  };
  out.indicators_found = categorizeIndicators(state.indicators || state.studies || []);

  // 2. Quote
  const quote = runTV("quote", 4000);
  if (!quote.error) {
    out.quote = {
      price:  safeNum(quote.close || quote.last || quote.price),
      time:   quote.time || quote.timestamp,
    };
    out.session = detectSession(out.quote.time);
  }
  const price = out.quote?.price;

  // 3. Timeframe check
  const tf = out.chart?.timeframe;
  const tfNum = parseInt(tf);
  if (!isNaN(tfNum) && tfNum >= 30) {
    out.warnings.push(
      `On ${tf}min timeframe. Killzones indicator only draws on ≤15min. ` +
      `Switch to 5min or 15min to read session levels, then switch back to ${tf}min to trade.`
    );
  }

  // 4. Read Killzones indicator
  const kzFilter = out.indicators_found.killzones;
  if (kzFilter) {
    // Read labels — the TFO indicator creates label.new() objects with the pivot text
    const lblData = runTV(`data labels --study "${kzFilter}"`, 6000);
    if (!lblData.error) {
      out.levels = parseKillzoneLabels(lblData.labels || []);
    }
    if (!out.levels || !Object.keys(out.levels).length) {
      out.warnings.push(
        "Killzones indicator active but no pivot labels found. " +
        "Ensure you're on ≤15min timeframe and 'Show Pivot Labels' is enabled in indicator settings."
      );
    }
  } else {
    out.warnings.push("ICT Killzones indicator not detected as active.");
  }

  // 5. Read DOL proxy indicator (if installed — see dol_proxy.pine)
  const dolProxyFilter = "DOL Levels"; // matches the proxy indicator title
  const allIndicators = state.indicators || state.studies || [];
  const dolProxy = allIndicators.find(i => (i.name || i.title || "").includes("DOL"));
  if (dolProxy) {
    const proxyLines = runTV(`data lines --study "${dolProxy.name || dolProxy.title}"`, 5000);
    const proxyLabels = runTV(`data labels --study "${dolProxy.name || dolProxy.title}"`, 5000);
    const customLevels = [];
    for (const lbl of (proxyLabels.labels || [])) {
      const p = safeNum(lbl.price || lbl.y);
      const t = (lbl.text || lbl.label || "").trim();
      if (p && t) customLevels.push({ label: t, price: p });
    }
    if (customLevels.length) out.custom_levels = customLevels;
  }

  // 6. OBs
  const obFilter = out.indicators_found.orderblock;
  if (obFilter && price) {
    const boxes = runTV(`data boxes --study "${obFilter}"`, 5000);
    if (!boxes.error) out.orderblocks = parseOBs(boxes.boxes || [], price);
  }

  // 7. FVGs
  const fvgFilter = out.indicators_found.fvg;
  if (fvgFilter && price) {
    const fLines = runTV(`data lines --study "${fvgFilter}"`, 5000);
    const fBoxes = runTV(`data boxes --study "${fvgFilter}"`, 5000);
    out.fvgs = parseFVGs(fLines.lines || [], fBoxes.boxes || [], price);
  }

  console.log(JSON.stringify(out));
}

function cmdScreenshot(savePath) {
  const target = savePath || path.join(__dirname, "images", `tv_${Date.now()}.png`);
  const result = runTV(["screenshot", "-r", "chart", "-o", target], 12000);
  if (result.error) {
    console.log(JSON.stringify({ success: false, error: result.error }));
    return;
  }
  const finalPath = result.path || result.file || target;
  if (finalPath && finalPath !== target && fs.existsSync(finalPath)) {
    try { fs.copyFileSync(finalPath, target); } catch {}
  }
  const exists = fs.existsSync(target);
  console.log(JSON.stringify({ success: exists, path: target, error: exists ? null : "File not found after screenshot" }));
}

// ── Entry ─────────────────────────────────────────────
const cmd = process.argv[2] || "read";
const arg = process.argv[3];
switch (cmd) {
  case "status":     cmdStatus();        break;
  case "read":       cmdRead();          break;
  case "screenshot": cmdScreenshot(arg); break;
  default: console.log(JSON.stringify({ error: `Unknown command: ${cmd}` }));
}
