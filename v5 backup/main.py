"""
Trading Journal v5 — FastAPI Backend
New universal log structure · moondream vision · ADHD-optimized · daily summary
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

DB_PATH    = "journal.db"
IMAGES_DIR = Path("images")
IMAGES_DIR.mkdir(exist_ok=True)
OLLAMA_URL  = "http://localhost:11434/api/generate"
OLLAMA_TAGS = "http://localhost:11434/api/tags"
TEXT_MODEL  = "qwen2.5"
VISION_MODEL = "moondream"   # ~1.7GB, fits 4GB RAM

# ── Database ──────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            color      TEXT DEFAULT '#4ade80',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO accounts (id, name, color) VALUES (1, 'Main', '#4ade80');

        CREATE TABLE IF NOT EXISTS trades (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id        INTEGER DEFAULT 1,
            timestamp         TEXT NOT NULL,
            trade_num         INTEGER DEFAULT 1,
            image_path        TEXT,

            -- Core new structure
            setup_type        TEXT,   -- trend_cont, reversal, range_chop, breakout, liq_sweep
            setup_quality     TEXT,   -- A, B, C
            execution_score   INTEGER,-- 1-10
            entry_behavior    TEXT,   -- decisive, hesitation, late, early, chase
            mgmt_behavior     TEXT,   -- followed_plan, cut_early, let_run, overheld, moved_stop, scaled_poorly
            exit_type         TEXT,   -- target_hit, stopped_out, manual_planned, manual_emotional
            emotion_state     TEXT,   -- calm, focused, fear, fomo, revenge, overconfidence, frustration
            r_multiple        REAL,   -- e.g. 1.5, -1, 2.0

            -- Optional context
            htf_bias          TEXT,   -- yes, no
            direction         TEXT,   -- long, short
            session           TEXT,
            dol_source        TEXT,
            liq_flow          TEXT,
            confluences       TEXT,
            position_size     REAL,
            instrument        TEXT DEFAULT 'MNQ',

            -- AI extracted
            ai_price          TEXT,
            ai_direction      TEXT,
            ai_notes          TEXT,

            notes             TEXT,
            created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        );

        CREATE TABLE IF NOT EXISTS daily_summaries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER DEFAULT 1,
            date            TEXT NOT NULL,
            daily_score     TEXT,     -- A, B, C
            mistake_1       TEXT,
            mistake_2       TEXT,
            best_setup      TEXT,
            biggest_leak    TEXT,     -- entries, management, psychology, overtrading
            one_fix         TEXT,
            total_trades    INTEGER,
            total_r         REAL,
            win_rate        REAL,
            ai_summary      TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(account_id, date)
        );
    """)
    conn.commit()
    conn.close()

init_db()

app.mount("/images", StaticFiles(directory="images"), name="images")

@app.get("/")
def root(): return FileResponse("index.html")

# ── System check ──────────────────────────────────────
@app.get("/system")
def system_check():
    try:
        resp  = requests.get(OLLAMA_TAGS, timeout=4)
        models = [m["name"].split(":")[0] for m in resp.json().get("models", [])]
        return {
            "ollama": True,
            "text":   TEXT_MODEL in models,
            "vision": VISION_MODEL in models,
            "vision_model": VISION_MODEL,
            "vision_size": "~1.7GB",
            "vision_cmd": f"ollama pull {VISION_MODEL}",
            "models": models
        }
    except:
        return {"ollama": False, "text": False, "vision": False}

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
    cur  = conn.execute("INSERT INTO accounts (name, color) VALUES (?,?)", (name, color))
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return {"id": aid, "name": name, "color": color}

@app.delete("/accounts/{aid}")
def delete_account(aid: int):
    conn  = get_db()
    count = conn.execute("SELECT COUNT(*) FROM trades WHERE account_id=?", (aid,)).fetchone()[0]
    if count:
        conn.close()
        raise HTTPException(400, f"Account has {count} trades — cannot delete.")
    conn.execute("DELETE FROM accounts WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    return {"success": True}

# ── Tags config ───────────────────────────────────────
@app.get("/tags")
def get_tags():
    return {
        "setup": [
            {"id": "trend_cont",  "label": "Trend Cont.",    "icon": "📈"},
            {"id": "reversal",    "label": "Reversal",        "icon": "🔄"},
            {"id": "range_chop",  "label": "Range / Chop",   "icon": "↔️"},
            {"id": "breakout",    "label": "Breakout",        "icon": "💥"},
            {"id": "liq_sweep",   "label": "Liq. Sweep",     "icon": "🌊"},
        ],
        "quality": [
            {"id": "A", "label": "A — Clean",        "desc": "High confidence"},
            {"id": "B", "label": "B — Valid",         "desc": "Not ideal"},
            {"id": "C", "label": "C — Forced",        "desc": "Low quality"},
        ],
        "entry_behavior": [
            {"id": "decisive",   "label": "Decisive",    "icon": "⚡"},
            {"id": "hesitation", "label": "Hesitation",  "icon": "😰"},
            {"id": "late",       "label": "Late Entry",  "icon": "⏰"},
            {"id": "early",      "label": "Early Entry", "icon": "🏃"},
            {"id": "chase",      "label": "Chase",       "icon": "🔥"},
        ],
        "mgmt_behavior": [
            {"id": "followed_plan",  "label": "Followed Plan",  "icon": "✅"},
            {"id": "cut_early",      "label": "Cut Early",       "icon": "✂️"},
            {"id": "let_run",        "label": "Let Run",         "icon": "🏆"},
            {"id": "overheld",       "label": "Overheld",        "icon": "⏳"},
            {"id": "moved_stop",     "label": "Moved Stop",      "icon": "🚧"},
            {"id": "scaled_poorly",  "label": "Scaled Poorly",   "icon": "📉"},
        ],
        "exit_type": [
            {"id": "target_hit",       "label": "Target Hit",        "icon": "🎯"},
            {"id": "stopped_out",      "label": "Stopped Out",        "icon": "🛑"},
            {"id": "manual_planned",   "label": "Manual (Planned)",   "icon": "📋"},
            {"id": "manual_emotional", "label": "Manual (Emotional)", "icon": "😤"},
        ],
        "emotion": [
            {"id": "calm",           "label": "Calm",           "icon": "😌"},
            {"id": "focused",        "label": "Focused",        "icon": "🎯"},
            {"id": "fear",           "label": "Fear / Hesitation","icon": "😰"},
            {"id": "fomo",           "label": "FOMO",           "icon": "💨"},
            {"id": "revenge",        "label": "Revenge",        "icon": "😤"},
            {"id": "overconfidence", "label": "Overconfident",  "icon": "😏"},
            {"id": "frustration",    "label": "Frustration",    "icon": "😠"},
        ],
        "htf_bias": [
            {"id": "yes", "label": "HTF Aligned ✓"},
            {"id": "no",  "label": "HTF Against ✗"},
        ],
        "session": [
            {"id": "asia"},{"id": "london"},{"id": "ny_pre"},{"id": "ny_am"},{"id": "ny_pm"}
        ],
        "liq_flow": [
            {"id": "erl_to_irl","label": "ERL → IRL"},
            {"id": "irl_to_erl","label": "IRL → ERL"},
            {"id": "continuation","label": "Continuation"},
            {"id": "random","label": "No Clear Flow"},
        ],
        "r_presets": ["+3R","+2R","+1.5R","+1R","+0.5R","BE","-0.5R","-1R"],
        "instruments": ["MNQ","NQ","ES","MES","YM","MYM","CL","GC"],
        "mistake_types": [
            "Overtrading","Hesitation","FOMO","Revenge","Rule Breaking","Poor Selection"
        ],
        "biggest_leak": ["Entries","Management","Psychology","Overtrading"],
    }

# ── Vision (moondream) ────────────────────────────────
def analyze_chart(image_path: str) -> dict:
    """Use moondream (~1.7GB) for lightweight chart reading"""
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        # Moondream uses a simpler prompt style
        prompt = "Describe this trading chart. What direction is the trade (long or short)? What is the entry price shown? What time is shown? Return as JSON: {\"direction\": \"long or short\", \"price\": \"entry price\", \"time\": \"HH:MM\", \"notes\": \"brief observation\"}"

        resp = requests.post(OLLAMA_URL, json={
            "model": VISION_MODEL,
            "prompt": prompt,
            "images": [img_b64],
            "stream": False,
            "options": {"temperature": 0.1}
        }, timeout=60)

        result = resp.json().get("response", "")
        m = re.search(r'\{.*?\}', result, re.DOTALL)
        if m:
            data = json.loads(m.group())
            return data
        # If no JSON found, return raw as note
        return {"notes": result[:200] if result else "No response"}

    except requests.exceptions.ConnectionError:
        return {"error": "Ollama not running"}
    except Exception as e:
        return {"error": str(e)}

# ── Trade number ──────────────────────────────────────
def get_trade_num(conn, account_id):
    today = date.today().isoformat()
    return conn.execute(
        "SELECT COUNT(*) FROM trades WHERE account_id=? AND date(timestamp)=?",
        (account_id, today)
    ).fetchone()[0] + 1

# ── Log trade ─────────────────────────────────────────
@app.post("/trade")
async def log_trade(
    account_id:       int            = Form(1),
    setup_type:       Optional[str]  = Form(None),
    setup_quality:    Optional[str]  = Form(None),
    execution_score:  Optional[int]  = Form(None),
    entry_behavior:   Optional[str]  = Form(None),
    mgmt_behavior:    Optional[str]  = Form(None),
    exit_type:        Optional[str]  = Form(None),
    emotion_state:    Optional[str]  = Form(None),
    r_multiple:       Optional[float]= Form(None),
    htf_bias:         Optional[str]  = Form(None),
    direction:        Optional[str]  = Form(None),
    session:          Optional[str]  = Form(None),
    dol_source:       Optional[str]  = Form(None),
    liq_flow:         Optional[str]  = Form(None),
    confluences:      Optional[str]  = Form(None),
    position_size:    Optional[float]= Form(None),
    instrument:       Optional[str]  = Form("MNQ"),
    notes:            Optional[str]  = Form(None),
    run_vision:       int            = Form(0),
    image:            Optional[UploadFile] = File(None)
):
    timestamp  = datetime.now().isoformat()
    image_path = None
    ai_data    = {}

    if image and image.filename:
        ext   = Path(image.filename).suffix or ".png"
        fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{ext}"
        image_path = str(IMAGES_DIR / fname)
        with open(image_path, "wb") as f:
            shutil.copyfileobj(image.file, f)
        if run_vision:
            ai_data = analyze_chart(image_path)

    conn      = get_db()
    trade_num = get_trade_num(conn, account_id)

    conn.execute("""
        INSERT INTO trades (
            account_id, timestamp, trade_num, image_path,
            setup_type, setup_quality, execution_score, entry_behavior,
            mgmt_behavior, exit_type, emotion_state, r_multiple,
            htf_bias, direction, session, dol_source, liq_flow, confluences,
            position_size, instrument, notes,
            ai_price, ai_direction, ai_notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        account_id, timestamp, trade_num, image_path,
        setup_type, setup_quality, execution_score, entry_behavior,
        mgmt_behavior, exit_type, emotion_state, r_multiple,
        htf_bias, direction or ai_data.get("direction"),
        session, dol_source, liq_flow, confluences,
        position_size, instrument, notes,
        ai_data.get("price"), ai_data.get("direction"), ai_data.get("notes")
    ))
    conn.commit()
    conn.close()

    return {
        "success":   True,
        "trade_num": trade_num,
        "ai_data":   ai_data
    }

# ── Today stats ───────────────────────────────────────
@app.get("/stats/today")
def today_stats(account_id: int = 1):
    today = date.today().isoformat()
    conn  = get_db()
    rows  = conn.execute(
        "SELECT * FROM trades WHERE account_id=? AND date(timestamp)=? ORDER BY timestamp",
        (account_id, today)
    ).fetchall()
    trades = [dict(r) for r in rows]

    # Check if summary exists
    summary = conn.execute(
        "SELECT * FROM daily_summaries WHERE account_id=? AND date=?",
        (account_id, today)
    ).fetchone()
    conn.close()

    total     = len(trades)
    r_trades  = [t for t in trades if t["r_multiple"] is not None]
    total_r   = round(sum(t["r_multiple"] for t in r_trades), 2)
    wins      = sum(1 for t in r_trades if t["r_multiple"] > 0)
    losses    = sum(1 for t in r_trades if t["r_multiple"] < 0)
    be        = sum(1 for t in r_trades if t["r_multiple"] == 0)
    win_rate  = round(wins / len(r_trades) * 100) if r_trades else 0
    avg_exec  = round(sum(t["execution_score"] or 0 for t in trades) / total) if total else 0

    # Emotion breakdown
    emotions = {}
    for t in trades:
        e = t["emotion_state"] or "unknown"
        emotions[e] = emotions.get(e, 0) + 1

    # Quality breakdown
    quality = {"A": 0, "B": 0, "C": 0}
    for t in trades:
        q = t["setup_quality"]
        if q in quality: quality[q] += 1

    # Session rules
    am_trades = sum(1 for t in trades if t["session"] in ["ny_am","ny_pre"])
    rules = {
        "total": total, "over_3": total >= 3,
        "am_trades": am_trades, "over_am": am_trades >= 2,
        "walk_away": total_r > 0 and losses == 0 and total >= 1
    }

    return {
        "date": today, "total": total,
        "wins": wins, "losses": losses, "be": be,
        "total_r": total_r, "win_rate": win_rate,
        "avg_exec": avg_exec, "quality": quality,
        "emotions": emotions, "rules": rules,
        "trades": trades,
        "summary": dict(summary) if summary else None
    }

# ── Daily summary generator ───────────────────────────
@app.post("/summary/generate")
def generate_summary(account_id: int = Form(1)):
    today  = date.today().isoformat()
    conn   = get_db()
    rows   = conn.execute(
        "SELECT * FROM trades WHERE account_id=? AND date(timestamp)=? ORDER BY timestamp",
        (account_id, today)
    ).fetchall()
    trades = [dict(r) for r in rows]
    conn.close()

    if not trades:
        return {"error": "No trades today"}

    total     = len(trades)
    r_trades  = [t for t in trades if t["r_multiple"] is not None]
    total_r   = round(sum(t["r_multiple"] for t in r_trades), 2)
    wins      = sum(1 for t in r_trades if t["r_multiple"] > 0)
    losses    = sum(1 for t in r_trades if t["r_multiple"] < 0)
    win_rate  = round(wins / len(r_trades) * 100) if r_trades else 0
    avg_exec  = sum(t["execution_score"] or 0 for t in trades) / total

    # Auto daily score
    c_setups    = sum(1 for t in trades if t["setup_quality"] == "C")
    bad_emotions= sum(1 for t in trades if t["emotion_state"] in ["revenge","fomo","frustration","overconfidence"])
    if avg_exec >= 7.5 and c_setups == 0 and bad_emotions == 0:
        daily_score = "A"
    elif avg_exec >= 6 and c_setups <= 1:
        daily_score = "B"
    else:
        daily_score = "C"

    # Best setup by R
    setup_r = {}
    for t in r_trades:
        s = t["setup_type"] or "unknown"
        setup_r[s] = setup_r.get(s, 0) + t["r_multiple"]
    best_setup = max(setup_r, key=setup_r.get) if setup_r else None

    # Biggest leak
    leaks = {
        "Entries":      sum(1 for t in trades if t["entry_behavior"] in ["hesitation","late","early","chase"]),
        "Management":   sum(1 for t in trades if t["mgmt_behavior"] in ["cut_early","overheld","moved_stop","scaled_poorly"]),
        "Psychology":   bad_emotions,
        "Overtrading":  1 if total > 3 else 0
    }
    biggest_leak = max(leaks, key=leaks.get)

    # Mistake types
    mistake_counts = {
        "Overtrading":     1 if total > 3 else 0,
        "Hesitation":      sum(1 for t in trades if t["entry_behavior"] == "hesitation"),
        "FOMO":            sum(1 for t in trades if t["emotion_state"] == "fomo"),
        "Revenge":         sum(1 for t in trades if t["emotion_state"] == "revenge"),
        "Rule Breaking":   sum(1 for t in trades if t["setup_quality"] == "C"),
        "Poor Selection":  c_setups,
    }
    top_mistakes = sorted(mistake_counts.items(), key=lambda x: x[1], reverse=True)
    mistake_1 = top_mistakes[0][0] if top_mistakes[0][1] > 0 else None
    mistake_2 = top_mistakes[1][0] if len(top_mistakes) > 1 and top_mistakes[1][1] > 0 else None

    # AI one fix
    one_fix = generate_one_fix(trades, daily_score, biggest_leak)

    # Save summary
    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO daily_summaries
        (account_id, date, daily_score, mistake_1, mistake_2, best_setup,
         biggest_leak, one_fix, total_trades, total_r, win_rate)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        account_id, today, daily_score, mistake_1, mistake_2, best_setup,
        biggest_leak, one_fix, total, total_r, win_rate
    ))
    conn.commit()
    conn.close()

    return {
        "daily_score": daily_score,
        "mistake_1": mistake_1, "mistake_2": mistake_2,
        "best_setup": best_setup, "biggest_leak": biggest_leak,
        "one_fix": one_fix,
        "total": total, "total_r": total_r, "win_rate": win_rate
    }

def generate_one_fix(trades, daily_score, biggest_leak):
    try:
        lines = []
        for t in trades:
            lines.append(f"Setup:{t['setup_type']} Q:{t['setup_quality']} Score:{t['execution_score']} "
                        f"Entry:{t['entry_behavior']} Mgmt:{t['mgmt_behavior']} Exit:{t['exit_type']} "
                        f"Emotion:{t['emotion_state']} R:{t['r_multiple']}")

        prompt = f"""Futures trader daily log. Day grade: {daily_score}. Biggest leak: {biggest_leak}.

Trades:
{chr(10).join(lines)}

Give ONE specific rule for tomorrow. Must be:
- Under 15 words
- Actionable and concrete
- Address the biggest leak
- No preamble, just the rule

Example: "No trades after 2 consecutive losses — walk away immediately."
Response (ONE rule only):"""

        resp = requests.post(OLLAMA_URL, json={
            "model": TEXT_MODEL, "prompt": prompt,
            "stream": False, "options": {"temperature": 0.3, "num_predict": 40}
        }, timeout=30)
        fix = resp.json().get("response", "").strip()
        # Clean up any preamble
        fix = fix.split("\n")[0].strip().strip('"')
        return fix if fix else f"Only take A-quality setups in {biggest_leak.lower()} sessions."
    except:
        return f"Focus on eliminating {biggest_leak.lower()} issues tomorrow."

# ── Analytics ─────────────────────────────────────────
def _get_trades(conn, account_id, days):
    if account_id == 0:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM trades WHERE date(timestamp)>=date('now',?) ORDER BY timestamp",
            (f"-{days} days",)
        ).fetchall()]
    return [dict(r) for r in conn.execute(
        "SELECT * FROM trades WHERE account_id=? AND date(timestamp)>=date('now',?) ORDER BY timestamp",
        (account_id, f"-{days} days")
    ).fetchall()]

@app.get("/analytics")
def analytics(account_id: int = 1, days: int = 30):
    conn   = get_db()
    trades = _get_trades(conn, account_id, days)
    conn.close()
    if not trades: return {"empty": True}

    r_trades = [t for t in trades if t["r_multiple"] is not None]
    total    = len(trades)
    wins     = sum(1 for t in r_trades if t["r_multiple"] > 0)
    losses   = sum(1 for t in r_trades if t["r_multiple"] < 0)
    be_count = sum(1 for t in r_trades if t["r_multiple"] == 0)
    total_r  = round(sum(t["r_multiple"] for t in r_trades), 2)
    win_rate = round(wins / len(r_trades) * 100, 1) if r_trades else 0
    avg_exec = round(sum(t["execution_score"] or 0 for t in trades) / total, 1) if total else 0

    # Performance by setup
    setup_perf = {}
    for t in r_trades:
        s = t["setup_type"] or "unknown"
        if s not in setup_perf:
            setup_perf[s] = {"total": 0, "wins": 0, "losses": 0, "total_r": 0}
        setup_perf[s]["total"] += 1
        setup_perf[s]["total_r"] = round(setup_perf[s]["total_r"] + t["r_multiple"], 2)
        if t["r_multiple"] > 0: setup_perf[s]["wins"] += 1
        if t["r_multiple"] < 0: setup_perf[s]["losses"] += 1
    for s in setup_perf:
        p = setup_perf[s]
        p["wr"] = round(p["wins"] / p["total"] * 100, 1) if p["total"] else 0

    # Performance by quality
    quality_perf = {}
    for t in r_trades:
        q = t["setup_quality"] or "?"
        if q not in quality_perf: quality_perf[q] = {"total":0,"wins":0,"losses":0,"total_r":0}
        quality_perf[q]["total"] += 1
        quality_perf[q]["total_r"] = round(quality_perf[q]["total_r"] + t["r_multiple"], 2)
        if t["r_multiple"] > 0: quality_perf[q]["wins"] += 1
        if t["r_multiple"] < 0: quality_perf[q]["losses"] += 1
    for q in quality_perf:
        p = quality_perf[q]
        p["wr"] = round(p["wins"]/p["total"]*100,1) if p["total"] else 0

    # Execution score bands vs R
    exec_bands = {"≤4":[],"5-6":[],"7-8":[],"9-10":[]}
    for t in r_trades:
        sc = t["execution_score"] or 0
        band = "9-10" if sc>=9 else "7-8" if sc>=7 else "5-6" if sc>=5 else "≤4"
        exec_bands[band].append(t["r_multiple"])
    exec_perf = {}
    for band, rs in exec_bands.items():
        if rs:
            exec_perf[band] = {
                "count": len(rs), "total_r": round(sum(rs),2),
                "avg_r": round(sum(rs)/len(rs),2),
                "wins": sum(1 for r in rs if r>0)
            }

    # Emotion vs R
    emotion_perf = {}
    for t in r_trades:
        e = t["emotion_state"] or "unknown"
        if e not in emotion_perf: emotion_perf[e] = {"total":0,"wins":0,"total_r":0}
        emotion_perf[e]["total"] += 1
        emotion_perf[e]["total_r"] = round(emotion_perf[e]["total_r"]+t["r_multiple"],2)
        if t["r_multiple"]>0: emotion_perf[e]["wins"] += 1
    for e in emotion_perf:
        p = emotion_perf[e]
        p["wr"] = round(p["wins"]/p["total"]*100,1) if p["total"] else 0
        p["avg_r"] = round(p["total_r"]/p["total"],2) if p["total"] else 0

    # Entry behavior vs R
    entry_perf = {}
    for t in r_trades:
        b = t["entry_behavior"] or "unknown"
        if b not in entry_perf: entry_perf[b] = {"total":0,"wins":0,"total_r":0}
        entry_perf[b]["total"] += 1
        entry_perf[b]["total_r"] = round(entry_perf[b]["total_r"]+t["r_multiple"],2)
        if t["r_multiple"]>0: entry_perf[b]["wins"] += 1
    for b in entry_perf:
        p = entry_perf[b]
        p["wr"] = round(p["wins"]/p["total"]*100,1) if p["total"] else 0

    # Sequence analytics
    sorted_t = sorted(trades, key=lambda t: t["timestamp"])
    wal = {"total":0,"wins":0}  # win after loss
    ma1 = {"total":0,"c_setups":0}  # C-setup after 1 loss
    ma2 = {"total":0,"c_setups":0}  # C-setup after 2 losses

    for i, t in enumerate(sorted_t):
        if i == 0: continue
        prev = sorted_t[i-1]
        same = t["timestamp"][:10]==prev["timestamp"][:10] and t["account_id"]==prev["account_id"]
        if not same: continue
        if prev.get("r_multiple") is not None and prev["r_multiple"] < 0:
            wal["total"] += 1
            if t.get("r_multiple") and t["r_multiple"]>0: wal["wins"] += 1
            ma1["total"] += 1
            if t["setup_quality"]=="C": ma1["c_setups"] += 1
        if i>=2:
            prev2=sorted_t[i-2]
            same2=t["timestamp"][:10]==prev2["timestamp"][:10]
            if same2 and prev.get("r_multiple",0)<0 and prev2.get("r_multiple",0)<0:
                ma2["total"] += 1
                if t["setup_quality"]=="C": ma2["c_setups"] += 1

    # Cumulative R
    daily_r = {}
    for t in sorted_t:
        d = t["timestamp"][:10]
        daily_r[d] = round(daily_r.get(d,0) + (t["r_multiple"] or 0), 2)
    cum = 0
    cumulative_r = []
    for d, r in sorted(daily_r.items()):
        cum = round(cum+r, 2)
        cumulative_r.append({"date":d,"daily":r,"cumulative":cum})

    freq = {}
    for t in trades:
        d=t["timestamp"][:10]; freq[d]=freq.get(d,0)+1
    trade_freq=[{"date":k,"count":v} for k,v in sorted(freq.items())]

    return {
        "total": total, "wins": wins, "losses": losses, "be": be_count,
        "win_rate": win_rate, "total_r": total_r, "avg_exec": avg_exec,
        "setup_performance": setup_perf,
        "quality_performance": quality_perf,
        "exec_performance": exec_perf,
        "emotion_performance": emotion_perf,
        "entry_performance": entry_perf,
        "sequence": {
            "win_after_loss": round(wal["wins"]/wal["total"]*100,1) if wal["total"] else None,
            "wal_total": wal["total"],
            "c_rate_after_1loss": round(ma1["c_setups"]/ma1["total"]*100,1) if ma1["total"] else None,
            "c_rate_after_2loss": round(ma2["c_setups"]/ma2["total"]*100,1) if ma2["total"] else None,
        },
        "cumulative_r": cumulative_r,
        "trade_frequency": trade_freq,
    }

# ── AI analysis ───────────────────────────────────────
@app.get("/analyze")
def analyze(account_id: int = 1, days: int = 7):
    conn   = get_db()
    trades = _get_trades(conn, account_id, days)
    conn.close()
    if not trades: return {"analysis": "No trades in this period."}

    r_trades = [t for t in trades if t["r_multiple"] is not None]
    lines = []
    for t in sorted(trades, key=lambda x: x["timestamp"]):
        lines.append(f"[{t['timestamp'][:10]}] T{t['trade_num']} {t.get('setup_type','?')} "
                    f"Q:{t.get('setup_quality','?')} Score:{t.get('execution_score','?')} "
                    f"Entry:{t.get('entry_behavior','?')} Mgmt:{t.get('mgmt_behavior','?')} "
                    f"Emotion:{t.get('emotion_state','?')} R:{t.get('r_multiple','?')}")

    prompt = f"""You are a direct trading performance analyst. Analyze this trader's log.

Their edge formula goal: [best setup] + [A quality] + [execution ≥7] = profitable

{days}-day log ({len(trades)} trades, total R: {round(sum(t['r_multiple'] or 0 for t in r_trades),2)}):
{chr(10).join(lines)}

Respond in this EXACT format:

EDGE IDENTIFIED:
[What setup + quality + score combination actually makes money]

BIGGEST LIABILITY:
[The single behavior costing the most R]

PATTERN:
[One behavioral pattern — be specific with numbers]

TOMORROW'S RULE:
[One rule, under 15 words]

Under 120 words total. No softening."""

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
def get_trades(account_id: int = 1, limit: int = 100, date_filter: Optional[str] = None):
    conn = get_db()
    if account_id == 0:
        where = f" WHERE date(timestamp)='{date_filter}'" if date_filter else ""
        sql = f"SELECT t.*, a.name acc_name, a.color acc_color FROM trades t LEFT JOIN accounts a ON t.account_id=a.id{where} ORDER BY timestamp DESC LIMIT {limit}"
    else:
        cond = f" AND date(timestamp)='{date_filter}'" if date_filter else ""
        sql = f"SELECT t.*, a.name acc_name, a.color acc_color FROM trades t LEFT JOIN accounts a ON t.account_id=a.id WHERE t.account_id={account_id}{cond} ORDER BY timestamp DESC LIMIT {limit}"
    rows = conn.execute(sql).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.delete("/trade/{tid}")
def delete_trade(tid: int):
    conn = get_db()
    conn.execute("DELETE FROM trades WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    return {"success": True}

@app.get("/summaries")
def get_summaries(account_id: int = 1, limit: int = 30):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM daily_summaries WHERE account_id=? ORDER BY date DESC LIMIT ?",
        (account_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.on_event("startup")
def startup():
    bat = Path("start_journal.bat")
    if not bat.exists():
        cwd = Path.cwd().resolve()
        bat.write_text(f"@echo off\ncd /d \"{cwd}\"\npython main.py\npause\n")
        print(f"✅ start_journal.bat created — add to shell:startup")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
