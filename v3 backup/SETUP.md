# Trading Journal v3 — Setup Guide

## Install & Run (same as before)
```
pip install fastapi uvicorn python-multipart requests
python main.py
```
Then open: http://127.0.0.1:8000

---

## What's New in v3 (vs v2)

### Built from cross-referencing ALL your notes + ICT PDF

#### The 2/2 Rule is now the gate
Every log now tracks DOL + PA as the two primary conditions.
The pre-trade checklist reflects your exact process:
  4HR Bias → 1HR DOL → 15min Confluence → 5min Entry → PA Clear → MSS/CISD

#### Session-aware logging
- Session buttons: Asia / London / NY Pre (7-8:30) / NY AM (8:30-11) / NY PM
- Analytics tab breaks down win rate PER SESSION
- AI analysis knows which sessions you overtrade in

#### DOL Source tracking
Log where your DOL came from:
Asia High/Low, London High/Low, Prev Day High/Low, 4HR FVG, 1HR BSL/SSL, HOD/LOD

#### Liquidity Flow (HIGH vs LOW probability)
Core from your notes: ERL→IRL and IRL→ERL = HIGH PROB
Random FVG/OB without clear flow = LOW PROB
Analytics will show your win rate BY flow type — proving to you with data
whether respecting this rule matters.

#### Full Confluence Stack (multi-select)
OB, FVG, iFVG, Breaker Block, BPR, SMT Divergence, OTE,
CE (Consequent Encroachment), Std Dev Target, Volume Imbalance,
Premium/Discount, Midnight Open, Weekly Open

#### Emotional State tracking
😌 Calm / 😤 Slightly Off / 🔥 Tilt
Analytics shows your win rate by emotional state.
Over time this will show you exactly how much tilt costs you.

#### Trade Management Review (post-trade)
After exiting, review:
- Did volatility confirm within 10 minutes?
- Did you hold the trade 4+ minutes?
- Did you move BE correctly (10pts or past resistance)?
- Did you exit early?

#### Expanded Mistake Tags
FOMO, Overtrade, No DOL, No Confirmation, Incorrect Bias, Revenge Trade,
Tilt Trading, Early Exit, BE Too Soon, Overleverage, Strategy Hopping,
Impatience, No Good PA, Low Prob Setup, Wrong TF Entry, Rule Breaking

---

## Log Flow (still under 15 seconds for core logging)

FAST PATH (required minimum):
1. Ctrl+V paste screenshot
2. Click trade quality (A+ High Prob / Low Prob / Mistake)
3. Click direction (Long / Short)
4. Click result (Win / Loss / BE)
5. LOG TRADE

EXPANDED (open collapsibles when you have time):
- Setup Context: setup type, entry trigger, session, DOL source, flow, confluences
- Pre-Trade Checklist: 6 checkboxes from your process
- Trade Management: post-trade review of rules followed

---

## Analytics Shows
- Overall win rate
- A+ High Prob vs Low Prob vs Mistake win rate comparison
- Total PNL with daily bar chart
- Emotional state vs win rate (calm vs tilt trading performance)
- Liquidity flow win rate (ERL→IRL vs random)
- Most skipped pre-trade checklist steps (shows your process gaps)
- Trade management adherence %
- Setup type performance table
- Session performance table (which session is most profitable)
- Top 10 recurring mistakes

---

## Optional: Vision Analysis
Download llama3.2-vision (~6GB) for AI chart reading:
```
ollama pull llama3.2-vision
```
Toggle "AI analyze chart" before logging.
Does NOT affect your Obsidian qwen2.5 tagging.
