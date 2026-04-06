# Trading Journal v8 — Setup

## Run (same as before)
```
pip install fastapi uvicorn python-multipart requests
python main.py
```
Open: http://127.0.0.1:8000

## What's new in v8

### Fixed
- History (Record) tab fully rebuilt — all badges render correctly
- Trade logging no longer requires R — Win/Loss/BE is enough to submit
- R can be filled from the Record tab after the fact (inline quick buttons)

### Result entry redesigned
- Win / Loss / BE are big prominent buttons (required to log)
- R amount (e.g. +1R, -1R) is optional — preset row below, or custom input
- "Fill later" note — if you skip R at log time, the Record tab shows R buttons on each trade for fast retroactive filling

### Read Chart panel clarified
- Shows: symbol, timeframe, any Pine labels near current price (session levels if on ≤15min), nearby OBs, nearby FVGs
- Removed: "suggested inputs" (was speculative)
- Honest warnings: tells you when levels couldn't be parsed and why
- Screenshot button: captures your TradingView chart, loads directly into the screenshot zone

### Read Chart — known limitations
- **Manually drawn trend lines CANNOT be read.** Only Pine Script objects (line.new, label.new, box.new) are accessible. Your manually drawn DOL lines are not readable.
- **Session levels only on ≤15min.** The Killzones indicator only draws Asia/London H/L labels at lower timeframes. On 1H or 4H, no labels are drawn by the indicator.
- **Label text is indicator-version specific.** If session levels don't appear, the indicator's label text doesn't match the expected patterns. Share your Pine Script and this can be fixed precisely.

---

## TradingView Debug Setup (for Read Chart)

### Step 1: Install tradingview-mcp
```
git clone https://github.com/tradesdontlie/tradingview-mcp.git
cd tradingview-mcp
npm install
npm link
```

### Step 2: Launch TradingView with debug
Double-click `launch_tradingview_debug.bat` (in this folder)

### Step 3: Use Read Chart
- Click "Read Chart" in the journal Log tab
- Shows your current chart context after ~10 seconds
- Click "📸 TV Screenshot" to capture your TradingView chart directly

---

## Indicator recommendation
Current slots: Killzones+Pivots + Orderblock
Recommended: Killzones+Pivots + FVG/iFVG (nephew sam)

Your entry model requires FVG confirmation. FVGs are harder to spot on 1m than OBs.
OBs are visible by eye (last candle before impulse).
When you upgrade to Essential (3 slots): add Orderblock as third.

---

## If you want to share Pine Script for better parsing
The session level parsing (Asia H/L, London H/L) depends entirely on
what text your indicator puts in its label.new() calls.
If you share the Pine Script for ICT Killzones Pivots, I can update
chart_bridge.js to match exactly, and levels will read reliably.
