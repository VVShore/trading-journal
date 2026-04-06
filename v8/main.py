"""
Trading Journal v8 — Complete Backend
Fresh build. Bridge integrated. R optional.
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import sqlite3, shutil, requests, json, base64, re, subprocess, os, threading
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
BRIDGE_SCRIPT = Path(__file__).parent / "chart_bridge.js"
BRIDGE_TIMEOUT = 20

# ── Favicon ───────────────────────────────────────────
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="6" fill="#0c0c0f"/>'
        '<text x="16" y="22" font-size="18" text-anchor="middle" fill="#818cf8">M</text>'
        '</svg>'
    )
    return Response(content=svg.encode("utf-8"), media_type="image/svg+xml")

# ── DB ────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            color TEXT DEFAULT '#818cf8',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO accounts (id,name,color) VALUES (1,'Main','#818cf8');

        CREATE TABLE IF NOT EXISTS trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER DEFAULT 1,
            timestamp       TEXT NOT NULL,
            session_num     INTEGER DEFAULT 1,
            image_path      TEXT,

            -- Core
            state           TEXT,
            setup_type      TEXT,
            quality         TEXT,
            entry_behavior  TEXT,
            mgmt_behavior   TEXT,
            result          TEXT,
            r_multiple      REAL,
            execution_score INTEGER,

            -- Optional context
            direction       TEXT,
            session         TEXT,
            htf_aligned     TEXT,
            liq_flow        TEXT,
            position_size   REAL,
            instrument      TEXT DEFAULT 'MNQ',
            physical_state  TEXT,
            sleep_quality   TEXT,

            -- Chart bridge data
            chart_symbol    TEXT,
            chart_tf        TEXT,
            chart_price     REAL,
            chart_levels    TEXT,

            -- Async vision
            ai_direction    TEXT,
            ai_price        TEXT,
            ai_time         TEXT,
            ai_notes        TEXT,
            ai_processed    INTEGER DEFAULT 1,

            notes           TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id      INTEGER DEFAULT 1,
            date            TEXT NOT NULL,
            grade           TEXT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER DEFAULT 1,
            type TEXT,
            date TEXT,
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
    info = {"ollama": False, "text": False, "vision": False,
            "bridge": BRIDGE_SCRIPT.exists(),
            "vision_cmd": f"ollama pull {VISION_MODEL}"}
    try:
        resp   = requests.get(OLLAMA_TAGS, timeout=4)
        models = [m["name"].split(":")[0] for m in resp.json().get("models", [])]
        info.update({"ollama": True, "text": TEXT_MODEL in models, "vision": VISION_MODEL in models})
    except: pass
    return info

# ── Accounts ──────────────────────────────────────────
@app.get("/accounts")
def get_accounts():
    conn = get_db()
    rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/accounts")
def create_account(name: str = Form(...), color: str = Form('#818cf8')):
    conn = get_db()
    cur  = conn.execute("INSERT INTO accounts (name,color) VALUES (?,?)", (name, color))
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
    conn.commit(); conn.close()
    return {"success": True}

# ── Tags ──────────────────────────────────────────────
@app.get("/tags")
def get_tags():
    return {
        "state": [
            {"id": "locked_in", "label": "Locked In",  "icon": "🔒"},
            {"id": "neutral",   "label": "Neutral",     "icon": "😐"},
            {"id": "off",       "label": "Off Today",   "icon": "🌫️"},
            {"id": "tilt",      "label": "Tilt",        "icon": "🔥"},
        ],
        "setup": [
            {"id": "liq_sweep",  "label": "Liq. Sweep",  "icon": "🌊"},
            {"id": "trend_cont", "label": "Trend Cont.", "icon": "📈"},
            {"id": "reversal",   "label": "Reversal",    "icon": "🔄"},
            {"id": "breakout",   "label": "Breakout",    "icon": "💥"},
            {"id": "range",      "label": "Range",       "icon": "↔️"},
        ],
        "quality": [
            {"id": "A", "desc": "Clean. High confidence."},
            {"id": "B", "desc": "Valid but not ideal."},
            {"id": "C", "desc": "Forced. You know it."},
        ],
        "result": [
            {"id": "win",  "label": "Win",  "color": "g"},
            {"id": "loss", "label": "Loss", "color": "r"},
            {"id": "be",   "label": "BE",   "color": "y"},
        ],
        "entry": [
            {"id": "decisive",   "label": "Decisive",   "good": True},
            {"id": "hesitation", "label": "Hesitation", "good": False},
            {"id": "late",       "label": "Late",       "good": False},
            {"id": "early",      "label": "Early",      "good": False},
            {"id": "chase",      "label": "Chase",      "good": False},
        ],
        "mgmt": [
            {"id": "plan",       "label": "Followed Plan", "good": True},
            {"id": "let_run",    "label": "Let Run",       "good": True},
            {"id": "cut_early",  "label": "Cut Early",     "good": False},
            {"id": "overheld",   "label": "Overheld",      "good": False},
            {"id": "moved_stop", "label": "Moved Stop",    "good": False},
        ],
        "r_presets": ["+3R","+2R","+1.5R","+1R","+0.5R","BE","-0.5R","-1R","-2R"],
        "session":   [
            {"id": "ny_am",  "label": "NY AM"},
            {"id": "ny_pre", "label": "NY Pre"},
            {"id": "london", "label": "London"},
            {"id": "ny_pm",  "label": "NY PM"},
            {"id": "asia",   "label": "Asia"},
        ],
        "liq_flow": [
            {"id": "erl_to_irl",   "label": "ERL → IRL"},
            {"id": "irl_to_erl",   "label": "IRL → ERL"},
            {"id": "continuation", "label": "Continuation"},
            {"id": "random",       "label": "No Clear Flow"},
        ],
        "physical": [
            {"id": "fresh",  "label": "Fresh"},
            {"id": "tired",  "label": "Tired"},
            {"id": "wired",  "label": "Wired"},
            {"id": "hungry", "label": "Hungry"},
        ],
        "sleep": [
            {"id": "good", "label": "Good"},
            {"id": "ok",   "label": "OK"},
            {"id": "poor", "label": "Poor"},
        ],
        "instruments": ["MNQ","NQ","ES","MES","YM","MYM","CL","GC"],
    }

# ── Chart Bridge ──────────────────────────────────────
def _run_bridge(command: str, extra_arg: str = None, timeout: int = BRIDGE_TIMEOUT) -> dict:
    if not BRIDGE_SCRIPT.exists():
        return {"error": "chart_bridge.js not found in journal folder."}
    args = ["node", str(BRIDGE_SCRIPT), command]
    if extra_arg:
        args.append(extra_arg)
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                               timeout=timeout, cwd=str(BRIDGE_SCRIPT.parent))
        output = result.stdout.strip()
        if not output:
            return {"error": result.stderr[:300] if result.stderr else "No output from bridge"}
        for line in reversed(output.split("\n")):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        return json.loads(output)
    except subprocess.TimeoutExpired:
        return {"error": f"Timed out after {timeout}s. Is TradingView running with debug port?"}
    except FileNotFoundError:
        return {"error": "Node.js not found. Install from nodejs.org"}
    except json.JSONDecodeError as e:
        return {"error": f"Parse error: {e}"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/chart/status")
def chart_status():
    return _run_bridge("status", timeout=5)

@app.get("/chart/read")
def chart_read():
    return _run_bridge("read", timeout=18)

@app.post("/chart/screenshot")
async def chart_screenshot():
    import time
    fname     = f"tv_{int(time.time()*1000)}.png"
    save_path = str(IMAGES_DIR / fname)
    result    = _run_bridge("screenshot", save_path, timeout=15)
    if result.get("success") and Path(save_path).exists():
        return {"success": True, "path": save_path, "filename": fname}
    return {"success": False, "error": result.get("error", "Screenshot failed")}

# ── Vision (async) ────────────────────────────────────
def _vision_async(trade_id: int, image_path: str):
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        prompt = ("Look at this trading chart. Is the trade long or short? "
                  "What price is at the entry? What time? "
                  'Reply ONLY with JSON: {"direction":"long or short","price":"number","time":"HH:MM"}')
        resp = requests.post(OLLAMA_URL, json={
            "model": VISION_MODEL, "prompt": prompt,
            "images": [img_b64], "stream": False,
            "options": {"temperature": 0.0, "num_predict": 60}
        }, timeout=45)
        result = resp.json().get("response","")
        m = re.search(r'\{.*?\}', result, re.DOTALL)
        data = json.loads(m.group()) if m else {}
        conn = get_db()
        conn.execute("UPDATE trades SET ai_direction=?,ai_price=?,ai_time=?,ai_processed=1 WHERE id=?",
                    (data.get("direction"), data.get("price"), data.get("time"), trade_id))
        conn.commit(); conn.close()
    except Exception as e:
        conn = get_db()
        conn.execute("UPDATE trades SET ai_processed=1, ai_notes=? WHERE id=?",
                    (f"Vision error: {str(e)[:80]}", trade_id))
        conn.commit(); conn.close()

def _session_num(conn, account_id):
    today = date.today().isoformat()
    return conn.execute(
        "SELECT COUNT(*) FROM trades WHERE account_id=? AND date(timestamp)=?",
        (account_id, today)
    ).fetchone()[0] + 1

# ── Log trade ─────────────────────────────────────────
@app.post("/trade")
async def log_trade(
    background_tasks: BackgroundTasks,
    account_id:     int            = Form(1),
    state:          Optional[str]  = Form(None),
    setup_type:     Optional[str]  = Form(None),
    quality:        Optional[str]  = Form(None),
    entry_behavior: Optional[str]  = Form(None),
    mgmt_behavior:  Optional[str]  = Form(None),
    result:         Optional[str]  = Form(None),
    r_multiple:     Optional[float]= Form(None),
    execution_score:Optional[int]  = Form(None),
    direction:      Optional[str]  = Form(None),
    session:        Optional[str]  = Form(None),
    htf_aligned:    Optional[str]  = Form(None),
    liq_flow:       Optional[str]  = Form(None),
    position_size:  Optional[float]= Form(None),
    instrument:     Optional[str]  = Form("MNQ"),
    physical_state: Optional[str]  = Form(None),
    sleep_quality:  Optional[str]  = Form(None),
    chart_symbol:   Optional[str]  = Form(None),
    chart_tf:       Optional[str]  = Form(None),
    chart_price:    Optional[float]= Form(None),
    chart_levels:   Optional[str]  = Form(None),
    notes:          Optional[str]  = Form(None),
    run_vision:     int            = Form(0),
    image:          Optional[UploadFile] = File(None)
):
    timestamp  = datetime.now().isoformat()
    image_path = None

    if image and image.filename:
        ext   = Path(image.filename).suffix or ".png"
        fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{ext}"
        image_path = str(IMAGES_DIR / fname)
        with open(image_path, "wb") as f:
            shutil.copyfileobj(image.file, f)

    conn     = get_db()
    sess_num = _session_num(conn, account_id)

    cur = conn.execute("""
        INSERT INTO trades (
            account_id, timestamp, session_num, image_path,
            state, setup_type, quality, entry_behavior, mgmt_behavior,
            result, r_multiple, execution_score,
            direction, session, htf_aligned, liq_flow,
            position_size, instrument, physical_state, sleep_quality,
            chart_symbol, chart_tf, chart_price, chart_levels, notes,
            ai_processed
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        account_id, timestamp, sess_num, image_path,
        state, setup_type, quality, entry_behavior, mgmt_behavior,
        result, r_multiple, execution_score,
        direction, session, htf_aligned, liq_flow,
        position_size, instrument, physical_state, sleep_quality,
        chart_symbol, chart_tf, chart_price, chart_levels, notes,
        0 if (run_vision and image_path) else 1
    ))
    trade_id = cur.lastrowid

    today = date.today().isoformat()
    conn.execute("INSERT OR IGNORE INTO streaks (account_id,type,date) VALUES (?,?,?)",
                (account_id, "logged", today))
    conn.commit(); conn.close()

    if run_vision and image_path:
        background_tasks.add_task(_vision_async, trade_id, image_path)

    return {"success": True, "trade_id": trade_id, "session_num": sess_num,
            "vision_running": bool(run_vision and image_path)}

@app.get("/trade/{tid}/vision")
def vision_status(tid: int):
    conn = get_db()
    row  = conn.execute(
        "SELECT ai_processed, ai_direction, ai_price, ai_time, ai_notes FROM trades WHERE id=?",
        (tid,)
    ).fetchone()
    conn.close()
    if not row: return {"found": False}
    return {**dict(row), "found": True}

# ── Update trade R (from history) ─────────────────────
@app.put("/trade/{tid}/result")
async def update_result(
    tid: int,
    result:     Optional[str]   = Form(None),
    r_multiple: Optional[float] = Form(None)
):
    conn = get_db()
    conn.execute("UPDATE trades SET result=?, r_multiple=? WHERE id=?",
                (result, r_multiple, tid))
    conn.commit(); conn.close()
    return {"success": True}

# ── Today stats ───────────────────────────────────────
@app.get("/stats/today")
def today_stats(account_id: int = 1):
    today = date.today().isoformat()
    conn  = get_db()
    rows  = conn.execute(
        "SELECT * FROM trades WHERE account_id=? AND date(timestamp)=? ORDER BY timestamp",
        (account_id, today)
    ).fetchall()
    trades   = [dict(r) for r in rows]
    session  = conn.execute(
        "SELECT * FROM sessions WHERE account_id=? AND date=?",
        (account_id, today)
    ).fetchone()
    log_streak = _streak(conn, account_id, "logged")
    conn.close()

    r_trades = [t for t in trades if t["r_multiple"] is not None]
    total    = len(trades)
    total_r  = round(sum(t["r_multiple"] for t in r_trades), 2)
    wins     = sum(1 for t in r_trades if t["r_multiple"] > 0)
    losses   = sum(1 for t in r_trades if t["r_multiple"] < 0)
    be       = sum(1 for t in r_trades if t["r_multiple"] == 0)
    win_rate = round(wins/len(r_trades)*100) if r_trades else 0

    q = {"A":0,"B":0,"C":0}
    for t in trades:
        if t["quality"] in q: q[t["quality"]] += 1

    last_result = None
    consecutive_losses = 0
    for t in sorted(trades, key=lambda x: x["timestamp"]):
        if t["r_multiple"] is not None:
            if t["r_multiple"] < 0:
                consecutive_losses += 1
                last_result = "loss"
            else:
                consecutive_losses = 0
                last_result = "win" if t["r_multiple"] > 0 else "be"
        elif t["result"]:
            if t["result"] == "loss":
                consecutive_losses += 1
                last_result = "loss"
            else:
                consecutive_losses = 0
                last_result = t["result"]

    am = sum(1 for t in trades if t.get("session") in ["ny_am","ny_pre"])
    execs = [t["execution_score"] for t in trades if t.get("execution_score")]
    avg_exec = round(sum(execs)/len(execs),1) if execs else 0

    return {
        "date": today, "total": total,
        "wins": wins, "losses": losses, "be": be,
        "total_r": total_r, "win_rate": win_rate, "avg_exec": avg_exec,
        "quality": q, "trades": trades,
        "last_result": last_result,
        "consecutive_losses": consecutive_losses,
        "log_streak": log_streak, "am_trades": am,
        "session": dict(session) if session else None,
        "guardrails": {
            "over_2":    total >= 2,
            "over_3":    total >= 3,
            "over_am":   am >= 2,
            "tilt":      any(t["state"]=="tilt" for t in trades),
            "post_loss": last_result == "loss",
            "post_2loss":consecutive_losses >= 2,
            "walk_away": total_r > 0 and losses == 0 and total >= 1,
        }
    }

def _streak(conn, account_id, stype):
    rows = conn.execute(
        "SELECT date FROM streaks WHERE account_id=? AND type=? ORDER BY date DESC",
        (account_id, stype)
    ).fetchall()
    if not rows: return 0
    streak = 0; check = date.today()
    for row in rows:
        d = date.fromisoformat(row["date"])
        if d == check or (streak > 0 and d == check - timedelta(days=1)):
            streak += 1; check = d - timedelta(days=1)
        else: break
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

    r_trades = [t for t in trades if t["r_multiple"] is not None]
    total    = len(trades)
    total_r  = round(sum(t["r_multiple"] for t in r_trades), 2)
    wins     = sum(1 for t in r_trades if t["r_multiple"] > 0)
    win_rate = round(wins/len(r_trades)*100) if r_trades else 0

    q = {"A":0,"B":0,"C":0}
    for t in trades:
        if t["quality"] in q: q[t["quality"]] += 1

    q_scores = {"A":100,"B":65,"C":10}
    q_avg    = sum(q_scores.get(t.get("quality","C"),50) for t in trades) / total
    execs    = [t["execution_score"] for t in trades if t.get("execution_score")]
    e_avg    = (sum(execs)/len(execs)*10) if execs else 50
    g_entry  = sum(1 for t in trades if t.get("entry_behavior")=="decisive")
    b_score  = g_entry/total*100
    c_pen    = q["C"]*15
    craftsman= max(0, min(100, round(q_avg*.45 + e_avg*.35 + b_score*.2 - c_pen)))

    if craftsman>=75 and q["C"]==0 and total<=2: grade = "A"
    elif craftsman>=55 and q["C"]<=1:            grade = "B"
    else:                                        grade = "C"

    sc = {}
    for t in trades: sc[t.get("state","neutral")] = sc.get(t.get("state","neutral"),0)+1
    dominant = max(sc, key=sc.get) if sc else "neutral"

    leaks = {
        "Entries":    sum(1 for t in trades if t.get("entry_behavior") in ["hesitation","late","early","chase"]),
        "Management": sum(1 for t in trades if t.get("mgmt_behavior") in ["cut_early","overheld","moved_stop"]),
        "Psychology": sum(1 for t in trades if t.get("state")=="tilt") + q["C"],
        "Overtrading":1 if total>2 else 0,
    }
    biggest_leak = max(leaks, key=leaks.get)

    pattern = _data_pattern(trades, r_trades, q, total)
    signal  = _signal(trades, biggest_leak, grade, q, total)
    mirror  = _mirror(trades, r_trades, grade, biggest_leak, dominant, q)

    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO sessions
        (account_id,date,grade,craftsman_score,dominant_state,
         biggest_leak,pattern_note,one_signal,total_r,win_rate,trade_count,ai_mirror)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (account_id,today,grade,craftsman,dominant,biggest_leak,pattern,signal,total_r,win_rate,total,mirror))
    conn.execute("INSERT OR IGNORE INTO streaks (account_id,type,date) VALUES (?,?,?)",
                (account_id,"closed",today))
    conn.commit(); conn.close()

    return {"grade":grade,"craftsman_score":craftsman,"dominant_state":dominant,
            "biggest_leak":biggest_leak,"pattern_note":pattern,"one_signal":signal,
            "total_r":total_r,"win_rate":win_rate,"trade_count":total,"quality":q,"ai_mirror":mirror}

def _data_pattern(trades, r_trades, q, total):
    sorted_r = sorted([t for t in trades if t.get("r_multiple") is not None], key=lambda x: x["timestamp"])
    post_loss_r = []
    for i, t in enumerate(sorted_r):
        if i>0 and sorted_r[i-1]["r_multiple"]<0:
            post_loss_r.append(t["r_multiple"])
    lines = []
    if q["C"]>0:
        c_r = [t["r_multiple"] for t in r_trades if t.get("quality")=="C" and t["r_multiple"] is not None]
        if c_r: lines.append(f"{q['C']} C-setup{'s' if q['C']>1 else ''}: {sum(c_r):+.1f}R.")
    if post_loss_r:
        avg = sum(post_loss_r)/len(post_loss_r)
        lines.append(f"After loss: averaged {avg:+.2f}R on next trade ({len(post_loss_r)} samples).")
    if total>2: lines.append(f"{total} trades — above 2-trade structure.")
    if q["C"]==0 and total<=2: lines.append(f"Clean session — {total} trade{'s' if total>1 else ''}, no C-setups.")
    return " ".join(lines) if lines else "Session logged."

def _signal(trades, biggest_leak, grade, q, total):
    if grade=="A": return "That's the standard. Replicate the state, replicate the result."
    signals = {
        "Entries":    "If hesitation is present before entry, the setup has already passed.",
        "Management": "The plan was made when you were clear-headed. Trust that version.",
        "Psychology": "State before size. Tilt is the variable — not the market.",
        "Overtrading":f"Two trades is the structure. Trade {total} was outside it.",
    }
    return signals.get(biggest_leak, "One trade at a time.")

def _mirror(trades, r_trades, grade, biggest_leak, dominant, q):
    try:
        total_r = round(sum(t["r_multiple"] for t in r_trades), 2)
        a_r = [t["r_multiple"] for t in r_trades if t.get("quality")=="A"]
        c_r = [t["r_multiple"] for t in r_trades if t.get("quality")=="C"]
        a_total = round(sum(a_r),2) if a_r else None
        c_total = round(sum(c_r),2) if c_r else None
        ctx = []
        if a_total is not None: ctx.append(f"A-quality: {a_total:+.1f}R ({len(a_r)} trades)")
        if c_total is not None: ctx.append(f"C-quality: {c_total:+.1f}R ({len(c_r)} trades)")
        ctx.append(f"State:{dominant} Leak:{biggest_leak}")
        prompt = f"""Session data: {', '.join(ctx)}. Grade: {grade}.

Write ONE sentence stating what the data shows about this session's execution.
Then write ONE sentence framing this in terms of the craftsman's standard.

Rules: No "you should", no advice. State facts and identity only. Under 40 words total.
Response (2 sentences, no preamble):"""
        resp = requests.post(OLLAMA_URL, json={
            "model":TEXT_MODEL,"prompt":prompt,"stream":False,
            "options":{"temperature":0.25,"num_predict":70,"stop":["\n\n"]}
        }, timeout=25)
        raw = resp.json().get("response","").strip()
        bad = ["you should","you need","try to","make sure","remember to"]
        if any(p in raw.lower() for p in bad): raise ValueError("Advice language detected")
        sentences = [s.strip() for s in re.split(r'(?<=[.!])\s+', raw) if s.strip()]
        mirror = " ".join(sentences[:2])
        return mirror if mirror else _fallback_mirror(grade, biggest_leak, a_total, c_total)
    except:
        return _fallback_mirror(grade, biggest_leak,
            round(sum(t["r_multiple"] for t in r_trades if t.get("quality")=="A"),2) if r_trades else None,
            round(sum(t["r_multiple"] for t in r_trades if t.get("quality")=="C"),2) if r_trades else None)

def _fallback_mirror(grade, biggest_leak, a_total, c_total):
    parts = []
    if a_total is not None: parts.append(f"A-quality work generated {a_total:+.1f}R.")
    if c_total is not None and c_total<0: parts.append(f"C-setups cost {c_total:.1f}R.")
    if not parts: parts.append(f"Session grade: {grade}.")
    return " ".join(parts)

# ── Analytics ─────────────────────────────────────────
def _get(conn, account_id, days):
    if account_id==0:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM trades WHERE date(timestamp)>=date('now',?) ORDER BY timestamp",
            (f"-{days} days",)
        ).fetchall()]
    return [dict(r) for r in conn.execute(
        "SELECT * FROM trades WHERE account_id=? AND date(timestamp)>=date('now',?) ORDER BY timestamp",
        (account_id, f"-{days} days")
    ).fetchall()]

@app.get("/analytics")
def analytics(account_id: int=1, days: int=30):
    conn     = get_db()
    trades   = _get(conn, account_id, days)
    sessions = [dict(r) for r in conn.execute(
        "SELECT * FROM sessions WHERE account_id=? ORDER BY date DESC LIMIT 20",
        (account_id,)
    ).fetchall()]
    log_streak = _streak(conn, account_id, "logged")
    conn.close()
    if not trades: return {"empty":True,"log_streak":log_streak}

    r_trades = [t for t in trades if t["r_multiple"] is not None]
    total    = len(trades)
    wins     = sum(1 for t in r_trades if t["r_multiple"]>0)
    losses   = sum(1 for t in r_trades if t["r_multiple"]<0)
    total_r  = round(sum(t["r_multiple"] for t in r_trades),2)
    win_rate = round(wins/len(r_trades)*100,1) if r_trades else 0

    qperf = {"A":{"total":0,"wins":0,"total_r":0},"B":{"total":0,"wins":0,"total_r":0},"C":{"total":0,"wins":0,"total_r":0}}
    for t in r_trades:
        q = t.get("quality","C")
        if q in qperf:
            qperf[q]["total"] += 1
            qperf[q]["total_r"] = round(qperf[q]["total_r"]+t["r_multiple"],2)
            if t["r_multiple"]>0: qperf[q]["wins"] += 1
    for q in qperf:
        p=qperf[q]; p["wr"]=round(p["wins"]/p["total"]*100,1) if p["total"] else 0; p["avg_r"]=round(p["total_r"]/p["total"],2) if p["total"] else 0

    a=qperf["A"]; c=qperf["C"]
    if a.get("total",0)<3: edge={"text":"Log more A-quality trades to define your edge.","ready":False}
    else:
        txt = f"A-quality: {a['wr']}% win rate · {'+' if a['total_r']>=0 else ''}{a['total_r']}R total."
        if c.get("total",0)>0: txt += f"  C-setups: {c['total_r']}R cost."
        edge = {"text":txt,"ready":True}

    def perf_by(key):
        d={}
        for t in r_trades:
            v=t.get(key,"unknown")
            if v not in d: d[v]={"total":0,"wins":0,"total_r":0}
            d[v]["total"]+=1; d[v]["total_r"]=round(d[v]["total_r"]+t["r_multiple"],2)
            if t["r_multiple"]>0: d[v]["wins"]+=1
        for v in d:
            p=d[v]; p["wr"]=round(p["wins"]/p["total"]*100,1) if p["total"] else 0; p["avg_r"]=round(p["total_r"]/p["total"],2) if p["total"] else 0
        return d

    def leak(key, bad_vals):
        bad=[t for t in r_trades if t.get(key) in bad_vals]
        good=[t for t in r_trades if t.get(key) and t.get(key) not in bad_vals]
        return {"bad_avg_r":round(sum(t["r_multiple"] for t in bad)/len(bad),2) if bad else None,
                "good_avg_r":round(sum(t["r_multiple"] for t in good)/len(good),2) if good else None,
                "bad_n":len(bad),"good_n":len(good)}

    sorted_t = sorted(r_trades, key=lambda t:t["timestamp"])
    wal={"total":0,"wins":0}; cal={"total":0,"c":0}
    for i,t in enumerate(sorted_t):
        if i==0: continue
        prev=sorted_t[i-1]; same=t["timestamp"][:10]==prev["timestamp"][:10]
        if same and prev["r_multiple"]<0:
            wal["total"]+=1
            if t["r_multiple"]>0: wal["wins"]+=1
            cal["total"]+=1
            if t.get("quality")=="C": cal["c"]+=1

    daily={}
    for t in sorted_t:
        d=t["timestamp"][:10]; daily[d]=round(daily.get(d,0)+(t["r_multiple"] or 0),2)
    cum=0; cum_r=[]
    for d,r in sorted(daily.items()):
        cum=round(cum+r,2); cum_r.append({"date":d,"daily":r,"cumulative":cum})

    freq={}
    for t in trades:
        d=t["timestamp"][:10]; freq[d]=freq.get(d,0)+1
    trade_freq=[{"date":k,"count":v} for k,v in sorted(freq.items())]

    return {
        "total":total,"wins":wins,"losses":losses,"win_rate":win_rate,"total_r":total_r,
        "quality":qperf,"edge":edge,
        "state_perf":perf_by("state"),
        "entry_leak":leak("entry_behavior",["hesitation","late","early","chase"]),
        "mgmt_leak":leak("mgmt_behavior",["cut_early","overheld","moved_stop"]),
        "setup_perf":perf_by("setup_type"),
        "sequence":{"win_after_loss":round(wal["wins"]/wal["total"]*100,1) if wal["total"] else None,
                    "c_after_loss":round(cal["c"]/cal["total"]*100,1) if cal["total"] else None,
                    "samples":wal["total"]},
        "cum_r":cum_r,"trade_freq":trade_freq,
        "sessions":sessions,"log_streak":log_streak,
    }

@app.get("/analyze")
def analyze(account_id:int=1, days:int=7):
    conn=get_db(); trades=_get(conn,account_id,days); conn.close()
    if not trades: return {"analysis":"No trades in this period."}
    r_trades=[t for t in trades if t["r_multiple"] is not None]
    total_r=round(sum(t["r_multiple"] for t in r_trades),2)
    lines=[]
    for t in sorted(trades,key=lambda x:x["timestamp"]):
        lines.append(f"[{t['timestamp'][:10]}] #{t['session_num']} State:{t.get('state','?')} "
                    f"Q:{t.get('quality','?')} Setup:{t.get('setup_type','?')} "
                    f"Entry:{t.get('entry_behavior','?')} Mgmt:{t.get('mgmt_behavior','?')} "
                    f"R:{t.get('r_multiple','?')}")
    prompt = f"""Analyze {days} days of trading data. Total R: {total_r:+.1f}.

{chr(10).join(lines)}

Respond EXACTLY:

YOUR EDGE: [1 sentence — what quality+setup combination makes money]
YOUR LEAK: [1 sentence — behavior costing most R, with evidence]
TOMORROW: [1 rule, under 12 words, craftsman framing not restriction]

No praise. No "you should." Pattern recognition only."""
    try:
        resp=requests.post(OLLAMA_URL,json={"model":TEXT_MODEL,"prompt":prompt,"stream":False,"options":{"temperature":0.15}},timeout=45)
        return {"analysis":resp.json().get("response","No response")}
    except Exception as e:
        return {"analysis":f"Ollama unavailable: {e}"}

# ── Trade history ─────────────────────────────────────
@app.get("/trades")
def get_trades(account_id:int=1, limit:int=100, date_filter:Optional[str]=None):
    conn=get_db()
    acc_clause="" if account_id==0 else f"t.account_id={account_id} AND"
    date_clause=f"date(timestamp)='{date_filter}' AND" if date_filter else ""
    where=f"WHERE {acc_clause} {date_clause} 1=1"
    sql=f"SELECT t.*, a.name acc_name, a.color acc_color FROM trades t LEFT JOIN accounts a ON t.account_id=a.id {where} ORDER BY timestamp DESC LIMIT {limit}"
    rows=conn.execute(sql).fetchall(); conn.close()
    return [dict(r) for r in rows]

@app.delete("/trade/{tid}")
def delete_trade(tid:int):
    conn=get_db(); conn.execute("DELETE FROM trades WHERE id=?", (tid,)); conn.commit(); conn.close()
    return {"success":True}

@app.on_event("startup")
def startup():
    bat=Path("start_journal.bat")
    if not bat.exists():
        cwd=Path.cwd().resolve()
        bat.write_text(f"@echo off\ncd /d \"{cwd}\"\npython main.py\npause\n")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
