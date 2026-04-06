"""
Trading Journal v3 - FastAPI Backend
Full ICT/SMC methodology integration
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import sqlite3, shutil, requests, json, base64, re
from datetime import datetime, date
from pathlib import Path
from typing import Optional

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_PATH = "journal.db"
IMAGES_DIR = Path("images")
IMAGES_DIR.mkdir(exist_ok=True)
OLLAMA_URL = "http://localhost:11434/api/generate"
TEXT_MODEL = "qwen2.5"
VISION_MODEL = "llama3.2-vision"

# ── Database ──────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            image_path TEXT,

            -- Core classification
            probability TEXT,       -- 'high_prob', 'low_prob', 'mistake'
            setup_type TEXT,        -- mss_model, trend_amd, 5m_fvg, etc.
            entry_trigger TEXT,     -- liq_sweep_mss, fvg_retest, ifvg_inv, cisd, starter
            direction TEXT,         -- long, short

            -- Session & Market Context
            session TEXT,           -- asia, london, ny_pre, ny_am, ny_pm
            dol_source TEXT,        -- asia_high, asia_low, london_high, london_low, pdh, pdl, 4hr_fvg, 1hr_bsl, 1hr_ssl
            liq_flow TEXT,          -- erl_to_irl, irl_to_erl, continuation

            -- Confluence stack (comma-separated)
            confluences TEXT,

            -- Pre-trade checklist
            chk_4hr_bias INTEGER DEFAULT 0,
            chk_1hr_dol INTEGER DEFAULT 0,
            chk_15min_conf INTEGER DEFAULT 0,
            chk_5min_entry INTEGER DEFAULT 0,
            chk_pa_clear INTEGER DEFAULT 0,
            chk_mss_cisd INTEGER DEFAULT 0,

            -- Trade management review (post-trade)
            mgmt_volatility_10min INTEGER DEFAULT 0,  -- volatility confirmed within 10min
            mgmt_held_4min INTEGER DEFAULT 0,         -- held trade at least 4min
            mgmt_be_correct INTEGER DEFAULT 0,        -- moved BE correctly (10pts or past resistance)
            mgmt_early_exit INTEGER DEFAULT 0,        -- exited early (flag)

            -- Emotional state
            emotional_state TEXT,   -- calm, slightly_emotional, tilt

            -- Mistake tags
            mistake_tags TEXT,

            -- Result
            result TEXT,            -- win, loss, be
            pnl REAL,

            -- AI extracted
            ai_price TEXT,
            ai_time TEXT,
            ai_duration TEXT,
            ai_direction TEXT,
            ai_pnl TEXT,
            ai_notes TEXT,

            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

app.mount("/images", StaticFiles(directory="images"), name="images")

@app.get("/")
def root(): return FileResponse("index.html")

# ── Tags ──────────────────────────────────────────────
@app.get("/tags")
def get_tags():
    return {
        "probability": [
            {"id": "high_prob", "label": "A+ High Prob", "type": "green"},
            {"id": "low_prob",  "label": "Low Prob",     "type": "yellow"},
            {"id": "mistake",   "label": "Mistake",      "type": "red"}
        ],
        "setup_type": [
            {"id": "mss_model",   "label": "MSS Model"},
            {"id": "trend_amd",   "label": "Trend / AMD"},
            {"id": "5m_fvg",      "label": "5m FVG"},
            {"id": "5m_ifvg",     "label": "5m iFVG"},
            {"id": "1m_fvg",      "label": "1m FVG (HiConv)"},
            {"id": "1m_ifvg",     "label": "1m iFVG (HiConv)"},
            {"id": "mmxm_buy",    "label": "MMXM Buy Model"},
            {"id": "mmxm_sell",   "label": "MMXM Sell Model"},
        ],
        "entry_trigger": [
            {"id": "liq_sweep_mss", "label": "Liq Sweep + MSS"},
            {"id": "cisd",          "label": "CISD"},
            {"id": "fvg_retest",    "label": "FVG Retest"},
            {"id": "ifvg_inv",      "label": "iFVG Inversion"},
            {"id": "ob_tap",        "label": "OB Tap"},
            {"id": "breaker_tap",   "label": "Breaker Block Tap"},
            {"id": "starter_pos",   "label": "Starter Position"},
            {"id": "smt_div",       "label": "SMT Divergence"},
        ],
        "session": [
            {"id": "asia",    "label": "Asia"},
            {"id": "london",  "label": "London"},
            {"id": "ny_pre",  "label": "NY Pre (7-8:30)"},
            {"id": "ny_am",   "label": "NY AM (8:30-11)"},
            {"id": "ny_pm",   "label": "NY PM (12-2)"},
        ],
        "dol_source": [
            {"id": "asia_high",   "label": "Asia High"},
            {"id": "asia_low",    "label": "Asia Low"},
            {"id": "london_high", "label": "London High"},
            {"id": "london_low",  "label": "London Low"},
            {"id": "pdh",         "label": "Prev Day High"},
            {"id": "pdl",         "label": "Prev Day Low"},
            {"id": "4hr_fvg",     "label": "4HR FVG"},
            {"id": "1hr_bsl",     "label": "1HR BSL"},
            {"id": "1hr_ssl",     "label": "1HR SSL"},
            {"id": "hod",         "label": "HOD"},
            {"id": "lod",         "label": "LOD"},
        ],
        "liq_flow": [
            {"id": "erl_to_irl",    "label": "ERL → IRL (High Prob)"},
            {"id": "irl_to_erl",    "label": "IRL → ERL (High Prob)"},
            {"id": "continuation",  "label": "Continuation"},
            {"id": "random",        "label": "No Clear Flow (Low Prob)"},
        ],
        "confluences": [
            {"id": "ob",            "label": "Order Block"},
            {"id": "fvg",           "label": "FVG"},
            {"id": "ifvg",          "label": "iFVG"},
            {"id": "breaker",       "label": "Breaker Block"},
            {"id": "bpr",           "label": "BPR"},
            {"id": "smt",           "label": "SMT Divergence"},
            {"id": "ote",           "label": "OTE"},
            {"id": "ce",            "label": "CE (Consec. Encroach)"},
            {"id": "std_dev",       "label": "Std Dev Target"},
            {"id": "vol_imb",       "label": "Volume Imbalance"},
            {"id": "premium_disc",  "label": "Premium / Discount"},
            {"id": "midnight_open", "label": "Midnight Open"},
            {"id": "weekly_open",   "label": "Weekly Open"},
        ],
        "emotional_state": [
            {"id": "calm",              "label": "😌 Calm"},
            {"id": "slightly_emotional","label": "😤 Slightly Off"},
            {"id": "tilt",              "label": "🔥 Tilt"},
        ],
        "mistake_tags": [
            {"id": "fomo",          "label": "FOMO"},
            {"id": "overtrade",     "label": "Overtrade"},
            {"id": "no_dol",        "label": "No DOL"},
            {"id": "no_confirmation","label": "No Confirmation"},
            {"id": "incorrect_bias","label": "Incorrect Bias"},
            {"id": "revenge",       "label": "Revenge Trade"},
            {"id": "tilt_trading",  "label": "Tilt Trading"},
            {"id": "early_exit",    "label": "Early Exit"},
            {"id": "be_too_soon",   "label": "BE Too Soon"},
            {"id": "overleverage",  "label": "Overleverage"},
            {"id": "strat_hop",     "label": "Strategy Hopping"},
            {"id": "impatience",    "label": "Impatience"},
            {"id": "no_pa",         "label": "No Good PA"},
            {"id": "low_prob_setup","label": "Low Prob Setup"},
            {"id": "wrong_tf",      "label": "Wrong TF Entry"},
            {"id": "rule_break",    "label": "Rule Breaking"},
        ]
    }

# ── Vision ────────────────────────────────────────────
def analyze_chart(image_path: str) -> dict:
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        prompt = """Analyze this Tradovate 1-minute futures chart. Respond ONLY with JSON:
{
  "price": "entry price visible on chart or from execution line",
  "time": "time of execution (HH:MM format)",
  "duration_candles": integer number of candles trade lasted,
  "direction": "long or short",
  "pnl": "PNL if visible bottom-left e.g. +$240 or -$80",
  "session": "asia/london/ny_pre/ny_am/ny_pm based on time",
  "notes": "one notable observation about price action"
}
Use null for anything you cannot determine."""
        resp = requests.post(OLLAMA_URL, json={
            "model": VISION_MODEL, "prompt": prompt,
            "images": [img_b64], "stream": False,
            "options": {"temperature": 0.1}
        }, timeout=60)
        result = resp.json().get("response", "")
        m = re.search(r'\{.*?\}', result, re.DOTALL)
        return json.loads(m.group()) if m else {}
    except Exception as e:
        print(f"Vision error: {e}")
        return {}

# ── Log trade ─────────────────────────────────────────
@app.post("/trade")
async def log_trade(
    probability: Optional[str]      = Form(None),
    setup_type: Optional[str]       = Form(None),
    entry_trigger: Optional[str]    = Form(None),
    direction: Optional[str]        = Form(None),
    session: Optional[str]          = Form(None),
    dol_source: Optional[str]       = Form(None),
    liq_flow: Optional[str]         = Form(None),
    confluences: Optional[str]      = Form(None),
    chk_4hr_bias: int               = Form(0),
    chk_1hr_dol: int                = Form(0),
    chk_15min_conf: int             = Form(0),
    chk_5min_entry: int             = Form(0),
    chk_pa_clear: int               = Form(0),
    chk_mss_cisd: int               = Form(0),
    mgmt_volatility_10min: int      = Form(0),
    mgmt_held_4min: int             = Form(0),
    mgmt_be_correct: int            = Form(0),
    mgmt_early_exit: int            = Form(0),
    emotional_state: Optional[str]  = Form(None),
    mistake_tags: Optional[str]     = Form(None),
    result: Optional[str]           = Form(None),
    pnl: Optional[float]            = Form(None),
    notes: Optional[str]            = Form(None),
    run_vision: int                 = Form(0),
    image: Optional[UploadFile]     = File(None)
):
    timestamp = datetime.now().isoformat()
    image_path = None
    ai_data = {}

    if image and image.filename:
        ext = Path(image.filename).suffix or ".png"
        fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        image_path = str(IMAGES_DIR / fname)
        with open(image_path, "wb") as f:
            shutil.copyfileobj(image.file, f)
        if run_vision:
            ai_data = analyze_chart(image_path)

    conn = get_db()
    conn.execute("""
        INSERT INTO trades (
            timestamp, image_path, probability, setup_type, entry_trigger, direction,
            session, dol_source, liq_flow, confluences,
            chk_4hr_bias, chk_1hr_dol, chk_15min_conf, chk_5min_entry, chk_pa_clear, chk_mss_cisd,
            mgmt_volatility_10min, mgmt_held_4min, mgmt_be_correct, mgmt_early_exit,
            emotional_state, mistake_tags, result, pnl, notes,
            ai_price, ai_time, ai_duration, ai_direction, ai_pnl, ai_notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        timestamp, image_path, probability, setup_type, entry_trigger, direction,
        session or ai_data.get("session"), dol_source, liq_flow, confluences,
        chk_4hr_bias, chk_1hr_dol, chk_15min_conf, chk_5min_entry, chk_pa_clear, chk_mss_cisd,
        mgmt_volatility_10min, mgmt_held_4min, mgmt_be_correct, mgmt_early_exit,
        emotional_state, mistake_tags, result, pnl, notes,
        ai_data.get("price"), ai_data.get("time"), str(ai_data.get("duration_candles","")),
        ai_data.get("direction") or direction, ai_data.get("pnl"), ai_data.get("notes")
    ))
    conn.commit()
    conn.close()
    return {"success": True, "ai_data": ai_data}

# ── Today stats ───────────────────────────────────────
@app.get("/stats/today")
def today_stats():
    today = date.today().isoformat()
    conn = get_db()
    rows = conn.execute("SELECT * FROM trades WHERE date(timestamp)=? ORDER BY timestamp ASC", (today,)).fetchall()
    trades = [dict(r) for r in rows]
    conn.close()

    total = len(trades)
    high_prob = sum(1 for t in trades if t["probability"] == "high_prob")
    mistakes  = sum(1 for t in trades if t["probability"] == "mistake")
    wins      = sum(1 for t in trades if t["result"] == "win")
    losses    = sum(1 for t in trades if t["result"] == "loss")
    pnl       = round(sum(t["pnl"] or 0 for t in trades), 2)

    # Checklist adherence
    def pct(key): return round(sum(1 for t in trades if t[key]) / total * 100) if total else 0
    checklist = {
        "4HR Bias": pct("chk_4hr_bias"),
        "1HR DOL": pct("chk_1hr_dol"),
        "15min Conf": pct("chk_15min_conf"),
        "5min Entry": pct("chk_5min_entry"),
        "PA Clear": pct("chk_pa_clear"),
        "MSS/CISD": pct("chk_mss_cisd"),
    }

    # Mistake counts
    mc = {}
    for t in trades:
        if t["mistake_tags"]:
            for tag in t["mistake_tags"].split(","):
                tag = tag.strip()
                if tag: mc[tag] = mc.get(tag, 0) + 1

    # Session counts
    sessions = {}
    for t in trades:
        s = t["session"] or "unknown"
        sessions[s] = sessions.get(s, 0) + 1

    # Emotional state
    emotions = {}
    for t in trades:
        e = t["emotional_state"] or "unknown"
        emotions[e] = emotions.get(e, 0) + 1

    # Session rules
    am_trades = sum(1 for t in trades if t["session"] in ["ny_am", "ny_pre"])
    rules = {
        "total": total, "max_total": 3,
        "am_trades": am_trades, "max_am": 2,
        "over_total": total >= 3,
        "over_am": am_trades >= 2,
        "walk_away": wins > 0 and losses == 0 and total >= 1
    }

    return {
        "date": today, "total": total, "high_prob": high_prob,
        "mistakes": mistakes, "wins": wins, "losses": losses, "pnl": pnl,
        "checklist": checklist, "mistake_counts": mc,
        "sessions": sessions, "emotions": emotions,
        "rules": rules, "trades": trades
    }

# ── Analytics ─────────────────────────────────────────
@app.get("/analytics")
def analytics(days: int = 30):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM trades WHERE date(timestamp)>=date('now',?) ORDER BY timestamp DESC",
        (f"-{days} days",)
    ).fetchall()
    conn.close()
    trades = [dict(r) for r in rows]
    if not trades: return {"empty": True}

    total  = len(trades)
    wins   = sum(1 for t in trades if t["result"] == "win")
    losses = sum(1 for t in trades if t["result"] == "loss")
    be     = sum(1 for t in trades if t["result"] == "be")
    pnl    = round(sum(t["pnl"] or 0 for t in trades), 2)

    # High prob vs low prob / mistake win rates
    def wr_for(prob):
        subset = [t for t in trades if t["probability"] == prob]
        w = sum(1 for t in subset if t["result"] == "win")
        return {"total": len(subset), "wins": w, "wr": round(w/len(subset)*100,1) if subset else None}

    # Session performance
    session_perf = {}
    for t in trades:
        s = t["session"] or "unknown"
        if s not in session_perf: session_perf[s] = {"wins":0,"losses":0,"total":0}
        session_perf[s]["total"] += 1
        if t["result"]=="win": session_perf[s]["wins"] += 1
        if t["result"]=="loss": session_perf[s]["losses"] += 1

    # Setup performance
    setup_perf = {}
    for t in trades:
        s = t["setup_type"] or "untagged"
        if s not in setup_perf: setup_perf[s] = {"wins":0,"losses":0,"total":0}
        setup_perf[s]["total"] += 1
        if t["result"]=="win": setup_perf[s]["wins"] += 1
        if t["result"]=="loss": setup_perf[s]["losses"] += 1

    # Liquidity flow performance
    flow_perf = {}
    for t in trades:
        f = t["liq_flow"] or "unknown"
        if f not in flow_perf: flow_perf[f] = {"wins":0,"losses":0,"total":0}
        flow_perf[f]["total"] += 1
        if t["result"]=="win": flow_perf[f]["wins"] += 1
        if t["result"]=="loss": flow_perf[f]["losses"] += 1

    # Emotional state vs win rate
    emotion_perf = {}
    for t in trades:
        e = t["emotional_state"] or "unknown"
        if e not in emotion_perf: emotion_perf[e] = {"wins":0,"total":0}
        emotion_perf[e]["total"] += 1
        if t["result"]=="win": emotion_perf[e]["wins"] += 1

    # Checklist skips
    def skip_pct(key):
        skipped = sum(1 for t in trades if not t[key])
        return round(skipped/total*100) if total else 0

    checklist_skips = {
        "4HR Bias":    skip_pct("chk_4hr_bias"),
        "1HR DOL":     skip_pct("chk_1hr_dol"),
        "15min Conf":  skip_pct("chk_15min_conf"),
        "5min Entry":  skip_pct("chk_5min_entry"),
        "PA Clear":    skip_pct("chk_pa_clear"),
        "MSS/CISD":    skip_pct("chk_mss_cisd"),
    }

    # Trade management adherence
    mgmt = {
        "Volatility 10min": round(sum(1 for t in trades if t["mgmt_volatility_10min"])/total*100) if total else 0,
        "Held 4min":        round(sum(1 for t in trades if t["mgmt_held_4min"])/total*100) if total else 0,
        "BE Correct":       round(sum(1 for t in trades if t["mgmt_be_correct"])/total*100) if total else 0,
        "Early Exit":       round(sum(1 for t in trades if t["mgmt_early_exit"])/total*100) if total else 0,
    }

    # Mistake frequency
    mc = {}
    for t in trades:
        if t["mistake_tags"]:
            for tag in t["mistake_tags"].split(","):
                tag = tag.strip()
                if tag: mc[tag] = mc.get(tag, 0) + 1
    top_mistakes = sorted(mc.items(), key=lambda x: x[1], reverse=True)[:10]

    # Daily PnL
    daily = {}
    for t in trades:
        d = t["timestamp"][:10]
        daily[d] = round(daily.get(d, 0) + (t["pnl"] or 0), 2)
    daily_pnl = [{"date":k,"pnl":v} for k,v in sorted(daily.items())]

    return {
        "period": days, "total": total, "wins": wins, "losses": losses, "be": be,
        "win_rate": round(wins/total*100,1) if total else 0,
        "total_pnl": pnl,
        "high_prob":  wr_for("high_prob"),
        "low_prob":   wr_for("low_prob"),
        "mistake":    wr_for("mistake"),
        "session_performance": session_perf,
        "setup_performance": setup_perf,
        "flow_performance": flow_perf,
        "emotion_performance": emotion_perf,
        "checklist_skips": checklist_skips,
        "mgmt_adherence": mgmt,
        "top_mistakes": top_mistakes,
        "daily_pnl": daily_pnl,
    }

# ── AI Behavioral Analysis ────────────────────────────
@app.get("/analyze")
def analyze(days: int = 7):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM trades WHERE date(timestamp)>=date('now',?) ORDER BY timestamp DESC",
        (f"-{days} days",)
    ).fetchall()
    conn.close()
    trades = [dict(r) for r in rows]
    if not trades: return {"analysis": "No trades in this period."}

    lines = []
    for t in trades:
        skipped = []
        if not t["chk_4hr_bias"]:    skipped.append("4HR bias")
        if not t["chk_1hr_dol"]:     skipped.append("1HR DOL")
        if not t["chk_15min_conf"]:  skipped.append("15min conf")
        if not t["chk_5min_entry"]:  skipped.append("5min entry")
        if not t["chk_pa_clear"]:    skipped.append("PA")
        if not t["chk_mss_cisd"]:    skipped.append("MSS/CISD")

        line = (f"[{t['timestamp'][:10]}] {(t['probability'] or '?').upper()} | "
                f"{t.get('session','?')} | {t.get('setup_type','?')} | "
                f"Flow:{t.get('liq_flow','?')} | Result:{t.get('result','?')} | "
                f"Emotion:{t.get('emotional_state','?')}")
        if t.get("mistake_tags"): line += f" | Mistakes:{t['mistake_tags']}"
        if skipped: line += f" | SKIPPED:{','.join(skipped)}"
        lines.append(line)

    prompt = f"""You are a direct trading psychology coach for an MNQ futures trader.

Their methodology:
- Must identify DOL + PA (2/2) before any trade. 1/2 or 0/2 = NO TRADE.
- Timeframe process: 4HR bias → 1HR DOL → 15min confluence → 5min entry → 1min confirmation
- High probability: ERL→IRL or IRL→ERL with MSS/CISD + FVG confluence
- Low probability: random FVG/OB without clear DOL or liquidity flow
- Rules: Max 3 trades/day, max 2 AM session, must hold 4+ minutes, volatility must confirm within 10min

Known psychological weaknesses: FOMO, overtrading, overleverage, tilt, revenge, early exit, BE too soon, strategy hopping, impatience

Trade log ({days} days):
{chr(10).join(lines)}

Give SHORT, brutally direct feedback in this exact format:

PATTERN DETECTED:
• [most repeated behavioral issue with count]
• [second pattern]

PROCESS VIOLATIONS:
• [specific checklist steps being skipped and consequence]

EMOTIONAL TREND:
[One sentence on emotional state pattern]

NON-NEGOTIABLE FOR TOMORROW:
[The single rule they must follow — reference their actual methodology]

Under 130 words. No softening. Reference their actual rules."""

    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": TEXT_MODEL, "prompt": prompt,
            "stream": False, "options": {"temperature": 0.2}
        }, timeout=60)
        return {"analysis": resp.json().get("response","No response"), "count": len(trades)}
    except Exception as e:
        return {"analysis": f"Ollama unavailable: {e}", "count": len(trades)}

# ── Trade history ─────────────────────────────────────
@app.get("/trades")
def get_trades(limit: int = 100, date_filter: Optional[str] = None):
    conn = get_db()
    if date_filter:
        rows = conn.execute(
            "SELECT * FROM trades WHERE date(timestamp)=? ORDER BY timestamp DESC LIMIT ?",
            (date_filter, limit)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.delete("/trade/{tid}")
def delete_trade(tid: int):
    conn = get_db()
    conn.execute("DELETE FROM trades WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    return {"success": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
