#!/usr/bin/env node
/**
 * chart_bridge.js
 * Called by the journal's FastAPI backend via subprocess.
 * Reads whatever indicators are currently active on your TradingView chart
 * and returns structured data for pre-populating the trade log.
 *
 * Requirements:
 *   - tradingview-mcp installed and npm-linked (tv CLI available)
 *   - TradingView Desktop running with --remote-debugging-port=9222
 *
 * Usage:
 *   node chart_bridge.js [read|screenshot|status]
 */

const { execSync, spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");

// ── Config ────────────────────────────────────────────
const TV_CLI = "tv"; // If npm link worked; fallback below
const CDP_PORT = 9222;
const TIMEOUT_MS = 8000;

// ── Helpers ───────────────────────────────────────────
function runTV(args, timeoutMs = TIMEOUT_MS) {
  try {
    const result = spawnSync(TV_CLI, args.split(" "), {
      timeout: timeoutMs,
      encoding: "utf8",
      env: { ...process.env, PORT: CDP_PORT },
    });

    if (result.error) throw result.error;
    if (result.status !== 0) {
      // Try fallback path if tv CLI not in PATH
      throw new Error(result.stderr || "Non-zero exit");
    }
    return JSON.parse(result.stdout || "{}");
  } catch (err) {
    // Fallback: try node directly if npm link failed
    try {
      const fallbackPaths = [
        path.join(__dirname, "tradingview-mcp", "src", "cli", "index.js"),
        path.join(process.env.USERPROFILE || process.env.HOME, "tradingview-mcp", "src", "cli", "index.js"),
        path.join(process.env.USERPROFILE || process.env.HOME, "Documents", "tradingview-mcp", "src", "cli", "index.js"),
      ];
      for (const fp of fallbackPaths) {
        if (fs.existsSync(fp)) {
          const r = spawnSync("node", [fp, ...args.split(" ")], {
            timeout: timeoutMs,
            encoding: "utf8",
          });
          if (r.status === 0) return JSON.parse(r.stdout || "{}");
        }
      }
    } catch {}
    return { error: err.message };
  }
}

function safeNum(val) {
  const n = parseFloat(val);
  return isNaN(n) ? null : n;
}

// ── Indicator name matching ───────────────────────────
// Flexible matching — works regardless of exact indicator name/version
const INDICATOR_PATTERNS = {
  killzones: ["killzone", "kill zone", "ict kill", "session"],
  pivots:    ["pivot", "asia", "london"],
  orderblock:["order block", "orderblock", "ob ", "mtf s&d"],
  fvg:       ["fvg", "fair value gap", "nephew", "imbalance"],
  smt:       ["smt", "smart money technique", "divergence"],
  volume_profile: ["volume profile", "vol profile", "vpvr"],
};

function matchIndicator(name, type) {
  const lower = name.toLowerCase();
  return INDICATOR_PATTERNS[type]?.some(p => lower.includes(p)) || false;
}

function categorizeIndicators(indicators) {
  const found = {};
  for (const ind of indicators) {
    for (const [type, _] of Object.entries(INDICATOR_PATTERNS)) {
      if (matchIndicator(ind.name || ind.title || "", type)) {
        found[type] = ind.name || ind.title || ind.id;
      }
    }
  }
  return found;
}

// ── Session detection from time ───────────────────────
function detectSession(timeStr) {
  if (!timeStr) return null;
  const [hStr, mStr] = (timeStr || "").split(":");
  const h = parseInt(hStr);
  if (isNaN(h)) return null;
  if (h >= 20 || h < 3)  return "asia";
  if (h >= 3  && h < 8)  return "london";
  if (h >= 7  && h < 9)  return "ny_pre";
  if (h >= 9  && h < 12) return "ny_am";
  if (h >= 12 && h < 16) return "ny_pm";
  return null;
}

// ── Parse level labels ────────────────────────────────
function parseLevelLabels(labels) {
  const levels = {
    asia_high: null, asia_low: null,
    london_high: null, london_low: null,
    pdh: null, pdl: null,
    custom: []
  };
  for (const lbl of labels || []) {
    const text = (lbl.text || lbl.label || "").toLowerCase();
    const price = safeNum(lbl.price || lbl.y);
    if (!price) continue;
    if (text.includes("asia") && text.includes("high"))   levels.asia_high   = price;
    else if (text.includes("asia") && text.includes("low"))  levels.asia_low    = price;
    else if (text.includes("london") && text.includes("high"))levels.london_high = price;
    else if (text.includes("london") && text.includes("low")) levels.london_low  = price;
    else if (text.includes("pdh") || (text.includes("prev") && text.includes("high")))
      levels.pdh = price;
    else if (text.includes("pdl") || (text.includes("prev") && text.includes("low")))
      levels.pdl = price;
    else levels.custom.push({ label: lbl.text || lbl.label, price });
  }
  return levels;
}

// ── Parse OB boxes ────────────────────────────────────
function parseOBBoxes(boxes, currentPrice) {
  if (!boxes || !boxes.length) return { bullish: [], bearish: [] };
  const bullish = [], bearish = [];
  for (const box of boxes) {
    const top = safeNum(box.top || box.y2);
    const bot = safeNum(box.bottom || box.y1);
    if (!top || !bot) continue;
    const mid = (top + bot) / 2;
    const distPct = currentPrice ? Math.abs(currentPrice - mid) / currentPrice * 100 : 999;
    if (distPct > 2) continue; // Only nearby OBs (within 2%)
    if (mid < currentPrice) bullish.push({ top, bottom: bot, mid, dist_pct: distPct });
    else bearish.push({ top, bottom: bot, mid, dist_pct: distPct });
  }
  return {
    bullish: bullish.sort((a,b) => a.dist_pct - b.dist_pct).slice(0, 3),
    bearish: bearish.sort((a,b) => a.dist_pct - b.dist_pct).slice(0, 3),
  };
}

// ── Parse FVG lines/boxes ─────────────────────────────
function parseFVGs(lines, boxes, currentPrice) {
  const fvgs = [];
  for (const item of [...(lines || []), ...(boxes || [])]) {
    const price = safeNum(item.price || item.top || item.y);
    if (!price) continue;
    const distPct = currentPrice ? Math.abs(currentPrice - price) / currentPrice * 100 : 999;
    if (distPct > 1.5) continue;
    fvgs.push({ price, dist_pct: distPct, type: item.type || "fvg" });
  }
  return fvgs.sort((a,b) => a.dist_pct - b.dist_pct).slice(0, 4);
}

// ── Infer bias from levels ────────────────────────────
function inferBias(currentPrice, levels, obs) {
  // Simple: is price above or below session levels?
  const refs = [
    levels.asia_high, levels.asia_low,
    levels.london_high, levels.london_low
  ].filter(Boolean);

  if (!refs.length || !currentPrice) return null;
  const above = refs.filter(r => currentPrice > r).length;
  const below = refs.filter(r => currentPrice < r).length;
  if (above > below * 1.5) return "bullish";
  if (below > above * 1.5) return "bearish";
  return null;
}

// ── MAIN COMMANDS ─────────────────────────────────────
async function cmdStatus() {
  // Quick health check — is TradingView connected?
  const state = runTV("status", 3000);
  const connected = !state.error && (state.connected || state.status === "connected" || state.chart);
  console.log(JSON.stringify({
    connected,
    error: state.error || null,
    hint: !connected ? "Launch TradingView with: scripts\\launch_tv_debug.bat" : null
  }));
}

async function cmdRead() {
  const output = {
    connected: false,
    error: null,
    chart: null,
    quote: null,
    session: null,
    indicators_found: {},
    levels: null,
    orderblocks: null,
    fvgs: null,
    suggested: {
      direction: null,
      session: null,
      dol_source: null,
      liq_flow: null,
      notes: null,
    }
  };

  // 1. Chart state — symbol, timeframe, active indicators
  const state = runTV("state", 5000);
  if (state.error) {
    output.error = "TradingView not connected. " + (state.error || "");
    console.log(JSON.stringify(output));
    return;
  }

  output.connected = true;
  output.chart = {
    symbol:    state.symbol || state.chart?.symbol,
    timeframe: state.timeframe || state.chart?.timeframe,
    type:      state.type || state.chart?.type,
  };

  // Categorize active indicators
  const indicators = state.indicators || state.studies || [];
  output.indicators_found = categorizeIndicators(indicators);

  // 2. Current quote
  const quote = runTV("quote", 4000);
  if (!quote.error) {
    output.quote = {
      price:  safeNum(quote.close || quote.last || quote.price),
      open:   safeNum(quote.open),
      high:   safeNum(quote.high),
      low:    safeNum(quote.low),
      volume: safeNum(quote.volume),
      time:   quote.time || quote.timestamp,
    };
  }

  const currentPrice = output.quote?.price;

  // 3. Detect session from time
  if (output.quote?.time) {
    const timeStr = new Date(output.quote.time).toTimeString().slice(0,5);
    output.session = detectSession(timeStr);
    output.suggested.session = output.session;
  }

  // 4. Read killzones/pivots → session levels
  if (output.indicators_found.killzones || output.indicators_found.pivots) {
    const filter = output.indicators_found.killzones || output.indicators_found.pivots;
    const lblData = runTV(`data labels --study "${filter}"`, 5000);
    const lineData = runTV(`data lines --study "${filter}"`, 5000);

    const allLabels = [
      ...(lblData.labels || []),
      ...(lineData.lines || []),
    ];
    output.levels = parseLevelLabels(allLabels);

    // Determine most likely DOL source
    if (currentPrice && output.levels) {
      const dols = {
        asia_high:   output.levels.asia_high,
        asia_low:    output.levels.asia_low,
        london_high: output.levels.london_high,
        london_low:  output.levels.london_low,
        pdh:         output.levels.pdh,
        pdl:         output.levels.pdl,
      };
      let closestDol = null, closestDist = Infinity;
      for (const [key, price] of Object.entries(dols)) {
        if (!price) continue;
        const dist = Math.abs(currentPrice - price);
        if (dist < closestDist) { closestDist = dist; closestDol = key; }
      }
      output.suggested.dol_source = closestDol;
    }
  }

  // 5. Read orderblocks
  if (output.indicators_found.orderblock) {
    const boxData = runTV(`data boxes --study "${output.indicators_found.orderblock}"`, 5000);
    if (!boxData.error) {
      output.orderblocks = parseOBBoxes(boxData.boxes || [], currentPrice);
    }
  }

  // 6. Read FVGs
  if (output.indicators_found.fvg) {
    const lineData = runTV(`data lines --study "${output.indicators_found.fvg}"`, 5000);
    const boxData  = runTV(`data boxes --study "${output.indicators_found.fvg}"`, 5000);
    output.fvgs = parseFVGs(
      lineData.lines || [], boxData.boxes || [], currentPrice
    );
  }

  // 7. Infer bias
  if (output.levels) {
    output.suggested.direction = inferBias(currentPrice, output.levels, output.orderblocks);
  }

  // 8. Infer liq flow (ERL→IRL or IRL→ERL based on price vs session levels)
  if (output.levels && currentPrice) {
    const extLevels = [output.levels.asia_high, output.levels.london_high, output.levels.pdh].filter(Boolean);
    const intLevels = [output.levels.asia_low, output.levels.london_low, output.levels.pdl].filter(Boolean);
    const nearExt = extLevels.some(l => Math.abs(currentPrice - l) / currentPrice < 0.003);
    const nearInt = intLevels.some(l => Math.abs(currentPrice - l) / currentPrice < 0.003);
    if (nearExt) output.suggested.liq_flow = "erl_to_irl";
    else if (nearInt) output.suggested.liq_flow = "irl_to_erl";
  }

  // 9. Build a brief notes string
  const noteParts = [];
  if (output.chart?.symbol) noteParts.push(`${output.chart.symbol} ${output.chart.timeframe}`);
  if (output.levels?.asia_high) noteParts.push(`Asia H: ${output.levels.asia_high}`);
  if (output.levels?.london_high) noteParts.push(`London H: ${output.levels.london_high}`);
  if (output.fvgs?.length) noteParts.push(`${output.fvgs.length} FVG(s) nearby`);
  if (noteParts.length) output.suggested.notes = noteParts.join(" · ");

  console.log(JSON.stringify(output));
}

async function cmdScreenshot(outputPath) {
  // Take screenshot and save to journal images folder
  const savePath = outputPath || path.join(__dirname, "images", `tv_${Date.now()}.png`);

  // Try to get screenshot via tv CLI
  try {
    const result = spawnSync(TV_CLI, ["screenshot", "-r", "chart", "-o", savePath], {
      timeout: 10000,
      encoding: "utf8",
    });
    if (result.status === 0) {
      console.log(JSON.stringify({ success: true, path: savePath }));
      return;
    }
  } catch {}

  // Fallback: screenshot without path arg
  const result = runTV("screenshot -r chart", 10000);
  if (result.error) {
    console.log(JSON.stringify({ success: false, error: result.error }));
  } else {
    // Move to images dir if path returned
    const srcPath = result.path || result.file;
    if (srcPath && fs.existsSync(srcPath)) {
      fs.copyFileSync(srcPath, savePath);
      console.log(JSON.stringify({ success: true, path: savePath }));
    } else {
      console.log(JSON.stringify({ success: true, path: srcPath || savePath, data: result }));
    }
  }
}

// ── Entry point ───────────────────────────────────────
const cmd = process.argv[2] || "read";
const arg = process.argv[3];

(async () => {
  switch (cmd) {
    case "status":     await cmdStatus(); break;
    case "read":       await cmdRead(); break;
    case "screenshot": await cmdScreenshot(arg); break;
    default:
      console.log(JSON.stringify({ error: `Unknown command: ${cmd}` }));
  }
})();
