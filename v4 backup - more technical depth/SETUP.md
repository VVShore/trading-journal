# Trading Journal v4 — Setup & Migration

## Fresh Install
```
pip install fastapi uvicorn python-multipart requests
python main.py
```
Open: http://127.0.0.1:8000

## Migrating from v3
v4 uses a NEW database schema. Two options:

Option A — Fresh start (recommended)
  Just run v4. New journal.db will be created.
  Copy your images/ folder over if you want old screenshots accessible.

Option B — Keep old data
  Copy your v3 journal.db into the v4 folder BEFORE first run.
  Run this migration SQL once (in python or sqlite3 CLI):
    ALTER TABLE trades ADD COLUMN account_id INTEGER DEFAULT 1;
    ALTER TABLE trades ADD COLUMN trade_num_session INTEGER DEFAULT 1;
    ALTER TABLE trades ADD COLUMN position_size REAL;
    ALTER TABLE trades ADD COLUMN instrument TEXT DEFAULT 'MNQ';
    ALTER TABLE trades ADD COLUMN hold_time_candles INTEGER;
  Then start v4 normally.

---

## Auto-start on Windows boot
v4 auto-generates start_journal.bat on first run.
1. Press Windows+R → type shell:startup → press Enter
2. Copy start_journal.bat shortcut into that folder
Done. Journal starts automatically on every boot.

---

## What's New in v4

### Multi-Account Support
- Account selector in top bar (dropdown)
- Create accounts for different prop firms or personal accounts
- All analytics filterable per account or "combined" (account_id=0)
- Deleting an account only works if it has no trades (data safety)
- Account resets: just create a new account. Old data stays forever.

### System Status Bar (top of Log page)
- Shows if Ollama is running
- Shows if qwen2.5 (text model) is available
- Shows if llama3.2-vision is downloaded
- If vision not available: shows exact command to download it

### Vision Model Fix
- /test-vision endpoint checks all models before you log
- Better error messages when vision fails (shows in UI, not just console)
- Graceful fallback: if vision fails, trade still logs normally

### Trade # in Session (auto)
- "Trade 1 of 3 today" shown at top of log
- Color-coded: blue=1, yellow=2, red=3+ (over limit warning)
- Auto-calculated server-side, no input needed

### Position Size Tracking
- Size input + instrument selector (MNQ/NQ/ES/MES/YM/CL/GC)
- Analytics: win rate by size, mistake rate by size, PNL by size
- Reveals overleverage patterns (e.g. win rate drops when size > 1)

### Notes Field
- Optional text area at bottom of log
- Shows in history cards under the trade

### Live Sync
- Today tab auto-refreshes every 30 seconds when active
- Analytics refresh on tab switch

### Sequence Analytics (NEW)
- Win rate after a loss (do you revenge trade or bounce back?)
- Mistake rate after 1 consecutive loss
- Mistake rate after 2 consecutive losses
- Average PNL of the trade taken after a loss
- Proves to you with data whether losses trigger rule-breaking

### Position Sizing Analytics (NEW)
- Win rate per position size (e.g. 1 MNQ vs 2 MNQ vs 3 MNQ)
- Mistake rate per size (does oversize trigger mistakes?)
- PNL per size bucket

### Hold Time Analytics (NEW)
- Avg hold time of wins vs losses (in minutes)
- Tracks against the 4-minute rule
- AI analysis now includes sizing and sequence context

### Graphs (NEW)
- Cumulative PnL line chart (per account or combined)
- Trade frequency bar chart per day (red=4+, yellow=3, blue=<3)
- Both dynamically update with period selector (7D/30D/90D/All)

### History Improvements
- "All accounts" checkbox to see all trades across accounts
- Account badge shown when viewing all accounts
- Trade # badge on every card
- Position size badge
- Notes displayed inline
- Fullscreen click on any screenshot

### AI Analysis Improvements
- Now includes position size data in analysis
- Includes sequence data (what happens after losses)
- More specific to your ICT methodology

---

## Fast log preserved
Minimum required clicks:
1. Paste screenshot (Ctrl+V)
2. Select quality (A+/Low/Mistake)
3. Direction + Result
4. LOG TRADE

Everything else is optional. Collapsibles keep the UI clean.
