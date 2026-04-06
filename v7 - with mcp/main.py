"""
Trading Journal v7
- Async vision (non-blocking log)
- Behavioral guardrails (tilt gate, post-loss lock, 2-trade limit)
- Structured mirror (data-first, AI fills gaps)
- Fixed optional context
- Favicon
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import sqlite3, shutil, requests, json, base64, re, threading
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
import subprocess
import os

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_PATH      = "journal.db"
IMAGES_DIR   = Path("images")
IMAGES_DIR.mkdir(exist_ok=True)
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_TAGS  = "http://localhost:11434/api/tags"
TEXT_MODEL   = "qwen2.5"
VISION_MODEL = "moondream"

BRIDGE_SCRIPT = os.path.join(os.path.dirname(__file__), "chart_bridge.js")
BRIDGE_TIMEOUT = 20

# ── Favicon (suppress 404) ────────────────────────────
FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" rx="6" fill="#0c0c0f"/>
<text x="16" y="22" font-size="18" text-anchor="middle" fill="#818cf8">◈</text>
</svg>"""

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(content=FAVICON_SVG.encode(), media_type="image/svg+xml")

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

            -- Core (main card — always visible)
            state           TEXT,   -- locked_in, neutral, off, tilt
            setup_type      TEXT,
            quality         TEXT,   -- A, B, C
            entry_behavior  TEXT,
            mgmt_behavior   TEXT,
            r_multiple      REAL,
            execution_score INTEGER,-- 1-10

            -- Optional context (collapsible)
            direction       TEXT,
            session         TEXT,
            htf_aligned     TEXT,
            liq_flow        TEXT,
            position_size   REAL,
            instrument      TEXT DEFAULT 'MNQ',
            physical_state  TEXT,
            sleep_quality   TEXT,

            -- AI vision (async — may be null initially)
            ai_direction    TEXT,
            ai_price        TEXT,
            ai_time         TEXT,
            ai_notes        TEXT,
            ai_processed    INTEGER DEFAULT 0,

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
    try:
        resp   = requests.get(OLLAMA_TAGS, timeout=4)
        models = [m["name"].split(":")[0] for m in resp.json().get("models", [])]
        return {
            "ollama":       True,
            "text":         TEXT_MODEL in models,
            "vision":       VISION_MODEL in models,
            "vision_cmd":   f"ollama pull {VISION_MODEL}",
            "text_model":   TEXT_MODEL,
            "vision_model": VISION_MODEL,
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
    conn.commit()
    conn.close()
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
            {"id": "liq_sweep",  "label": "Liq. Sweep",    "icon": "🌊"},
            {"id": "trend_cont", "label": "Trend Cont.",    "icon": "📈"},
            {"id": "reversal",   "label": "Reversal",       "icon": "🔄"},
            {"id": "breakout",   "label": "Breakout",       "icon": "💥"},
            {"id": "range",      "label": "Range",          "icon": "↔️"},
        ],
        "quality": [
            {"id": "A", "desc": "Clean. High confidence."},
            {"id": "B", "desc": "Valid but not ideal."},
            {"id": "C", "desc": "Forced. You know it."},
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
        "r_presets": ["+3R", "+2R", "+1.5R", "+1R", "+0.5R", "BE", "-0.5R", "-1R", "-2R"],
        "session":    [
            {"id": "ny_am",   "label": "NY AM"},
            {"id": "ny_pre",  "label": "NY Pre"},
            {"id": "london",  "label": "London"},
            {"id": "ny_pm",   "label": "NY PM"},
            {"id": "asia",    "label": "Asia"},
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
        "instruments": ["MNQ", "NQ", "ES", "MES", "YM", "MYM", "CL", "GC"],
    }
    
def _run_bridge(command: str, extra_arg: str = None, timeout: int = BRIDGE_TIMEOUT) -> dict:
    """Run chart_bridge.js and return parsed JSON output"""
    if not os.path.exists(BRIDGE_SCRIPT):
        return {"error": "chart_bridge.js not found. Place it in the same folder as main.py."}

    args = ["node", BRIDGE_SCRIPT, command]
    if extra_arg:
        args.append(extra_arg)

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.path.dirname(__file__)
        )
        if result.returncode != 0 and not result.stdout:
            return {"error": result.stderr or "Bridge script returned non-zero exit"}

        import json
        output = result.stdout.strip()
        if not output:
            return {"error": "No output from bridge script"}

        # Find JSON in output (may have debug lines before it)
        lines = output.split("\n")
        for line in reversed(lines):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)

        return json.loads(output)

    except subprocess.TimeoutExpired:
        return {"error": f"TradingView read timed out after {timeout}s. Is TradingView running with debug port?"}
    except json.JSONDecodeError as e:
        return {"error": f"Could not parse bridge output: {e}", "raw": result.stdout[:200]}
    except FileNotFoundError:
        return {"error": "Node.js not found. Install from nodejs.org"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/chart/status")
def chart_status():
    """Check if TradingView is connected via CDP"""
    return _run_bridge("status", timeout=5)


@app.get("/chart/read")
def chart_read():
    """
    Read current TradingView chart state.
    Returns: symbol, price, session, active indicator data,
    level prices (Asia/London H/L), OB zones, FVGs,
    and suggested values for the trade log.
    """
    return _run_bridge("read", timeout=18)


@app.post("/chart/screenshot")
async def chart_screenshot():
    """
    Take a screenshot of the current TradingView chart.
    Saves to images/ folder and returns the path for use in the trade log.
    """
    import time
    fname = f"tv_{int(time.time() * 1000)}.png"
    save_path = str(IMAGES_DIR / fname)

    result = _run_bridge("screenshot", save_path, timeout=15)

    if result.get("success") and os.path.exists(save_path):
        return {"success": True, "path": save_path, "filename": fname}
    elif result.get("path") and os.path.exists(str(result.get("path", ""))):
        # Bridge saved to a different path — move it
        import shutil
        shutil.copy(result["path"], save_path)
        return {"success": True, "path": save_path, "filename": fname}
    else:
        return {"success": False, "error": result.get("error", "Screenshot failed"), "detail": result}
        
# ── Async vision processing ───────────────────────────
def _run_vision_async(trade_id: int, image_path: str):
    """Runs in background thread — updates trade after logging"""
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        # Minimal, specific prompt — just what we can extract reliably
        prompt = ("Look at this trading chart. Is the trade going long (up) or short (down)? "
                  "What price is shown at the entry point? What time is shown? "
                  "Reply ONLY with JSON: {\"direction\":\"long or short\",\"price\":\"number\",\"time\":\"HH:MM\"}")

        resp = requests.post(OLLAMA_URL, json={
            "model": VISION_MODEL,
            "prompt": prompt,
            "images": [img_b64],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 60}
        }, timeout=45)

        result = resp.json().get("response", "")
        m      = re.search(r'\{.*?\}', result, re.DOTALL)
        data   = json.loads(m.group()) if m else {}

        conn = get_db()
        conn.execute("""
            UPDATE trades SET
                ai_direction=?, ai_price=?, ai_time=?, ai_processed=1
            WHERE id=?
        """, (
            data.get("direction"), data.get("price"),
            data.get("time"), trade_id
        ))
        conn.commit()
        conn.close()

    except Exception as e:
        # Fail silently — trade is already logged
        conn = get_db()
        conn.execute("UPDATE trades SET ai_processed=1, ai_notes=? WHERE id=?",
                    (f"Vision error: {str(e)[:100]}", trade_id))
        conn.commit()
        conn.close()

# ── Session number ────────────────────────────────────
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
            r_multiple, execution_score,
            direction, session, htf_aligned, liq_flow,
            position_size, instrument, physical_state, sleep_quality, notes,
            ai_processed
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        account_id, timestamp, sess_num, image_path,
        state, setup_type, quality, entry_behavior, mgmt_behavior,
        r_multiple, execution_score,
        direction, session, htf_aligned, liq_flow,
        position_size, instrument, physical_state, sleep_quality, notes,
        0 if (run_vision and image_path) else 1
    ))
    trade_id = cur.lastrowid

    # Streaks
    today = date.today().isoformat()
    conn.execute("INSERT OR IGNORE INTO streaks (account_id,type,date) VALUES (?,?,?)",
                (account_id, "logged", today))
    conn.commit()
    conn.close()

    # Vision runs in background — trade is already logged
    if run_vision and image_path:
        background_tasks.add_task(_run_vision_async, trade_id, image_path)

    return {
        "success":    True,
        "trade_id":   trade_id,
        "session_num": sess_num,
        "vision_running": bool(run_vision and image_path)
    }

# ── Vision status poll ────────────────────────────────
@app.get("/trade/{tid}/vision")
def get_vision_status(tid: int):
    conn = get_db()
    row  = conn.execute(
        "SELECT ai_processed, ai_direction, ai_price, ai_time, ai_notes FROM trades WHERE id=?",
        (tid,)
    ).fetchone()
    conn.close()
    if not row: return {"found": False}
    d = dict(row)
    d["found"] = True
    return d

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
    session = conn.execute(
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
    win_rate = round(wins / len(r_trades) * 100) if r_trades else 0

    q = {"A": 0, "B": 0, "C": 0}
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

    am = sum(1 for t in trades if t.get("session") in ["ny_am","ny_pre"])

    return {
        "date": today, "total": total,
        "wins": wins, "losses": losses, "be": be,
        "total_r": total_r, "win_rate": win_rate,
        "quality": q, "trades": trades,
        "last_result": last_result,
        "consecutive_losses": consecutive_losses,
        "log_streak": log_streak,
        "am_trades": am,
        "session": dict(session) if session else None,
        "guardrails": {
            "over_2":      total >= 2,
            "over_3":      total >= 3,
            "over_am":     am >= 2,
            "tilt_active": any(t["state"] == "tilt" for t in trades),
            "post_loss":   last_result == "loss",
            "post_2loss":  consecutive_losses >= 2,
            "walk_away":   total_r > 0 and losses == 0 and total >= 1,
        }
    }

def _streak(conn, account_id, stype):
    rows = conn.execute(
        "SELECT date FROM streaks WHERE account_id=? AND type=? ORDER BY date DESC",
        (account_id, stype)
    ).fetchall()
    if not rows: return 0
    streak = 0
    check  = date.today()
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
    win_rate = round(wins / len(r_trades) * 100) if r_trades else 0

    q_counts = {"A": 0, "B": 0, "C": 0}
    for t in trades:
        if t["quality"] in q_counts: q_counts[t["quality"]] += 1

    # Craftsman score — weighted quality + execution + behavior
    q_scores = {"A": 100, "B": 65, "C": 10}
    q_avg    = sum(q_scores.get(t.get("quality","C"), 50) for t in trades) / total
    exec_raw = [t["execution_score"] for t in trades if t.get("execution_score")]
    e_avg    = (sum(exec_raw) / len(exec_raw) * 10) if exec_raw else 50
    good_entry = sum(1 for t in trades if t.get("entry_behavior") == "decisive")
    b_score    = (good_entry / total * 100)
    c_penalty  = q_counts["C"] * 15
    craftsman  = max(0, min(100, round(q_avg * 0.45 + e_avg * 0.35 + b_score * 0.2 - c_penalty)))

    # Grade
    if craftsman >= 75 and q_counts["C"] == 0 and total <= 2:
        grade = "A"
    elif craftsman >= 55 and q_counts["C"] <= 1:
        grade = "B"
    else:
        grade = "C"

    # Dominant state
    sc = {}
    for t in trades: sc[t.get("state","neutral")] = sc.get(t.get("state","neutral"), 0) + 1
    dominant_state = max(sc, key=sc.get) if sc else "neutral"

    # Biggest leak
    leaks = {
        "Entries":    sum(1 for t in trades if t.get("entry_behavior") in ["hesitation","late","early","chase"]),
        "Management": sum(1 for t in trades if t.get("mgmt_behavior") in ["cut_early","overheld","moved_stop"]),
        "Psychology": sum(1 for t in trades if t.get("state") in ["tilt"]) + q_counts["C"],
        "Overtrading":1 if total > 2 else 0,
    }
    biggest_leak = max(leaks, key=leaks.get)

    # Pattern note — data-first, no AI needed
    pattern_note = _data_pattern(trades, r_trades, q_counts, total)

    # Signal for tomorrow
    one_signal = _signal(trades, biggest_leak, grade, q_counts, total)

    # AI mirror — short, specific, identity-framed
    ai_mirror = _mirror(trades, r_trades, grade, biggest_leak, dominant_state, q_counts)

    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO sessions
        (account_id, date, grade, craftsman_score, dominant_state,
         biggest_leak, pattern_note, one_signal, total_r, win_rate, trade_count, ai_mirror)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        account_id, today, grade, craftsman, dominant_state,
        biggest_leak, pattern_note, one_signal, total_r, win_rate, total, ai_mirror
    ))
    conn.execute("INSERT OR IGNORE INTO streaks (account_id,type,date) VALUES (?,?,?)",
                (account_id, "closed", today))
    conn.commit()
    conn.close()

    return {
        "grade": grade, "craftsman_score": craftsman,
        "dominant_state": dominant_state, "biggest_leak": biggest_leak,
        "pattern_note": pattern_note, "one_signal": one_signal,
        "total_r": total_r, "win_rate": win_rate, "trade_count": total,
        "quality": q_counts, "ai_mirror": ai_mirror
    }

def _data_pattern(trades, r_trades, q, total):
    """Pure data pattern — no AI, always useful"""
    sorted_r = sorted([t for t in trades if t.get("r_multiple") is not None],
                      key=lambda x: x["timestamp"])

    # Most important pattern: what happened after loss
    post_loss_r = []
    for i, t in enumerate(sorted_r):
        if i > 0 and sorted_r[i-1]["r_multiple"] < 0 and t.get("r_multiple") is not None:
            post_loss_r.append(t["r_multiple"])

    lines = []

    # Quality distribution
    if q["C"] > 0:
        c_r = [t["r_multiple"] for t in r_trades if t.get("quality") == "C" and t["r_multiple"] is not None]
        lines.append(f"{q['C']} C-setup{'s' if q['C']>1 else ''}: {sum(c_r):+.1f}R total.")

    # Post-loss behavior
    if post_loss_r:
        avg = sum(post_loss_r)/len(post_loss_r)
        lines.append(f"After a loss: averaged {avg:+.2f}R on next trade ({len(post_loss_r)} sample{'s' if len(post_loss_r)>1 else ''}).")

    # Overtrading
    if total > 2:
        lines.append(f"{total} trades today — above the 2-trade structure.")

    # Clean session
    if q["C"] == 0 and total <= 2 and post_loss_r == []:
        lines.append(f"Clean session — {total} trade{'s' if total>1 else ''}, no C-setups.")

    return " ".join(lines) if lines else "Session logged."

def _signal(trades, biggest_leak, grade, q, total):
    if grade == "A":
        return "That's the standard. Replicate the state, replicate the result."
    signals = {
        "Entries":    "If hesitation is present before entry, the setup has already passed.",
        "Management": "The plan was made when you were clear-headed. Trust that version.",
        "Psychology": "State before size. Tilt is the variable — not the market.",
        "Overtrading": f"Two trades is the structure. Trade {total} was outside it.",
    }
    return signals.get(biggest_leak, "One trade at a time.")

def _mirror(trades, r_trades, grade, biggest_leak, dominant_state, q):
    """AI mirror — structured prompt, short output, validated"""
    try:
        total_r    = round(sum(t["r_multiple"] for t in r_trades), 2)
        a_r        = [t["r_multiple"] for t in r_trades if t.get("quality") == "A"]
        c_r        = [t["r_multiple"] for t in r_trades if t.get("quality") == "C"]
        a_total    = round(sum(a_r), 2) if a_r else None
        c_total    = round(sum(c_r), 2) if c_r else None

        context = []
        if a_total is not None: context.append(f"A-quality: {a_total:+.1f}R ({len(a_r)} trades)")
        if c_total is not None: context.append(f"C-quality: {c_total:+.1f}R ({len(c_r)} trades)")
        context.append(f"State: {dominant_state}")
        context.append(f"Leak: {biggest_leak}")

        prompt = f"""Session data: {', '.join(context)}. Grade: {grade}.

Write ONE sentence that states what the data shows about this session's execution.
Then write ONE sentence that frames this in terms of the craftsman's standard (who they are building toward).

Rules:
- No "you should", "you need to", "try to"
- State facts and identity, not advice
- Under 40 words total
- Speak as if you know this person deeply

Example of correct tone: "A-quality entries drove all positive R today. The craftsman's standard is already visible — the C-setup is the only variable left to remove."

Response (2 sentences only, no preamble):"""

        resp = requests.post(OLLAMA_URL, json={
            "model":  TEXT_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.25, "num_predict": 70, "stop": ["\n\n"]}
        }, timeout=25)

        raw = resp.json().get("response", "").strip()

        # Validate — reject if it contains advice language
        bad_phrases = ["you should", "you need", "try to", "make sure", "remember to", "consider"]
        if any(p in raw.lower() for p in bad_phrases):
            raise ValueError("Output contains advice language — using fallback")

        # Clean to 2 sentences
        sentences = [s.strip() for s in re.split(r'(?<=[.!])\s+', raw) if s.strip()]
        mirror = " ".join(sentences[:2])
        return mirror if mirror else _fallback_mirror(grade, biggest_leak, a_total, c_total)

    except Exception:
        return _fallback_mirror(grade, biggest_leak,
            round(sum(t["r_multiple"] for t in r_trades if t.get("quality")=="A"), 2) if r_trades else None,
            round(sum(t["r_multiple"] for t in r_trades if t.get("quality")=="C"), 2) if r_trades else None)

def _fallback_mirror(grade, biggest_leak, a_total, c_total):
    """Data-driven fallback — never fails, always relevant"""
    parts = []
    if a_total is not None:
        parts.append(f"A-quality work generated {a_total:+.1f}R.")
    if c_total is not None and c_total < 0:
        parts.append(f"C-setups cost {c_total:.1f}R — the gap between grade {grade} and grade A.")
    if not parts:
        parts.append(f"Session grade: {grade}. The pattern builds with every logged trade.")
    return " ".join(parts)

# ── Analytics ─────────────────────────────────────────
def _get(conn, account_id, days):
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
    conn     = get_db()
    trades   = _get(conn, account_id, days)
    sessions = [dict(r) for r in conn.execute(
        "SELECT * FROM sessions WHERE account_id=? ORDER BY date DESC LIMIT 20",
        (account_id,)
    ).fetchall()]
    log_streak = _streak(conn, account_id, "logged")
    conn.close()

    if not trades: return {"empty": True, "log_streak": log_streak}

    r_trades = [t for t in trades if t["r_multiple"] is not None]
    total    = len(trades)
    wins     = sum(1 for t in r_trades if t["r_multiple"] > 0)
    losses   = sum(1 for t in r_trades if t["r_multiple"] < 0)
    total_r  = round(sum(t["r_multiple"] for t in r_trades), 2)
    win_rate = round(wins / len(r_trades) * 100, 1) if r_trades else 0

    # Quality performance (the core mirror)
    qperf = {q: {"total":0,"wins":0,"total_r":0} for q in ["A","B","C"]}
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

    # Edge statement
    edge = _edge(qperf)

    # State performance
    sperf = {}
    for t in r_trades:
        s = t.get("state","neutral")
        if s not in sperf: sperf[s] = {"total":0,"wins":0,"total_r":0}
        sperf[s]["total"] += 1
        sperf[s]["total_r"] = round(sperf[s]["total_r"]+t["r_multiple"],2)
        if t["r_multiple"]>0: sperf[s]["wins"] += 1
    for s in sperf:
        p = sperf[s]
        p["wr"] = round(p["wins"]/p["total"]*100,1) if p["total"] else 0
        p["avg_r"] = round(p["total_r"]/p["total"],2) if p["total"] else 0

    # Entry leak
    def leak(key, bad_vals):
        bad  = [t for t in r_trades if t.get(key) in bad_vals]
        good = [t for t in r_trades if t.get(key) and t.get(key) not in bad_vals]
        return {
            "bad_avg_r":  round(sum(t["r_multiple"] for t in bad)/len(bad),2) if bad else None,
            "good_avg_r": round(sum(t["r_multiple"] for t in good)/len(good),2) if good else None,
            "bad_n": len(bad), "good_n": len(good)
        }

    entry_leak = leak("entry_behavior", ["hesitation","late","early","chase"])
    mgmt_leak  = leak("mgmt_behavior",  ["cut_early","overheld","moved_stop"])

    # Post-loss sequence
    sorted_t = sorted(r_trades, key=lambda t: t["timestamp"])
    wal = {"total":0,"wins":0}
    cal = {"total":0,"c":0}
    for i, t in enumerate(sorted_t):
        if i==0: continue
        prev = sorted_t[i-1]
        same = t["timestamp"][:10]==prev["timestamp"][:10]
        if same and prev["r_multiple"] < 0:
            wal["total"] += 1
            if t["r_multiple"] > 0: wal["wins"] += 1
            cal["total"] += 1
            if t.get("quality") == "C": cal["c"] += 1

    # Setup performance
    setup_p = {}
    for t in r_trades:
        s = t.get("setup_type","unknown")
        if s not in setup_p: setup_p[s] = {"total":0,"wins":0,"total_r":0}
        setup_p[s]["total"] += 1
        setup_p[s]["total_r"] = round(setup_p[s]["total_r"]+t["r_multiple"],2)
        if t["r_multiple"]>0: setup_p[s]["wins"] += 1
    for s in setup_p:
        p = setup_p[s]
        p["wr"] = round(p["wins"]/p["total"]*100,1) if p["total"] else 0

    # Physical state
    physp = {}
    for t in r_trades:
        ph = t.get("physical_state","?")
        if ph not in physp: physp[ph] = {"total":0,"total_r":0}
        physp[ph]["total"] += 1
        physp[ph]["total_r"] = round(physp[ph]["total_r"]+t["r_multiple"],2)
    for p in physp:
        pp = physp[p]
        pp["avg_r"] = round(pp["total_r"]/pp["total"],2) if pp["total"] else 0

    # Cumulative R
    daily = {}
    for t in sorted(trades, key=lambda x: x["timestamp"]):
        d = t["timestamp"][:10]
        daily[d] = round(daily.get(d,0)+(t["r_multiple"] or 0),2)
    cum = 0
    cum_r = []
    for d, r in sorted(daily.items()):
        cum = round(cum+r,2)
        cum_r.append({"date":d,"daily":r,"cumulative":cum})

    freq = {}
    for t in trades:
        d=t["timestamp"][:10]; freq[d]=freq.get(d,0)+1
    trade_freq = [{"date":k,"count":v} for k,v in sorted(freq.items())]

    return {
        "total": total, "wins": wins, "losses": losses,
        "win_rate": win_rate, "total_r": total_r,
        "quality": qperf, "edge": edge,
        "state_perf": sperf,
        "entry_leak": entry_leak, "mgmt_leak": mgmt_leak,
        "setup_perf": setup_p, "physical_perf": physp,
        "sequence": {
            "win_after_loss": round(wal["wins"]/wal["total"]*100,1) if wal["total"] else None,
            "c_after_loss":   round(cal["c"]/cal["total"]*100,1) if cal["total"] else None,
            "samples": wal["total"],
        },
        "cum_r": cum_r, "trade_freq": trade_freq,
        "sessions": sessions, "log_streak": log_streak,
    }

def _edge(qperf):
    a = qperf.get("A",{}); c = qperf.get("C",{})
    if a.get("total",0) < 3:
        return {"text": "Log more A-quality trades to define your edge.", "ready": False}
    text = f"A-quality: {a['wr']}% win rate · {'+' if a['total_r']>=0 else ''}{a['total_r']}R total."
    if c.get("total",0) > 0:
        text += f"  C-setups: {c['total_r']}R cost."
    return {"text": text, "ready": True}

# ── AI Analysis ───────────────────────────────────────
@app.get("/analyze")
def analyze(account_id: int = 1, days: int = 7):
    conn   = get_db()
    trades = _get(conn, account_id, days)
    conn.close()
    if not trades: return {"analysis": "No trades in this period."}

    r_trades = [t for t in trades if t["r_multiple"] is not None]
    total_r  = round(sum(t["r_multiple"] for t in r_trades), 2)

    lines = []
    for t in sorted(trades, key=lambda x: x["timestamp"]):
        lines.append(
            f"[{t['timestamp'][:10]}] #{t['session_num']} "
            f"State:{t.get('state','?')} Q:{t.get('quality','?')} "
            f"Setup:{t.get('setup_type','?')} Entry:{t.get('entry_behavior','?')} "
            f"Mgmt:{t.get('mgmt_behavior','?')} R:{t.get('r_multiple','?')}"
        )

    prompt = f"""You are reading {days} days of trading data for a craftsman-trader. Total R: {total_r:+.1f}.

{chr(10).join(lines)}

Respond in exactly this format — no deviation:

YOUR EDGE: [1 sentence — what quality+setup combination makes money, stated as fact]
YOUR LEAK: [1 sentence — the single behavior costing the most R, with evidence from the data]
TOMORROW: [1 rule, under 12 words, framed as a craftsman's standard not a restriction]

No praise. No "you should." Pattern recognition only."""

    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": TEXT_MODEL, "prompt": prompt,
            "stream": False, "options": {"temperature": 0.15}
        }, timeout=45)
        return {"analysis": resp.json().get("response","No response")}
    except Exception as e:
        return {"analysis": f"Ollama unavailable: {e}"}

# ── Trade history ─────────────────────────────────────
@app.get("/trades")
def get_trades(account_id: int = 1, limit: int = 100, date_filter: Optional[str] = None):
    conn = get_db()
    acc_clause = "" if account_id == 0 else f"t.account_id={account_id} AND"
    date_clause = f"date(timestamp)='{date_filter}' AND" if date_filter else ""
    where = f"WHERE {acc_clause} {date_clause} 1=1"
    sql = f"""SELECT t.*, a.name acc_name, a.color acc_color
              FROM trades t LEFT JOIN accounts a ON t.account_id=a.id
              {where} ORDER BY timestamp DESC LIMIT {limit}"""
    rows = conn.execute(sql).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.delete("/trade/{tid}")
def delete_trade(tid: int):
    conn = get_db()
    conn.execute("DELETE FROM trades WHERE id=?", (tid,))
    conn.commit(); conn.close()
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
