# TradingView Chart Bridge — Setup Guide

## What This Does
Adds a "Read Chart" button to your trading journal that:
- Reads your currently active TradingView indicators (Killzones/Pivots, Orderblocks, FVG)
- Extracts: Asia/London session H/L, nearby OB zones, nearby FVGs, current price, symbol, timeframe
- Suggests: direction, session, DOL source, liquidity flow for your trade log
- Takes a TradingView screenshot on demand (1-click, no Ctrl+V needed)
- Works with your current free plan (2 indicators) — auto-scales as you add more

## Requirements
- Node.js 18+ (free): https://nodejs.org — download LTS version
- TradingView Desktop (you already have it)
- tradingview-mcp (free, local): 5-minute install below
- Your existing journal (v7) already running

## Does This Require Claude Code or any LLM subscription?
NO. The chart reading uses only the `tv` CLI commands locally.
No AI subscription needed. qwen2.5 in Ollama handles analysis as before.

---

## Step 1: Install Node.js
Download from https://nodejs.org (click "LTS" version)
Install normally. Verify in Command Prompt:
```
node --version
```
Should show v18+ or v20+

---

## Step 2: Install tradingview-mcp
In Command Prompt:
```
git clone https://github.com/tradesdontlie/tradingview-mcp.git
cd tradingview-mcp
npm install
npm link
```
`npm link` makes the `tv` command available globally.
Verify: `tv --help` should show available commands.

If `git` isn't installed: https://git-scm.com/downloads

---

## Step 3: Place bridge files in your journal folder
Copy these files into the SAME folder as your `main.py`:
  - chart_bridge.js
  - launch_tradingview_debug.bat

Your folder should look like:
```
trading_journal_v7/
  main.py
  index.html
  chart_bridge.js           ← new
  launch_tradingview_debug.bat  ← new
  journal.db
  images/
```

---

## Step 4: Add bridge endpoints to main.py
Open main.py and add these imports near the top (after existing imports):
```python
import subprocess
import os
```

Then add this constant near your other config:
```python
BRIDGE_SCRIPT = os.path.join(os.path.dirname(__file__), "chart_bridge.js")
BRIDGE_TIMEOUT = 20
```

Then paste the entire contents of `chart_bridge_endpoints.py` into main.py
(anywhere between your tag endpoint and your trade endpoint is fine).

---

## Step 5: Add the Read Chart UI to index.html
Open index.html and find the line:
```html
<div class="state-grid" id="state-grid"></div>
```

Add the chart panel HTML just BEFORE that line.

Add the CSS from chart_panel_addon.html into the existing <style> tag.

Add the JavaScript from chart_panel_addon.html into the existing <script> tag
(near the bottom, before the closing </script>).

---

## Step 6: Launch TradingView with Debug Port
IMPORTANT: TradingView must be launched with a special debug flag.
You cannot use a normally-launched TradingView instance.

Double-click: `launch_tradingview_debug.bat`
This auto-detects your TradingView installation and launches it correctly.

After this, TradingView looks and works exactly the same.
The debug port just allows our bridge to read the chart.

You must do this every time you want to use the "Read Chart" feature.
To automate: add the .bat to your Windows startup folder.

---

## Step 7: Test
1. Launch TradingView via the .bat file
2. Open your journal (http://127.0.0.1:8000)
3. The top of the Log page shows: "TradingView connected" (green dot)
4. Click "Read Chart"
5. After ~10-15 seconds, the chart data panel appears

---

## How It Works in Practice

Before a trade:
1. Set up your TradingView chart as normal (1H for bias, switch to 1m/5m for entry)
2. Open the journal
3. Click "Read Chart" — it reads your active Killzones and any other enabled indicators
4. You see: Asia H/L, London H/L, current price, any nearby OBs/FVGs
5. Click "Apply to Log →" — direction, session, DOL source fill automatically
6. Click "📸 Screenshot" if you want your chart captured (or paste your Tradovate screenshot)
7. Fill quality, R, entry/mgmt buttons — most context is already filled
8. Log Trade

---

## What Reads From Each Indicator

### Killzones & Pivots (ICT Killzones Pivots)
Reads: Asia High, Asia Low, London High, London Low, Prev Day High/Low
Used for: DOL source suggestion, liquidity flow direction, session detection

### MTF S&D Orderblocks
Reads: Nearby OB zones (top/bottom price) within 2% of current price
Displayed as: Bullish OBs / Bearish OBs with price ranges

### FVG/IFVG (nephew sam)
Reads: FVG price levels within 1.5% of current price
Displayed as: FVG list with distance from current price

### Any additional indicators (when you upgrade)
The bridge auto-detects by name pattern — no config needed.
SMT, Volume Profile, etc. will appear in the "active indicators" chip row.

---

## Indicator Slot Recommendation

Current: Killzones+Pivots (1) + Orderblock (2)
Recommended: Killzones+Pivots (1) + FVG/iFVG nephew sam (2)

Reason: Your entry model requires FVG confirmation (Liq Sweep + MSS + FVG).
FVGs are harder to spot visually on 1m than OBs.
OBs are identifiable by eye (last down candle before impulse) — no indicator needed.
The FVG indicator gives you precision on iFVG inversions which are your best entries.
When you upgrade (3 slots): add the Orderblock indicator back as slot 3.

---

## Limitations (honest)

1. TradingView must be launched via debug .bat — cannot read a normally-launched instance
2. CDP can break on TradingView updates — fix: relaunch with .bat, usually resolves itself
3. Indicator names must fuzzy-match known patterns — if a new indicator doesn't appear,
   edit INDICATOR_PATTERNS in chart_bridge.js to add its name
4. Session level detection depends on how the Killzones indicator draws labels —
   if your version uses different text for the labels, levels may not parse
5. The bridge cannot read your Tradovate account — only TradingView chart drawings
6. Free plan: only active indicators are read; broken/disabled ones are skipped automatically

---

## Troubleshooting

**"TradingView not connected"**
→ Launch TradingView via launch_tradingview_debug.bat, not normally

**"Read timed out"**
→ TradingView might be loading; wait for chart to fully load then retry

**No levels showing**
→ Make sure Killzones indicator is active and your timeframe shows Asia/London candles

**`tv` command not found**
→ Run `npm link` again from inside the tradingview-mcp folder
→ Or: find chart_bridge.js and set the fallback path in the BRIDGE_SCRIPT config

**Node.js not found**
→ Install from nodejs.org and restart Command Prompt
