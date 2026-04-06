"""
Trading Journal v6 — The Craftsman's Mirror
Built for: High-agency, identity-driven, pattern-recognition brain
Design principles:
  - Zero friction on bad days (5-second minimum log)
  - Identity language, not task language
  - Data mirror, not report card
  - Pattern recognition over daily scores
  - Shame-free architecture
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import sqlite3, shutil, requests, json, base64, re
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
VISION_MODEL = "moondream"

# ── DB ────────────────────────────────────────────────
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
        INSERT OR IGNORE INTO accounts (id,name,color) VALUES (1,'Main','#4ade80');

        CREATE TABLE IF NOT EXISTS trades (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id       INTEGER DEFAULT 1,
            timestamp        TEXT NOT NULL,
            session_num      INTEGER DEFAULT 1,
            image_path       TEXT,

            -- Core (must be fast — designed for Mode B brain)
            state            TEXT,   -- locked_in, off, neutral, tilt
            setup_type       TEXT,   -- trend_cont, reversal, range, breakout, liq_sweep
            quality          TEXT,   -- A, B, C
            r_multiple       REAL,

            -- Behavioral (one step beyond minimum)
            entry_behavior   TEXT,   -- decisive, hesitation, late, early, chase
            mgmt_behavior    TEXT,   -- plan, cut_early, let_run, overheld, moved_stop
            exit_type        TEXT,   -- target, stopped, manual_plan, manual_emo

            -- Context (optional — collapsed by default)
            direction        TEXT,
            session          TEXT,
            htf_aligned      TEXT,   -- yes, no
            execution_score  INTEGER,
            liq_flow         TEXT,
            position_size    REAL,
            instrument       TEXT DEFAULT 'MNQ',

            -- Environment (optional — inferred trait tracking)
            physical_state   TEXT,   -- fresh, tired, hungry, wired
            sleep_quality    TEXT,   -- good, ok, poor

            -- AI extracted
            ai_direction     TEXT,
            ai_price         TEXT,
            ai_notes         TEXT,

            notes            TEXT,
            created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER DEFAULT 1,
            date            TEXT NOT NULL,
            session_grade   TEXT,
            craftsman_score INTEGER,
            dominant_state  TEXT,
            biggest_leak    TEXT,
            pattern_note    TEXT,
            one_signal      TEXT,
            total_r         REAL,
            win_rate        REAL,
            trade_count     INTEGER,
            ai_mirror       TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(account_id, date)
        );

        CREATE TABLE IF NOT EXISTS streaks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER DEFAULT 1,
            type       TEXT,   -- logged, clean_day, no_c_setups
            date       TEXT,
            UNIQUE(account_id, type, date)
        );
    """)
    conn.commit()
    conn.close()

init_db()

app.mount("/images", StaticFiles(directory="images"), name="images")

@app.get("/")
def root(): return FileResponse("index.html")

# ── System ────────────────────────────────────────────
@app.get("/system")
def system():
    try:
        resp   = requests.get(OLLAMA_TAGS, timeout=4)
        models = [m["name"].split(":")[0] for m in resp.json().get("models", [])]
        return {
            "ollama":  True,
            "text":    TEXT_MODEL in models,
            "vision":  VISION_MODEL in models,
            "vision_cmd": f"ollama pull {VISION_MODEL}",
            "vision_size": "~1.7GB"
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
    cur  = conn.execute("INSERT INTO accounts (name,color) VALUES (?,?)", (name,color))
    conn.commit()
    aid  = cur.lastrowid
    conn.close()
    return {"id": aid, "name": name, "color": color}

@app.delete("/accounts/{aid}")
def delete_account(aid: int):
    conn  = get_db()
    count = conn.execute("SELECT COUNT(*) FROM trades WHERE account_id=?", (aid,)).fetchone()[0]
    if count:
        conn.close()
        raise HTTPException(400, f"Account has {count} trades.")
    conn.execute("DELETE FROM accounts WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    return {"success": True}

# ── Tags ──────────────────────────────────────────────
@app.get("/tags")
def get_tags():
    return {
        # State — the fastest possible emotional check-in
        "state": [
            {"id": "locked_in", "label": "Locked In",  "icon": "🔒", "color": "green"},
            {"id": "neutral",   "label": "Neutral",     "icon": "😐", "color": "blue"},
            {"id": "off",       "label": "Off Today",   "icon": "🌫️", "color": "yellow"},
            {"id": "tilt",      "label": "Tilt",        "icon": "🔥", "color": "red"},
        ],
        "setup": [
            {"id": "liq_sweep",  "label": "Liq. Sweep",    "icon": "🌊"},
            {"id": "trend_cont", "label": "Trend Cont.",    "icon": "📈"},
            {"id": "reversal",   "label": "Reversal",       "icon": "🔄"},
            {"id": "breakout",   "label": "Breakout",       "icon": "💥"},
            {"id": "range",      "label": "Range",          "icon": "↔️"},
        ],
        "quality": [
            {"id": "A", "label": "A",    "desc": "Clean. High confidence."},
            {"id": "B", "label": "B",    "desc": "Valid but not ideal."},
            {"id": "C", "label": "C",    "desc": "Forced. You know it."},
        ],
        "r_presets": ["+3R","+2R","+1.5R","+1R","+0.5R","BE","-0.5R","-1R","-2R"],
        "entry_behavior": [
            {"id": "decisive",   "label": "Decisive",   "good": True},
            {"id": "hesitation", "label": "Hesitation", "good": False},
            {"id": "late",       "label": "Late",       "good": False},
            {"id": "early",      "label": "Early",      "good": False},
            {"id": "chase",      "label": "Chase",      "good": False},
        ],
        "mgmt_behavior": [
            {"id": "plan",       "label": "Followed Plan", "good": True},
            {"id": "let_run",    "label": "Let Run",       "good": True},
            {"id": "cut_early",  "label": "Cut Early",     "good": False},
            {"id": "overheld",   "label": "Overheld",      "good": False},
            {"id": "moved_stop", "label": "Moved Stop",    "good": False},
        ],
        "exit_type": [
            {"id": "target",      "label": "Target Hit",    "good": True},
            {"id": "stopped",     "label": "Stopped Out",   "good": None},
            {"id": "manual_plan", "label": "Exit (Planned)","good": True},
            {"id": "manual_emo",  "label": "Exit (Emotion)","good": False},
        ],
        "physical_state": [
            {"id": "fresh",   "label": "Fresh"},
            {"id": "tired",   "label": "Tired"},
            {"id": "wired",   "label": "Wired"},
            {"id": "hungry",  "label": "Hungry"},
        ],
        "sleep": [
            {"id": "good", "label": "Good"},
            {"id": "ok",   "label": "OK"},
            {"id": "poor", "label": "Poor"},
        ],
        "instruments": ["MNQ","NQ","ES","MES","YM","MYM","CL","GC"],
    }

# ── Vision ────────────────────────────────────────────
def analyze_chart(image_path: str) -> dict:
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        prompt = ("Describe this trading chart briefly. "
                  "What direction is the trade? What price? What time? "
                  "JSON only: {\"direction\": \"long/short\", \"price\": \"price\", "
                  "\"time\": \"HH:MM\", \"notes\": \"one sentence\"}")
        resp = requests.post(OLLAMA_URL, json={
            "model": VISION_MODEL, "prompt": prompt,
            "images": [img_b64], "stream": False,
            "options": {"temperature": 0.1}
        }, timeout=60)
        result = resp.json().get("response","")
        m = re.search(r'\{.*?\}', result, re.DOTALL)
        return json.loads(m.group()) if m else {"notes": result[:200]}
    except requests.ConnectionError:
        return {"error": "Ollama not running"}
    except Exception as e:
        return {"error": str(e)}

# ── Session number ────────────────────────────────────
def get_session_num(conn, account_id):
    today = date.today().isoformat()
    return conn.execute(
        "SELECT COUNT(*) FROM trades WHERE account_id=? AND date(timestamp)=?",
        (account_id, today)
    ).fetchone()[0] + 1

# ── Log trade ─────────────────────────────────────────
@app.post("/trade")
async def log_trade(
    account_id:      int            = Form(1),
    state:           Optional[str]  = Form(None),
    setup_type:      Optional[str]  = Form(None),
    quality:         Optional[str]  = Form(None),
    r_multiple:      Optional[float]= Form(None),
    entry_behavior:  Optional[str]  = Form(None),
    mgmt_behavior:   Optional[str]  = Form(None),
    exit_type:       Optional[str]  = Form(None),
    direction:       Optional[str]  = Form(None),
    session:         Optional[str]  = Form(None),
    htf_aligned:     Optional[str]  = Form(None),
    execution_score: Optional[int]  = Form(None),
    liq_flow:        Optional[str]  = Form(None),
    position_size:   Optional[float]= Form(None),
    instrument:      Optional[str]  = Form("MNQ"),
    physical_state:  Optional[str]  = Form(None),
    sleep_quality:   Optional[str]  = Form(None),
    notes:           Optional[str]  = Form(None),
    run_vision:      int            = Form(0),
    image:           Optional[UploadFile] = File(None)
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

    conn = get_db()
    snum = get_session_num(conn, account_id)

    conn.execute("""
        INSERT INTO trades (
            account_id, timestamp, session_num, image_path,
            state, setup_type, quality, r_multiple,
            entry_behavior, mgmt_behavior, exit_type,
            direction, session, htf_aligned, execution_score, liq_flow,
            position_size, instrument, physical_state, sleep_quality, notes,
            ai_direction, ai_price, ai_notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        account_id, timestamp, snum, image_path,
        state, setup_type, quality, r_multiple,
        entry_behavior, mgmt_behavior, exit_type,
        direction or ai_data.get("direction"),
        session, htf_aligned, execution_score, liq_flow,
        position_size, instrument, physical_state, sleep_quality, notes,
        ai_data.get("direction"), ai_data.get("price"), ai_data.get("notes")
    ))

    # Mark as logged today for streak
    today = date.today().isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO streaks (account_id,type,date) VALUES (?,?,?)",
        (account_id, "logged", today)
    )
    if quality and quality != "C":
        conn.execute(
            "INSERT OR IGNORE INTO streaks (account_id,type,date) VALUES (?,?,?)",
            (account_id, "no_c_setups", today)
        )

    conn.commit()
    conn.close()
    return {"success": True, "session_num": snum, "ai_data": ai_data}

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
    session_data = conn.execute(
        "SELECT * FROM sessions WHERE account_id=? AND date=?",
        (account_id, today)
    ).fetchone()

    # Streaks
    log_streak   = _calc_streak(conn, account_id, "logged")
    clean_streak = _calc_streak(conn, account_id, "no_c_setups")
    conn.close()

    r_trades = [t for t in trades if t["r_multiple"] is not None]
    total    = len(trades)
    total_r  = round(sum(t["r_multiple"] for t in r_trades), 2)
    wins     = sum(1 for t in r_trades if t["r_multiple"] > 0)
    losses   = sum(1 for t in r_trades if t["r_multiple"] < 0)
    be       = sum(1 for t in r_trades if t["r_multiple"] == 0)
    win_rate = round(wins/len(r_trades)*100) if r_trades else 0

    quality_counts = {"A": 0, "B": 0, "C": 0}
    for t in trades:
        q = t["quality"]
        if q in quality_counts: quality_counts[q] += 1

    state_counts = {}
    for t in trades:
        s = t["state"] or "unknown"
        state_counts[s] = state_counts.get(s, 0) + 1

    # Session rules
    am_count = sum(1 for t in trades if t.get("session") in ["ny_am","ny_pre"])
    rules = {
        "total": total, "over_3": total >= 3,
        "am": am_count, "over_am": am_count >= 2,
        "c_count": quality_counts["C"],
        "tilt_flag": state_counts.get("tilt", 0) > 0,
        "walk_away": total_r > 0 and losses == 0 and total >= 1
    }

    return {
        "date": today, "total": total,
        "wins": wins, "losses": losses, "be": be,
        "total_r": total_r, "win_rate": win_rate,
        "quality": quality_counts, "states": state_counts,
        "rules": rules, "trades": trades,
        "log_streak": log_streak, "clean_streak": clean_streak,
        "session": dict(session_data) if session_data else None
    }

def _calc_streak(conn, account_id, streak_type):
    rows = conn.execute(
        "SELECT date FROM streaks WHERE account_id=? AND type=? ORDER BY date DESC",
        (account_id, streak_type)
    ).fetchall()
    if not rows: return 0
    streak = 0
    check  = date.today()
    for row in rows:
        d = date.fromisoformat(row["date"])
        if d == check or d == check - timedelta(days=1 if streak > 0 else 0):
            streak += 1
            check   = d - timedelta(days=1)
        else:
            break
    return streak

# ── Session close ─────────────────────────────────────
@app.post("/session/close")
def close_session(account_id: int = Form(1)):
    today  = date.today().isoformat()
    conn   = get_db()
    rows   = conn.execute(
        "SELECT * FROM trades WHERE account_id=? AND date(timestamp)=? ORDER BY timestamp",
        (account_id, today)
    ).fetchall()
    trades = [dict(r) for r in rows]
    conn.close()

    if not trades: return {"error": "No trades today"}

    r_trades  = [t for t in trades if t["r_multiple"] is not None]
    total     = len(trades)
    total_r   = round(sum(t["r_multiple"] for t in r_trades), 2)
    wins      = sum(1 for t in r_trades if t["r_multiple"] > 0)
    win_rate  = round(wins/len(r_trades)*100) if r_trades else 0

    # Craftsman score (0-100, not a grade — a measure)
    quality_score  = sum({"A":100,"B":65,"C":20}.get(t.get("quality","C"),50) for t in trades) / total
    exec_scores    = [t["execution_score"] for t in trades if t.get("execution_score")]
    exec_score     = sum(exec_scores)/len(exec_scores)*10 if exec_scores else 50
    behavior_score = sum(100 if t.get("entry_behavior")=="decisive" else 40 for t in trades) / total
    c_penalty      = sum(1 for t in trades if t.get("quality")=="C") * 10
    craftsman_score= max(0, min(100, round((quality_score*0.5 + exec_score*0.3 + behavior_score*0.2) - c_penalty)))

    # Session grade
    c_count   = sum(1 for t in trades if t.get("quality")=="C")
    bad_state = sum(1 for t in trades if t.get("state") in ["tilt"])
    if craftsman_score >= 75 and c_count == 0:
        grade = "A"
    elif craftsman_score >= 55 and c_count <= 1:
        grade = "B"
    else:
        grade = "C"

    # Dominant state
    state_counts = {}
    for t in trades:
        s = t["state"] or "neutral"
        state_counts[s] = state_counts.get(s, 0) + 1
    dominant_state = max(state_counts, key=state_counts.get) if state_counts else "neutral"

    # Biggest leak
    leaks = {
        "Entries":    sum(1 for t in trades if t.get("entry_behavior") in ["hesitation","late","early","chase"]),
        "Management": sum(1 for t in trades if t.get("mgmt_behavior") in ["cut_early","overheld","moved_stop"]),
        "Psychology": bad_state + c_count,
        "Overtrading":1 if total > 3 else 0,
    }
    biggest_leak = max(leaks, key=leaks.get)

    # Pattern note
    pattern_note = _derive_pattern(trades, r_trades)

    # AI mirror (short, identity-language)
    ai_mirror = _generate_mirror(trades, r_trades, grade, biggest_leak, dominant_state)

    # One signal
    one_signal = _generate_signal(trades, biggest_leak, grade)

    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO sessions
        (account_id, date, session_grade, craftsman_score, dominant_state,
         biggest_leak, pattern_note, one_signal, total_r, win_rate, trade_count, ai_mirror)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        account_id, today, grade, craftsman_score, dominant_state,
        biggest_leak, pattern_note, one_signal, total_r, win_rate, total, ai_mirror
    ))
    # Mark clean day if A or B
    if grade in ["A","B"]:
        conn.execute(
            "INSERT OR IGNORE INTO streaks (account_id,type,date) VALUES (?,?,?)",
            (account_id, "clean_day", today)
        )
    conn.commit()
    conn.close()

    return {
        "grade": grade, "craftsman_score": craftsman_score,
        "dominant_state": dominant_state, "biggest_leak": biggest_leak,
        "pattern_note": pattern_note, "one_signal": one_signal,
        "total_r": total_r, "win_rate": win_rate,
        "ai_mirror": ai_mirror
    }

def _derive_pattern(trades, r_trades):
    """Generate a pattern observation from the session data"""
    c_setups = sum(1 for t in trades if t.get("quality") == "C")
    tilt     = sum(1 for t in trades if t.get("state") == "tilt")
    chases   = sum(1 for t in trades if t.get("entry_behavior") == "chase")
    r_after_loss = []
    sorted_r = [t for t in sorted(trades, key=lambda x: x["timestamp"]) if t.get("r_multiple") is not None]
    for i, t in enumerate(sorted_r):
        if i > 0 and sorted_r[i-1]["r_multiple"] < 0:
            r_after_loss.append(t["r_multiple"])

    if tilt > 0 and c_setups > 0:
        return f"Tilt state coincided with {c_setups} C-setup{'s' if c_setups>1 else ''} today."
    if chases > 1:
        return f"{chases} chase entries. The market moved before the entry."
    if r_after_loss and sum(r_after_loss) < 0:
        return f"Trades taken after a loss averaged {round(sum(r_after_loss)/len(r_after_loss),1)}R. Pattern: post-loss decisions cost you."
    if c_setups == 0 and len(trades) >= 2:
        return f"Clean quality today — {len(trades)} trades, no C setups logged."
    return "Session logged. Pattern analysis requires more data points."

def _generate_mirror(trades, r_trades, grade, biggest_leak, dominant_state):
    """Short, identity-language AI feedback — 2-3 sentences max"""
    try:
        total_r = round(sum(t["r_multiple"] for t in r_trades), 2)
        lines   = []
        for t in trades:
            lines.append(f"State:{t.get('state','?')} Q:{t.get('quality','?')} "
                        f"Entry:{t.get('entry_behavior','?')} Mgmt:{t.get('mgmt_behavior','?')} "
                        f"R:{t.get('r_multiple','?')}")

        prompt = f"""You are a direct, perceptive trading coach. Trader grade: {grade}. State: {dominant_state}. Biggest leak: {biggest_leak}. Total R: {total_r}.

Trades:
{chr(10).join(lines)}

Write exactly 2 sentences. First sentence: what their data actually shows about their behavior (factual, no judgment). Second sentence: what this means for their identity as a craftsman (use "craftsman" framing, not "trader" framing). 
No motivation. No praise. No punishment. Just pattern recognition.
Example tone: "Your A-quality entries generated all the positive R today. The C-setups were the variable — remove them and the craftsman is already there."
Response (2 sentences only):"""

        resp = requests.post(OLLAMA_URL, json={
            "model": TEXT_MODEL, "prompt": prompt,
            "stream": False, "options": {"temperature": 0.3, "num_predict": 80}
        }, timeout=30)
        mirror = resp.json().get("response","").strip()
        # Clean to 2 sentences max
        sentences = [s.strip() for s in mirror.split(".") if s.strip()]
        return ". ".join(sentences[:2]) + "." if sentences else mirror
    except:
        return "Pattern data logged. The mirror builds with more sessions."

def _generate_signal(trades, biggest_leak, grade):
    """One signal for tomorrow — concrete, short"""
    leak_signals = {
        "Entries":    "Only enter on decisive execution. If you hesitate, the setup is gone.",
        "Management": "Set the stop. Walk away from the screen. The plan was made pre-trade.",
        "Psychology": "State check before every trade. Tilt is not a starting point — it's a stop sign.",
        "Overtrading":"Three trades is the structure. The fourth is always the leak.",
    }
    if grade == "A":
        return "Repeat what you did today. Document it before the next session."
    return leak_signals.get(biggest_leak, "One trade at a time. The craftsman doesn't rush.")

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

    # Session history
    sessions = [dict(r) for r in conn.execute(
        "SELECT * FROM sessions WHERE account_id=? ORDER BY date DESC LIMIT 30",
        (account_id,)
    ).fetchall()]

    # Streak data
    log_streak   = _calc_streak(conn, account_id, "logged")
    clean_streak = _calc_streak(conn, account_id, "clean_day")
    conn.close()

    if not trades: return {"empty": True, "log_streak": log_streak, "clean_streak": clean_streak}

    r_trades = [t for t in trades if t["r_multiple"] is not None]
    total    = len(trades)
    wins     = sum(1 for t in r_trades if t["r_multiple"] > 0)
    losses   = sum(1 for t in r_trades if t["r_multiple"] < 0)
    total_r  = round(sum(t["r_multiple"] for t in r_trades), 2)
    win_rate = round(wins/len(r_trades)*100, 1) if r_trades else 0

    # Quality performance — the core insight
    qperf = {"A":{"total":0,"wins":0,"total_r":0},"B":{"total":0,"wins":0,"total_r":0},"C":{"total":0,"wins":0,"total_r":0}}
    for t in r_trades:
        q = t.get("quality","C")
        if q in qperf:
            qperf[q]["total"] += 1
            qperf[q]["total_r"] = round(qperf[q]["total_r"] + t["r_multiple"], 2)
            if t["r_multiple"] > 0: qperf[q]["wins"] += 1
    for q in qperf:
        p = qperf[q]
        p["wr"]    = round(p["wins"]/p["total"]*100, 1) if p["total"] else 0
        p["avg_r"] = round(p["total_r"]/p["total"], 2) if p["total"] else 0

    # Edge statement generator
    edge_statement = _generate_edge_statement(qperf, r_trades)

    # State vs R
    state_perf = {}
    for t in r_trades:
        s = t.get("state","neutral")
        if s not in state_perf: state_perf[s] = {"total":0,"wins":0,"total_r":0}
        state_perf[s]["total"] += 1
        state_perf[s]["total_r"] = round(state_perf[s]["total_r"]+t["r_multiple"],2)
        if t["r_multiple"]>0: state_perf[s]["wins"] += 1
    for s in state_perf:
        p = state_perf[s]
        p["wr"]    = round(p["wins"]/p["total"]*100,1) if p["total"] else 0
        p["avg_r"] = round(p["total_r"]/p["total"],2) if p["total"] else 0

    # Behavior leaks
    def behavior_cost(key, bad_values):
        bad  = [t for t in r_trades if t.get(key) in bad_values]
        good = [t for t in r_trades if t.get(key) and t.get(key) not in bad_values]
        bad_r  = round(sum(t["r_multiple"] for t in bad)/len(bad),2) if bad else None
        good_r = round(sum(t["r_multiple"] for t in good)/len(good),2) if good else None
        return {"bad_avg_r":bad_r, "good_avg_r":good_r, "bad_count":len(bad), "good_count":len(good)}

    entry_leak = behavior_cost("entry_behavior", ["hesitation","late","early","chase"])
    mgmt_leak  = behavior_cost("mgmt_behavior",  ["cut_early","overheld","moved_stop"])

    # Sequence
    sorted_t = sorted(r_trades, key=lambda t: t["timestamp"])
    wal = {"total":0,"wins":0}
    csl = {"total":0,"c_count":0}
    for i, t in enumerate(sorted_t):
        if i==0: continue
        prev = sorted_t[i-1]
        same = t["timestamp"][:10]==prev["timestamp"][:10]
        if same and prev["r_multiple"] < 0:
            wal["total"] += 1
            if t["r_multiple"]>0: wal["wins"] += 1
        if same and prev["r_multiple"] < 0:
            csl["total"] += 1
            if t.get("quality")=="C": csl["c_count"] += 1

    # Setup performance
    setup_perf = {}
    for t in r_trades:
        s = t.get("setup_type","unknown")
        if s not in setup_perf: setup_perf[s] = {"total":0,"wins":0,"total_r":0}
        setup_perf[s]["total"] += 1
        setup_perf[s]["total_r"] = round(setup_perf[s]["total_r"]+t["r_multiple"],2)
        if t["r_multiple"]>0: setup_perf[s]["wins"] += 1
    for s in setup_perf:
        p = setup_perf[s]
        p["wr"] = round(p["wins"]/p["total"]*100,1) if p["total"] else 0

    # Cumulative R
    daily = {}
    for t in sorted(trades, key=lambda x: x["timestamp"]):
        d = t["timestamp"][:10]
        daily[d] = round(daily.get(d,0)+(t["r_multiple"] or 0),2)
    cum = 0
    cumulative_r = []
    for d, r in sorted(daily.items()):
        cum = round(cum+r,2)
        cumulative_r.append({"date":d,"daily":r,"cumulative":cum})

    freq = {}
    for t in trades:
        d=t["timestamp"][:10]; freq[d]=freq.get(d,0)+1
    trade_freq = [{"date":k,"count":v} for k,v in sorted(freq.items())]

    # Physical state correlation
    phys_perf = {}
    for t in r_trades:
        p = t.get("physical_state","unknown")
        if p not in phys_perf: phys_perf[p] = {"total":0,"total_r":0}
        phys_perf[p]["total"] += 1
        phys_perf[p]["total_r"] = round(phys_perf[p]["total_r"]+t["r_multiple"],2)
    for p in phys_perf:
        pp = phys_perf[p]
        pp["avg_r"] = round(pp["total_r"]/pp["total"],2) if pp["total"] else 0

    return {
        "total": total, "wins": wins, "losses": losses,
        "win_rate": win_rate, "total_r": total_r,
        "quality_performance": qperf,
        "edge_statement": edge_statement,
        "state_performance": state_perf,
        "entry_leak": entry_leak,
        "mgmt_leak": mgmt_leak,
        "setup_performance": setup_perf,
        "sequence": {
            "win_after_loss": round(wal["wins"]/wal["total"]*100,1) if wal["total"] else None,
            "c_rate_after_loss": round(csl["c_count"]/csl["total"]*100,1) if csl["total"] else None,
            "wal_sample": wal["total"],
        },
        "physical_state": phys_perf,
        "cumulative_r": cumulative_r,
        "trade_freq": trade_freq,
        "sessions": sessions,
        "log_streak": log_streak,
        "clean_streak": clean_streak,
    }

def _generate_edge_statement(qperf, r_trades):
    """The most important output — what is this person's actual edge"""
    a = qperf.get("A",{})
    c = qperf.get("C",{})
    if a.get("total",0) < 3:
        return "Need more A-quality trades to define your edge. Keep logging."
    edge = f"A-quality setups: {a['wr']}% win rate, {'+' if a['total_r']>=0 else ''}{a['total_r']}R total."
    if c.get("total",0) > 0:
        edge += f" C-setups: {c['total_r']}R. That's the cost of the leak."
    return edge

# ── AI analysis ───────────────────────────────────────
@app.get("/analyze")
def analyze(account_id: int = 1, days: int = 7):
    conn   = get_db()
    trades = _get_trades(conn, account_id, days)
    conn.close()
    if not trades: return {"analysis": "No trades in this period."}

    r_trades = [t for t in trades if t["r_multiple"] is not None]
    total_r  = round(sum(t["r_multiple"] for t in r_trades),2)
    lines    = []
    for t in sorted(trades, key=lambda x: x["timestamp"]):
        lines.append(f"[{t['timestamp'][:10]}] #{t['session_num']} "
                    f"State:{t.get('state','?')} Q:{t.get('quality','?')} "
                    f"Setup:{t.get('setup_type','?')} Entry:{t.get('entry_behavior','?')} "
                    f"Mgmt:{t.get('mgmt_behavior','?')} R:{t.get('r_multiple','?')}")

    prompt = f"""You are analyzing a craftsman-trader's log. Your job is pattern recognition, not motivation.

{days}-day record ({len(trades)} trades, {total_r}R total):
{chr(10).join(lines)}

Write exactly 3 short statements in this format:

YOUR EDGE: [What quality + setup combination generates positive R — state it as fact]
YOUR LEAK: [The single behavioral pattern costing the most R — be specific]
YOUR SIGNAL: [One concrete change, framed as a craftsman would think about it — under 15 words]

No praise. No punishment. No "you should." Just pattern recognition stated as fact.
Speak directly, like someone who has studied this person's data carefully."""

    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": TEXT_MODEL, "prompt": prompt,
            "stream": False, "options": {"temperature": 0.2}
        }, timeout=60)
        return {"analysis": resp.json().get("response","No response")}
    except Exception as e:
        return {"analysis": f"Ollama unavailable: {e}"}

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

@app.on_event("startup")
def startup():
    bat = Path("start_journal.bat")
    if not bat.exists():
        cwd = Path.cwd().resolve()
        bat.write_text(f"@echo off\ncd /d \"{cwd}\"\npython main.py\npause\n")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
