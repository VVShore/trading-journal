# Trading Journal v5 — Setup Guide

## Install & Run
```
pip install fastapi uvicorn python-multipart requests
python main.py
```
Open: http://127.0.0.1:8000

## Vision model (optional, lightweight)
v5 uses moondream (~1.7GB, runs on ~2-3GB RAM — fits 4GB constraint):
```
ollama pull moondream
```
Status shown in the app's system bar.

## Auto-start (Windows)
start_journal.bat is auto-generated on first run.
Press Windows+R → type shell:startup → copy start_journal.bat there.

---

## What Changed in v5

### New Universal Log Structure
Replaced ICT-specific buttons with a universal framework that works
regardless of strategy. The new 8-step flow:

1. Setup: Trend Cont. / Reversal / Range / Breakout / Liq. Sweep
2. Quality: A (clean) / B (valid) / C (forced)
3. Execution Score: 1-10
4. Entry Behavior: Decisive / Hesitation / Late / Early / Chase
5. Management: Followed Plan / Cut Early / Let Run / Overheld / Moved Stop / Scaled Poorly
6. Exit Type: Target Hit / Stopped Out / Manual Planned / Manual Emotional
7. Emotion: Calm / Focused / Fear / FOMO / Revenge / Overconfident / Frustrated
8. Result: R-multiple (+1R, -1R, +2R etc)

Optional (collapsible, doesn't slow down core logging):
- HTF bias alignment (Yes/No)
- Direction, Session, Liquidity Flow
- Position size
- Notes

### ADHD-Optimized Step Flow
- 8 numbered steps, each auto-expands after you make a selection
- After picking an option, the next step scrolls into view automatically
- Each step shows a summary of your choice in the header when done
- "Clear All" button always available to start over
- Trade counter shows current trade # and running R at top
- Screenshot shows a clean paste indicator, no required fields blocking you

### Vision Model Fix
- Switched from llama3.2-vision (6GB) to moondream (~1.7GB)
- Fits 4GB RAM easily
- System status bar shows exact ollama pull command if not downloaded
- Graceful fallback if vision fails — trade still logs, no crash

### Daily Summary (NEW)
End of session → "End Session — Generate Summary" button on Today tab.
Auto-calculates:
- Daily Grade (A/B/C) from execution scores + C-setup count + emotional trades
- Top 2 mistake types from the day's data
- Best setup (highest R)
- Biggest Leak (entries / management / psychology / overtrading)
- One Fix for Tomorrow (AI-generated, under 15 words)
Summary is saved and shows on future visits to Today tab.

### Analytics Rebuilt Around New Structure
- Quality A/B/C performance table (total R, win rate, avg R per grade)
- Setup type performance (which setups actually make money)
- Execution score bands vs R (does score 7+ actually win more?)
- Emotion vs win rate AND avg R (not just win rate)
- Entry behavior vs win rate + avg R
- Sequence analytics (C-setup rate after losses, win rate after loss)
- Cumulative R line chart
- Trade frequency bar chart

### Edge Formula Output
Once you have enough data, the Analytics tab shows:
"Edge: A-quality setups win X% · C-setups cost you XR"
This is the core goal: prove to yourself what actually works.

---

## Migrating from v4
v5 has a different trade structure (r_multiple instead of pnl, new fields).
Recommend fresh start — old v4 data is incompatible without migration.
Copy images/ folder if you want old screenshots.

## What was removed
- Heavy ICT-specific confluence buttons (moved to optional context)
- llama3.2-vision (too heavy, replaced with moondream)
- PNL tracking (replaced with R-multiples — cleaner for behavioral analysis)
  Note: You can still track PNL in Notes or position size fields
