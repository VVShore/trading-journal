"""
Trading Journal v4 — FastAPI Backend
Multi-account · Sequence analytics · Position sizing · Graph data · Live sync
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import sqlite3, shutil, requests, json, base64, re, os
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_PATH      = "journal.db"
IMAGES_DIR   = Path("images")
IMAGES_DIR.mkdir(exist_ok=True)
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_TAGS  = "http://localhost:11434/api/tags"
TEXT_MODEL   = "qwen2.5"
VISION_MODEL = "llama3.2-vision"

# ── DB ────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL,
            color     TEXT DEFAULT '#4ade80',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        INSERT OR IGNORE INTO accounts (id, name, color) VALUES (1, 'Main Account', '#4ade80');

        CREATE TABLE IF NOT EXISTS trades (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id           INTEGER DEFAULT 1,
            timestamp            TEXT NOT NULL,
            trade_num_session    INTEGER DEFAULT 1,
            image_path           TEXT,

            -- Core
            probability          TEXT,
            setup_type           TEXT,
            entry_trigger        TEXT,
            direction            TEXT,

            -- Position sizing
            position_size        REAL,
            instrument           TEXT DEFAULT 'MNQ',

            -- Context
            session              TEXT,
            dol_source           TEXT,
            liq_flow             TEXT,
            confluences          TEXT,

            -- Checklist
            chk_4hr_bias         INTEGER DEFAULT 0,
            chk_1hr_dol          INTEGER DEFAULT 0,
            chk_15min_conf       INTEGER DEFAULT 0,
            chk_5min_entry       INTEGER DEFAULT 0,
            chk_pa_clear         INTEGER DEFAULT 0,
            chk_mss_cisd         INTEGER DEFAULT 0,

            -- Management
            mgmt_volatility_10min INTEGER DEFAULT 0,
            mgmt_held_4min        INTEGER DEFAULT 0,
            mgmt_be_correct       INTEGER DEFAULT 0,
            mgmt_early_exit       INTEGER DEFAULT 0,

            -- Emotional
            emotional_state      TEXT,
            mistake_tags         TEXT,

            -- Result
            result               TEXT,
            pnl                  REAL,
            hold_time_candles    INTEGER,

            -- AI extracted
            ai_price             TEXT,
            ai_time              TEXT,
            ai_duration          TEXT,
            ai_direction         TEXT,
            ai_pnl               TEXT,
            ai_notes             TEXT,

            notes                TEXT,
            created_at           TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (account_id) REFERENCES accounts(id)
        );
    """)
    conn.commit()
    conn.close()

init_db()

app.mount("/images", StaticFiles(directory="images"), name="images")

@app.get("/")
def root(): return FileResponse("index.html")

# ── Accounts ──────────────────────────────────────────
@app.get("/accounts")
def get_accounts():
    conn = get_db()
    rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/accounts")
def create_account(name: str = Form(...), color: str = Form('#4ade80')):
    conn = get_db()
    cur = conn.execute("INSERT INTO accounts (name, color) VALUES (?,?)", (name, color))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return {"id": new_id, "name": name, "color": color}

@app.put("/accounts/{aid}")
def update_account(aid: int, name: str = Form(...), color: str = Form('#4ade80')):
    conn = get_db()
    conn.execute("UPDATE accounts SET name=?, color=? WHERE id=?", (name, color, aid))
    conn.commit()
    conn.close()
    return {"success": True}

@app.delete("/accounts/{aid}")
def delete_account(aid: int):
    conn = get_db()
    # Don't delete if it has trades — just return error
    count = conn.execute("SELECT COUNT(*) FROM trades WHERE account_id=?", (aid,)).fetchone()[0]
    if count > 0:
        conn.close()
        raise HTTPException(400, f"Account has {count} trades. Cannot delete.")
    conn.execute("DELETE FROM accounts WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    return {"success": True}

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
            {"id": "mmxm_buy",    "label": "MMXM Buy"},
            {"id": "mmxm_sell",   "label": "MMXM Sell"},
        ],
        "entry_trigger": [
            {"id": "liq_sweep_mss", "label": "Liq Sweep + MSS"},
            {"id": "cisd",          "label": "CISD"},
            {"id": "fvg_retest",    "label": "FVG Retest"},
            {"id": "ifvg_inv",      "label": "iFVG Inversion"},
            {"id": "ob_tap",        "label": "OB Tap"},
            {"id": "breaker_tap",   "label": "Breaker Tap"},
            {"id": "starter_pos",   "label": "Starter Position"},
            {"id": "smt_div",       "label": "SMT Divergence"},
        ],
        "session": [
            {"id": "asia",    "label": "Asia"},
            {"id": "london",  "label": "London"},
            {"id": "ny_pre",  "label": "NY Pre"},
            {"id": "ny_am",   "label": "NY AM"},
            {"id": "ny_pm",   "label": "NY PM"},
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
            {"id": "erl_to_irl",   "label": "ERL → IRL"},
            {"id": "irl_to_erl",   "label": "IRL → ERL"},
            {"id": "continuation", "label": "Continuation"},
            {"id": "random",       "label": "No Clear Flow"},
        ],
        "confluences": [
            {"id": "ob"},{"id": "fvg"},{"id": "ifvg"},{"id": "breaker"},
            {"id": "bpr"},{"id": "smt"},{"id": "ote"},{"id": "ce"},
            {"id": "std_dev"},{"id": "vol_imb"},{"id": "premium_disc"},
            {"id": "midnight_open"},{"id": "weekly_open"},
        ],
        "emotional_state": [
            {"id": "calm",               "label": "😌 Calm"},
            {"id": "slightly_emotional", "label": "😤 Slightly Off"},
            {"id": "tilt",               "label": "🔥 Tilt"},
        ],
        "mistake_tags": [
            {"id": "fomo",           "label": "FOMO"},
            {"id": "overtrade",      "label": "Overtrade"},
            {"id": "no_dol",         "label": "No DOL"},
            {"id": "no_confirmation","label": "No Confirmation"},
            {"id": "incorrect_bias", "label": "Incorrect Bias"},
            {"id": "revenge",        "label": "Revenge Trade"},
            {"id": "tilt_trading",   "label": "Tilt Trading"},
            {"id": "early_exit",     "label": "Early Exit"},
            {"id": "be_too_soon",    "label": "BE Too Soon"},
            {"id": "overleverage",   "label": "Overleverage"},
            {"id": "strat_hop",      "label": "Strategy Hopping"},
            {"id": "impatience",     "label": "Impatience"},
            {"id": "no_pa",          "label": "No Good PA"},
            {"id": "low_prob_setup", "label": "Low Prob Setup"},
            {"id": "wrong_tf",       "label": "Wrong TF Entry"},
            {"id": "rule_break",     "label": "Rule Breaking"},
        ],
        "instruments": ["MNQ", "NQ", "ES", "MES", "YM", "MYM", "CL", "GC"]
    }

# ── Vision ────────────────────────────────────────────
@app.get("/test-vision")
def test_vision():
    """Check if vision model is available in Ollama"""
    try:
        resp = requests.get(OLLAMA_TAGS, timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        vision_available = any(VISION_MODEL.split(":")[0] in m for m in models)
        text_available   = any(TEXT_MODEL.split(":")[0] in m for m in models)
        return {
            "ollama_running": True,
            "vision_available": vision_available,
            "text_available": text_available,
            "models": models
        }
    except Exception as e:
        return {"ollama_running": False, "error": str(e)}

def analyze_chart(image_path: str) -> dict:
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        prompt = """Analyze this Tradovate 1-minute futures chart screenshot.
Return ONLY a JSON object, no other text:
{
  "price": "entry/execution price if visible (e.g. 18245.50)",
  "time": "execution time in HH:MM format",
  "duration_candles": number of 1-minute candles the trade lasted as integer,
  "direction": "long or short",
  "pnl": "PNL if visible bottom-left (e.g. +$240 or -$80)",
  "session": "one of: asia/london/ny_pre/ny_am/ny_pm",
  "notes": "single sentence observation about the chart structure"
}
Use null for values you cannot determine with confidence."""
        resp = requests.post(OLLAMA_URL, json={
            "model": VISION_MODEL, "prompt": prompt,
            "images": [img_b64], "stream": False,
            "options": {"temperature": 0.1}
        }, timeout=90)
        result = resp.json().get("response", "")
        m = re.search(r'\{.*?\}', result, re.DOTALL)
        if m:
            data = json.loads(m.group())
            # Clean up duration_candles to int
            if data.get("duration_candles"):
                try: data["duration_candles"] = int(data["duration_candles"])
                except: data["duration_candles"] = None
            return data
        return {"error": "Could not parse AI response"}
    except requests.exceptions.ConnectionError:
        return {"error": "Ollama not running"}
    except Exception as e:
        return {"error": str(e)}

# ── Session trade counter ─────────────────────────────
def get_session_trade_num(conn, account_id: int) -> int:
    today = date.today().isoformat()
    count = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE account_id=? AND date(timestamp)=?",
        (account_id, today)
    ).fetchone()[0]
    return count + 1

# ── Log trade ─────────────────────────────────────────
@app.post("/trade")
async def log_trade(
    account_id: int                 = Form(1),
    probability: Optional[str]      = Form(None),
    setup_type: Optional[str]       = Form(None),
    entry_trigger: Optional[str]    = Form(None),
    direction: Optional[str]        = Form(None),
    position_size: Optional[float]  = Form(None),
    instrument: Optional[str]       = Form("MNQ"),
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
    hold_time_candles: Optional[int]= Form(None),
    notes: Optional[str]            = Form(None),
    run_vision: int                 = Form(0),
    image: Optional[UploadFile]     = File(None)
):
    timestamp  = datetime.now().isoformat()
    image_path = None
    ai_data    = {}

    if image and image.filename:
        ext = Path(image.filename).suffix or ".png"
        fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{ext}"
        image_path = str(IMAGES_DIR / fname)
        with open(image_path, "wb") as f:
            shutil.copyfileobj(image.file, f)
        if run_vision:
            ai_data = analyze_chart(image_path)

    conn = get_db()
    trade_num = get_session_trade_num(conn, account_id)

    # Use AI duration if not manually provided
    candles = hold_time_candles
    if not candles and ai_data.get("duration_candles"):
        candles = ai_data["duration_candles"]

    conn.execute("""
        INSERT INTO trades (
            account_id, timestamp, trade_num_session, image_path,
            probability, setup_type, entry_trigger, direction,
            position_size, instrument,
            session, dol_source, liq_flow, confluences,
            chk_4hr_bias, chk_1hr_dol, chk_15min_conf, chk_5min_entry, chk_pa_clear, chk_mss_cisd,
            mgmt_volatility_10min, mgmt_held_4min, mgmt_be_correct, mgmt_early_exit,
            emotional_state, mistake_tags, result, pnl, hold_time_candles, notes,
            ai_price, ai_time, ai_duration, ai_direction, ai_pnl, ai_notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        account_id, timestamp, trade_num, image_path,
        probability, setup_type, entry_trigger, direction,
        position_size, instrument,
        session or ai_data.get("session"), dol_source, liq_flow, confluences,
        chk_4hr_bias, chk_1hr_dol, chk_15min_conf, chk_5min_entry, chk_pa_clear, chk_mss_cisd,
        mgmt_volatility_10min, mgmt_held_4min, mgmt_be_correct, mgmt_early_exit,
        emotional_state, mistake_tags, result, pnl, candles, notes,
        ai_data.get("price"), ai_data.get("time"),
        str(candles or ai_data.get("duration_candles", "")),
        ai_data.get("direction") or direction, ai_data.get("pnl"), ai_data.get("notes")
    ))
    conn.commit()
    conn.close()
    return {"success": True, "trade_num": trade_num, "ai_data": ai_data}

# ── Today stats ───────────────────────────────────────
@app.get("/stats/today")
def today_stats(account_id: int = 1):
    today = date.today().isoformat()
    conn  = get_db()
    rows  = conn.execute(
        "SELECT * FROM trades WHERE account_id=? AND date(timestamp)=? ORDER BY timestamp ASC",
        (account_id, today)
    ).fetchall()
    trades = [dict(r) for r in rows]
    conn.close()

    total     = len(trades)
    high_prob = sum(1 for t in trades if t["probability"] == "high_prob")
    mistakes  = sum(1 for t in trades if t["probability"] == "mistake")
    wins      = sum(1 for t in trades if t["result"] == "win")
    losses    = sum(1 for t in trades if t["result"] == "loss")
    pnl       = round(sum(t["pnl"] or 0 for t in trades), 2)

    def pct(key): return round(sum(1 for t in trades if t[key]) / total * 100) if total else 0
    checklist = {
        "4HR Bias": pct("chk_4hr_bias"), "1HR DOL": pct("chk_1hr_dol"),
        "15min Conf": pct("chk_15min_conf"), "5min Entry": pct("chk_5min_entry"),
        "PA Clear": pct("chk_pa_clear"), "MSS/CISD": pct("chk_mss_cisd"),
    }

    mc = {}
    for t in trades:
        if t["mistake_tags"]:
            for tag in t["mistake_tags"].split(","):
                tag = tag.strip()
                if tag: mc[tag] = mc.get(tag, 0) + 1

    am_trades = sum(1 for t in trades if t["session"] in ["ny_am", "ny_pre"])
    rules = {
        "total": total, "am_trades": am_trades,
        "over_total": total >= 3, "over_am": am_trades >= 2,
        "walk_away": wins > 0 and losses == 0 and total >= 1
    }

    return {
        "date": today, "total": total, "high_prob": high_prob, "mistakes": mistakes,
        "wins": wins, "losses": losses, "pnl": pnl,
        "checklist": checklist, "mistake_counts": mc, "rules": rules, "trades": trades
    }

# ── Analytics core ────────────────────────────────────
def _fetch_trades(conn, account_id, days):
    if account_id == 0:  # 0 = all accounts
        return [dict(r) for r in conn.execute(
            "SELECT * FROM trades WHERE date(timestamp)>=date('now',?) ORDER BY timestamp ASC",
            (f"-{days} days",)
        ).fetchall()]
    return [dict(r) for r in conn.execute(
        "SELECT * FROM trades WHERE account_id=? AND date(timestamp)>=date('now',?) ORDER BY timestamp ASC",
        (account_id, f"-{days} days")
    ).fetchall()]

@app.get("/analytics")
def analytics(account_id: int = 1, days: int = 30):
    conn   = get_db()
    trades = _fetch_trades(conn, account_id, days)
    conn.close()
    if not trades: return {"empty": True}

    total  = len(trades)
    wins   = sum(1 for t in trades if t["result"] == "win")
    losses = sum(1 for t in trades if t["result"] == "loss")
    be     = sum(1 for t in trades if t["result"] == "be")
    pnl    = round(sum(t["pnl"] or 0 for t in trades), 2)

    def wr_for(prob):
        sub = [t for t in trades if t["probability"] == prob]
        w = sum(1 for t in sub if t["result"] == "win")
        return {"total": len(sub), "wins": w, "wr": round(w/len(sub)*100, 1) if sub else None}

    # ── Sequence analytics ────────────────────────────
    # Group by day→sort by time for proper sequencing
    win_after_loss = {"total": 0, "wins": 0}
    mistake_after_1loss = {"total": 0, "mistakes": 0}
    mistake_after_2loss = {"total": 0, "mistakes": 0}
    pnl_after_loss_list = []

    # Sort trades by timestamp
    sorted_trades = sorted(trades, key=lambda t: t["timestamp"])
    for i, t in enumerate(sorted_trades):
        if i == 0: continue
        prev = sorted_trades[i-1]
        same_day = t["timestamp"][:10] == prev["timestamp"][:10]
        same_acc = t["account_id"] == prev["account_id"]
        if not (same_day and same_acc): continue

        if prev["result"] == "loss":
            win_after_loss["total"] += 1
            if t["result"] == "win": win_after_loss["wins"] += 1
            mistake_after_1loss["total"] += 1
            if t["probability"] == "mistake": mistake_after_1loss["mistakes"] += 1
            if t["pnl"] is not None: pnl_after_loss_list.append(t["pnl"])

        # After 2 consecutive losses
        if i >= 2:
            prev2 = sorted_trades[i-2]
            same_day2 = t["timestamp"][:10] == prev2["timestamp"][:10]
            same_acc2 = t["account_id"] == prev2["account_id"]
            if same_day2 and same_acc2 and prev["result"] == "loss" and prev2["result"] == "loss":
                mistake_after_2loss["total"] += 1
                if t["probability"] == "mistake": mistake_after_2loss["mistakes"] += 1

    sequence = {
        "win_after_loss_rate": round(win_after_loss["wins"]/win_after_loss["total"]*100, 1) if win_after_loss["total"] else None,
        "win_after_loss_total": win_after_loss["total"],
        "mistake_rate_after_1loss": round(mistake_after_1loss["mistakes"]/mistake_after_1loss["total"]*100, 1) if mistake_after_1loss["total"] else None,
        "mistake_rate_after_1loss_total": mistake_after_1loss["total"],
        "mistake_rate_after_2loss": round(mistake_after_2loss["mistakes"]/mistake_after_2loss["total"]*100, 1) if mistake_after_2loss["total"] else None,
        "mistake_rate_after_2loss_total": mistake_after_2loss["total"],
        "avg_pnl_after_loss": round(sum(pnl_after_loss_list)/len(pnl_after_loss_list), 2) if pnl_after_loss_list else None,
    }

    # ── Position sizing ───────────────────────────────
    size_perf = {}
    for t in trades:
        sz = t["position_size"]
        if sz is None: continue
        key = f"{sz} {t['instrument'] or 'MNQ'}"
        if key not in size_perf: size_perf[key] = {"wins": 0, "losses": 0, "total": 0, "mistakes": 0, "pnl": 0}
        size_perf[key]["total"] += 1
        if t["result"] == "win":  size_perf[key]["wins"] += 1
        if t["result"] == "loss": size_perf[key]["losses"] += 1
        if t["probability"] == "mistake": size_perf[key]["mistakes"] += 1
        size_perf[key]["pnl"] = round(size_perf[key]["pnl"] + (t["pnl"] or 0), 2)

    for k in size_perf:
        sub = size_perf[k]
        sub["wr"] = round(sub["wins"]/sub["total"]*100, 1) if sub["total"] else 0
        sub["mistake_rate"] = round(sub["mistakes"]/sub["total"]*100, 1) if sub["total"] else 0

    # ── Hold time ─────────────────────────────────────
    with_time = [t for t in trades if t["hold_time_candles"]]
    hold_wins  = [t["hold_time_candles"] for t in with_time if t["result"] == "win"]
    hold_loss  = [t["hold_time_candles"] for t in with_time if t["result"] == "loss"]
    hold_time = {
        "avg_win_mins":  round(sum(hold_wins)/len(hold_wins), 1) if hold_wins else None,
        "avg_loss_mins": round(sum(hold_loss)/len(hold_loss), 1) if hold_loss else None,
        "distribution":  [{"mins": t["hold_time_candles"], "result": t["result"]} for t in with_time]
    }

    # ── Perf breakdowns ───────────────────────────────
    def perf_by(key):
        d = {}
        for t in trades:
            v = t.get(key) or "unknown"
            if v not in d: d[v] = {"wins":0,"losses":0,"total":0,"pnl":0}
            d[v]["total"] += 1
            if t["result"]=="win":  d[v]["wins"] += 1
            if t["result"]=="loss": d[v]["losses"] += 1
            d[v]["pnl"] = round(d[v]["pnl"] + (t["pnl"] or 0), 2)
        for v in d:
            d[v]["wr"] = round(d[v]["wins"]/d[v]["total"]*100,1) if d[v]["total"] else 0
        return d

    # ── Checklist skips ───────────────────────────────
    def skip_pct(key): return round(sum(1 for t in trades if not t[key])/total*100) if total else 0
    checklist_skips = {
        "4HR Bias": skip_pct("chk_4hr_bias"), "1HR DOL": skip_pct("chk_1hr_dol"),
        "15min Conf": skip_pct("chk_15min_conf"), "5min Entry": skip_pct("chk_5min_entry"),
        "PA Clear": skip_pct("chk_pa_clear"), "MSS/CISD": skip_pct("chk_mss_cisd"),
    }

    # ── Mgmt adherence ────────────────────────────────
    mgmt = {
        "Volatility 10min": round(sum(1 for t in trades if t["mgmt_volatility_10min"])/total*100) if total else 0,
        "Held 4min":        round(sum(1 for t in trades if t["mgmt_held_4min"])/total*100) if total else 0,
        "BE Correct":       round(sum(1 for t in trades if t["mgmt_be_correct"])/total*100) if total else 0,
        "Early Exit":       round(sum(1 for t in trades if t["mgmt_early_exit"])/total*100) if total else 0,
    }

    # ── Mistake freq ──────────────────────────────────
    mc = {}
    for t in trades:
        if t["mistake_tags"]:
            for tag in t["mistake_tags"].split(","):
                tag = tag.strip()
                if tag: mc[tag] = mc.get(tag, 0) + 1
    top_mistakes = sorted(mc.items(), key=lambda x: x[1], reverse=True)[:10]

    # ── Daily PnL (cumulative for graph) ─────────────
    daily = {}
    for t in sorted_trades:
        d = t["timestamp"][:10]
        acc = t["account_id"]
        key = f"{d}_{acc}" if account_id == 0 else d
        daily[key] = round(daily.get(key, 0) + (t["pnl"] or 0), 2)

    # Cumulative PnL
    cumulative = []
    running = 0
    for k, v in sorted(daily.items()):
        running = round(running + v, 2)
        date_part = k.split("_")[0] if account_id == 0 else k
        cumulative.append({"date": date_part, "daily": v, "cumulative": running})

    # Trade frequency per day
    freq = {}
    for t in trades:
        d = t["timestamp"][:10]
        freq[d] = freq.get(d, 0) + 1
    trade_freq = [{"date": k, "count": v} for k, v in sorted(freq.items())]

    return {
        "period": days, "total": total, "wins": wins, "losses": losses, "be": be,
        "win_rate": round(wins/total*100, 1) if total else 0,
        "total_pnl": pnl,
        "high_prob": wr_for("high_prob"),
        "low_prob":  wr_for("low_prob"),
        "mistake":   wr_for("mistake"),
        "sequence": sequence,
        "size_performance": size_perf,
        "hold_time": hold_time,
        "session_performance": perf_by("session"),
        "setup_performance": perf_by("setup_type"),
        "flow_performance": perf_by("liq_flow"),
        "emotion_performance": perf_by("emotional_state"),
        "checklist_skips": checklist_skips,
        "mgmt_adherence": mgmt,
        "top_mistakes": top_mistakes,
        "cumulative_pnl": cumulative,
        "trade_frequency": trade_freq,
    }

# ── AI Analysis ───────────────────────────────────────
@app.get("/analyze")
def analyze(account_id: int = 1, days: int = 7):
    conn   = get_db()
    trades = _fetch_trades(conn, account_id, days)
    conn.close()
    if not trades: return {"analysis": "No trades in this period."}

    sorted_trades = sorted(trades, key=lambda t: t["timestamp"])
    lines = []
    for i, t in enumerate(sorted_trades):
        skipped = []
        if not t["chk_4hr_bias"]:   skipped.append("4HR")
        if not t["chk_1hr_dol"]:    skipped.append("DOL")
        if not t["chk_15min_conf"]: skipped.append("15m")
        if not t["chk_5min_entry"]: skipped.append("5m entry")
        if not t["chk_pa_clear"]:   skipped.append("PA")
        if not t["chk_mss_cisd"]:   skipped.append("MSS")
        line = (f"[{t['timestamp'][:10]}] Trade#{t['trade_num_session']} "
                f"{(t['probability'] or '?').upper()} | {t.get('session','?')} | "
                f"{t.get('setup_type','?')} | Flow:{t.get('liq_flow','?')} | "
                f"Size:{t.get('position_size','?')}{t.get('instrument','')} | "
                f"Emotion:{t.get('emotional_state','?')} | Result:{t.get('result','?')}")
        if t.get("pnl") is not None: line += f" PNL:${t['pnl']}"
        if t.get("mistake_tags"):    line += f" | Mistakes:{t['mistake_tags']}"
        if skipped:                  line += f" | SKIPPED:{','.join(skipped)}"
        lines.append(line)

    prompt = f"""You are a direct trading psychology coach for an MNQ/NQ futures trader using ICT methodology.

Their rules:
- 4HR→1HR DOL→15min confluence→5min entry→1min confirm (NEVER enter on 1min alone)
- High prob = ERL→IRL or IRL→ERL with MSS/CISD + FVG. Low prob = random FVG/OB = NO TRADE
- Max 3 trades/day, max 2 AM session, hold 4+ min, volatility confirms within 10min
- After a loss: SIZE DOWN, do not revenge trade
- Max risk $400, 40pts MNQ stop

Known psychological weaknesses: FOMO, overtrading, overleverage, tilt after losses, early exits, rule breaking

Trade log ({days} days):
{chr(10).join(lines)}

Format response EXACTLY:

BIGGEST PATTERN: [most repeated issue with count]

SEQUENCE RISK: [what happens after they take a loss — be specific]

PROCESS GAPS: [which checklist steps skipped most and what that costs]

SIZING ISSUE: [any overleverage or size escalation after losses]

NON-NEGOTIABLE TOMORROW: [single rule, reference their actual methodology]

Under 140 words. Be brutally direct."""

    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": TEXT_MODEL, "prompt": prompt,
            "stream": False, "options": {"temperature": 0.2}
        }, timeout=60)
        return {"analysis": resp.json().get("response", "No response"), "count": len(trades)}
    except Exception as e:
        return {"analysis": f"Ollama unavailable: {e}", "count": len(trades)}

# ── Trade history ─────────────────────────────────────
@app.get("/trades")
def get_trades(account_id: int = 1, limit: int = 100, date_filter: Optional[str] = None):
    conn = get_db()
    if account_id == 0:
        base = "SELECT t.*, a.name as account_name, a.color as account_color FROM trades t LEFT JOIN accounts a ON t.account_id=a.id"
        where = f" WHERE date(timestamp)='{date_filter}'" if date_filter else ""
    else:
        base = "SELECT t.*, a.name as account_name, a.color as account_color FROM trades t LEFT JOIN accounts a ON t.account_id=a.id"
        where = f" WHERE t.account_id={account_id}" + (f" AND date(timestamp)='{date_filter}'" if date_filter else "")
    rows = conn.execute(f"{base}{where} ORDER BY timestamp DESC LIMIT {limit}").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.delete("/trade/{tid}")
def delete_trade(tid: int):
    conn = get_db()
    conn.execute("DELETE FROM trades WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    return {"success": True}

@app.put("/trade/{tid}")
async def update_trade(tid: int, result: Optional[str] = Form(None), pnl: Optional[float] = Form(None), notes: Optional[str] = Form(None)):
    conn = get_db()
    conn.execute("UPDATE trades SET result=?, pnl=?, notes=? WHERE id=?", (result, pnl, notes, tid))
    conn.commit()
    conn.close()
    return {"success": True}

# ── Startup .bat generator ────────────────────────────
@app.on_event("startup")
def generate_startup_files():
    bat = Path("start_journal.bat")
    if not bat.exists():
        cwd = Path.cwd().resolve()
        bat.write_text(f"@echo off\ncd /d \"{cwd}\"\npython main.py\n")
        print(f"✅ Generated start_journal.bat — add to shell:startup for auto-start")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
