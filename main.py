"""
Trading Journal — Whop Hosted (combined final)

Fixes applied vs previous versions:
  1. EmbedHeadersMiddleware is registered FIRST so it is the outermost
     layer and processes response headers LAST — after CORS — meaning it
     can remove X-Frame-Options and set CSP without CORS overwriting it.
  2. X-Frame-Options is DELETED (not set to ALLOWALL which browsers reject).
  3. CSP uses frame-ancestors * so any Whop domain/subdomain can embed.
  4. Cross-Origin-Opener-Policy / Embedder-Policy set to unsafe-none for
     iframe localStorage access.
  5. CORS allows all origins (allow_credentials=False required with *).
  6. Neon SSL: _build_dsn handles both ? and & URL separators correctly.
  7. Connection pool uses keepalives so Neon idle connections don't drop.
  8. db() context manager tests the connection before use and recovers
     broken ones automatically (Neon closes idle after ~5 min).
  9. rows() and row_one() guard against None cursor.description.
 10. _serialize() handles all datetime/date objects before JSON response.
 11. /health endpoint so Railway knows the app is alive.
 12. Startup logs clearly to Railway deploy log.

Environment variables (set in Railway dashboard — never in code):
  DATABASE_URL   — Neon connection string (paste from Neon dashboard)
  WHOP_API_KEY   — Whop dashboard > Developer Settings > API Keys
  JWT_SECRET     — python -c "import secrets; print(secrets.token_hex(32))"
  GROQ_API_KEY   — console.groq.com (free account)

Optional:
  DEV_MODE       — "true" to bypass Whop auth for local testing
  DEV_USER_ID    — user ID used when DEV_MODE is true
"""

import os, json, shutil, subprocess
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool
from fastapi import FastAPI, Form, File, UploadFile, HTTPException, Request, Depends
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
import httpx
import jwt as pyjwt
from groq import Groq

# ── Config ────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")
WHOP_API_KEY = os.environ.get("WHOP_API_KEY", "")
JWT_SECRET   = os.environ.get("JWT_SECRET", "insecure-default-change-me")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL   = "llama-3.1-8b-instant"
DEV_MODE     = os.environ.get("DEV_MODE", "").lower() == "true"
DEV_USER_ID  = os.environ.get("DEV_USER_ID", "dev_user_001")

IMAGES_DIR    = Path("images")
IMAGES_DIR.mkdir(exist_ok=True)
BRIDGE_SCRIPT = Path(__file__).parent / "chart_bridge.js"

# ── App ───────────────────────────────────────────────
app = FastAPI()

# ── Middleware ORDER matters ──────────────────────────
# In Starlette, the FIRST add_middleware call becomes the OUTERMOST wrapper.
# Outermost = last to process the response = can override all inner headers.
# EmbedHeadersMiddleware MUST be first so it runs last on responses and
# can guarantee X-Frame-Options is gone and CSP is correct regardless of
# what CORS or Railway's proxy injects.

class EmbedHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Delete X-Frame-Options entirely.
        # Setting it to ALLOWALL is NOT valid in modern browsers — delete it.
        response.headers.pop("x-frame-options", None)
        response.headers.pop("X-Frame-Options", None)

        # Allow any site to embed this page in an iframe.
        # frame-ancestors * is what makes Whop embedding work.
        response.headers["Content-Security-Policy"] = (
            "frame-ancestors *; "
            "default-src 'self' 'unsafe-inline' 'unsafe-eval' https: data: blob:;"
        )

        # Allow localStorage and cookies to work inside cross-origin iframes.
        response.headers["Cross-Origin-Opener-Policy"]   = "unsafe-none"
        response.headers["Cross-Origin-Embedder-Policy"] = "unsafe-none"

        return response

# Register EmbedHeaders FIRST (outermost, runs last on response)
app.add_middleware(EmbedHeadersMiddleware)

# Register CORS SECOND (inner, runs first on response)
# allow_origins=["*"] requires allow_credentials=False
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
app.mount("/images", StaticFiles(directory="images"), name="images")

# ── Basic routes ──────────────────────────────────────
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="6" fill="#0c0c0f"/>'
        '<text x="16" y="22" font-size="18" text-anchor="middle" fill="#818cf8">M</text>'
        '</svg>'
    )
    return Response(content=svg.encode(), media_type="image/svg+xml")

@app.get("/")
def root():
    return FileResponse("index.html")

@app.get("/health")
def health():
    """
    Railway pings this to confirm the app is alive.
    Also reports DB connectivity so you can diagnose from the Railway logs.
    """
    db_ok = False
    try:
        pool = get_pool()
        if pool:
            conn = pool.getconn()
            cur  = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            pool.putconn(conn)
            db_ok = True
    except Exception as e:
        print(f"Health check DB error: {e}")
    return {
        "status":   "ok",
        "db":       "connected" if db_ok else "disconnected",
        "dev_mode": DEV_MODE,
    }

# ── DB pool ───────────────────────────────────────────
_pool: Optional[ThreadedConnectionPool] = None

def _build_dsn(url: str) -> str:
    """
    Neon requires SSL. Append sslmode=require if not already present.
    Handles both clean URLs (use ?) and URLs already having params (use &).
    """
    if not url or "sslmode" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}sslmode=require"

def get_pool() -> Optional[ThreadedConnectionPool]:
    global _pool
    if _pool is None and DATABASE_URL:
        dsn = _build_dsn(DATABASE_URL)
        _pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=dsn,
            # Keepalives prevent Neon from silently closing idle connections.
            # Without these, the pool returns broken connections after ~5 min
            # of inactivity and every request fails until the pool is reset.
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )
    return _pool

@contextmanager
def db():
    pool = get_pool()
    if not pool:
        raise HTTPException(500, "Database not configured — check DATABASE_URL in Railway")

    conn = None
    try:
        conn = pool.getconn()
        # Validate the connection before use.
        # Neon can close idle connections; if so, get a fresh one.
        try:
            _cur = conn.cursor()
            _cur.execute("SELECT 1")
            _cur.close()
        except Exception:
            pool.putconn(conn, close=True)
            conn = pool.getconn()

        conn.autocommit = False
        yield conn
        conn.commit()

    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise

    finally:
        if conn:
            try:
                pool.putconn(conn)
            except Exception:
                pass

def rows(cursor) -> list:
    if not cursor.description:
        return []
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]

def row_one(cursor) -> Optional[dict]:
    if not cursor.description:
        return None
    cols = [d[0] for d in cursor.description]
    r    = cursor.fetchone()
    return dict(zip(cols, r)) if r else None

def _serialize(obj):
    """Recursively convert datetime/date objects to ISO strings for JSON."""
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj

# ── DB schema ─────────────────────────────────────────
def init_db():
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id              SERIAL PRIMARY KEY,
                user_id         TEXT NOT NULL,
                timestamp       TIMESTAMP NOT NULL,
                session_num     INTEGER DEFAULT 1,
                image_path      TEXT,
                state           TEXT,
                setup_type      TEXT,
                quality         TEXT,
                entry_behavior  TEXT,
                mgmt_behavior   TEXT,
                result          TEXT,
                r_multiple      REAL,
                execution_score INTEGER,
                direction       TEXT,
                session         TEXT,
                htf_aligned     TEXT,
                liq_flow        TEXT,
                position_size   REAL,
                instrument      TEXT DEFAULT 'MNQ',
                physical_state  TEXT,
                sleep_quality   TEXT,
                chart_symbol    TEXT,
                chart_tf        TEXT,
                chart_price     REAL,
                chart_levels    TEXT,
                dol_levels      TEXT,
                open_price      REAL,
                notes           TEXT,
                created_at      TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id              SERIAL PRIMARY KEY,
                user_id         TEXT NOT NULL,
                date            DATE NOT NULL,
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
                created_at      TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, date)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS streaks (
                id      SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                type    TEXT,
                date    DATE,
                UNIQUE(user_id, type, date)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_ts   ON trades(user_id, timestamp)")

# ── Auth ──────────────────────────────────────────────
def create_jwt(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.utcnow() + timedelta(hours=48),
        "iat": datetime.utcnow(),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")

def decode_jwt(token: str) -> str:
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload["sub"]
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired — reload the page")
    except Exception:
        raise HTTPException(401, "Invalid session token")

def current_user(request: Request) -> str:
    if DEV_MODE:
        return DEV_USER_ID
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    return decode_jwt(auth[7:])

@app.post("/auth/whop")
async def auth_whop(request: Request):
    if DEV_MODE:
        return {"jwt": create_jwt(DEV_USER_ID), "user_id": DEV_USER_ID}

    body  = await request.json()
    token = body.get("token", "").strip()
    if not token:
        raise HTTPException(400, "No token provided")

    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(
            "https://api.whop.com/v2/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code != 200:
        raise HTTPException(401, "Invalid or expired Whop token")

    data    = resp.json()
    user_id = data.get("id") or data.get("user", {}).get("id")
    if not user_id:
        raise HTTPException(401, "Could not identify Whop user")

    return {"jwt": create_jwt(str(user_id)), "user_id": str(user_id)}

@app.get("/auth/verify")
def auth_verify(uid: str = Depends(current_user)):
    return {"valid": True, "user_id": uid}

# ── Groq AI ───────────────────────────────────────────
_groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

def ai(prompt: str, max_tokens: int = 120) -> str:
    if not _groq:
        return "Add GROQ_API_KEY to Railway environment variables to enable AI."
    try:
        resp = _groq.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"AI unavailable: {str(e)[:80]}"

# ── Tags ──────────────────────────────────────────────
@app.get("/tags")
def get_tags():
    return {
        "state":  [{"id":"locked_in","label":"Locked In","icon":"🔒"},{"id":"neutral","label":"Neutral","icon":"😐"},{"id":"off","label":"Off Today","icon":"🌫️"},{"id":"tilt","label":"Tilt","icon":"🔥"}],
        "setup":  [{"id":"liq_sweep","label":"Liq. Sweep","icon":"🌊"},{"id":"trend_cont","label":"Trend Cont.","icon":"📈"},{"id":"reversal","label":"Reversal","icon":"🔄"},{"id":"breakout","label":"Breakout","icon":"💥"},{"id":"range","label":"Range","icon":"↔️"}],
        "quality":[{"id":"A","desc":"Clean."},{"id":"B","desc":"Valid."},{"id":"C","desc":"Forced."}],
        "result": [{"id":"win","label":"Win"},{"id":"loss","label":"Loss"},{"id":"be","label":"BE"}],
        "entry":  [{"id":"decisive","label":"Decisive","good":True},{"id":"hesitation","label":"Hesitation","good":False},{"id":"late","label":"Late","good":False},{"id":"early","label":"Early","good":False},{"id":"chase","label":"Chase","good":False}],
        "mgmt":   [{"id":"plan","label":"Followed Plan","good":True},{"id":"let_run","label":"Let Run","good":True},{"id":"cut_early","label":"Cut Early","good":False},{"id":"overheld","label":"Overheld","good":False},{"id":"moved_stop","label":"Moved Stop","good":False}],
        "r_presets":["+3R","+2R","+1.5R","+1R","+0.5R","BE","-0.5R","-1R","-2R"],
        "session": [{"id":"ny_am","label":"NY AM"},{"id":"ny_pre","label":"NY Pre"},{"id":"london","label":"London"},{"id":"ny_pm","label":"NY PM"},{"id":"asia","label":"Asia"}],
        "liq_flow":[{"id":"erl_to_irl","label":"ERL → IRL"},{"id":"irl_to_erl","label":"IRL → ERL"},{"id":"continuation","label":"Continuation"},{"id":"random","label":"No Clear Flow"}],
        "physical":[{"id":"fresh","label":"Fresh"},{"id":"tired","label":"Tired"},{"id":"wired","label":"Wired"},{"id":"hungry","label":"Hungry"}],
        "sleep":  [{"id":"good","label":"Good"},{"id":"ok","label":"OK"},{"id":"poor","label":"Poor"}],
        "instruments":["MNQ","NQ","ES","MES","YM","MYM","CL","GC"],
    }

@app.get("/system")
def system(uid: str = Depends(current_user)):
    return {
        "ai_enabled":     bool(_groq),
        "ai_provider":    "Groq (llama-3.1-8b-instant)" if _groq else "not configured",
        "bridge_available": BRIDGE_SCRIPT.exists(),
        "dev_mode":       DEV_MODE,
    }

# ── Log trade ─────────────────────────────────────────
def _session_num(conn, user_id: str) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM trades WHERE user_id=%s AND timestamp::date=CURRENT_DATE",
        (user_id,)
    )
    return cur.fetchone()[0] + 1

@app.post("/trade")
async def log_trade(
    state:          Optional[str]   = Form(None),
    setup_type:     Optional[str]   = Form(None),
    quality:        Optional[str]   = Form(None),
    entry_behavior: Optional[str]   = Form(None),
    mgmt_behavior:  Optional[str]   = Form(None),
    result:         Optional[str]   = Form(None),
    r_multiple:     Optional[float] = Form(None),
    execution_score:Optional[int]   = Form(None),
    direction:      Optional[str]   = Form(None),
    session:        Optional[str]   = Form(None),
    htf_aligned:    Optional[str]   = Form(None),
    liq_flow:       Optional[str]   = Form(None),
    position_size:  Optional[float] = Form(None),
    instrument:     Optional[str]   = Form("MNQ"),
    physical_state: Optional[str]   = Form(None),
    sleep_quality:  Optional[str]   = Form(None),
    chart_symbol:   Optional[str]   = Form(None),
    chart_tf:       Optional[str]   = Form(None),
    chart_price:    Optional[float] = Form(None),
    chart_levels:   Optional[str]   = Form(None),
    dol_levels:     Optional[str]   = Form(None),
    open_price:     Optional[float] = Form(None),
    notes:          Optional[str]   = Form(None),
    image:          Optional[UploadFile] = File(None),
    request: Request = None,
):
    uid        = current_user(request)
    timestamp  = datetime.now()
    image_path = None

    if image and image.filename:
        ext       = Path(image.filename).suffix or ".png"
        fname     = f"{timestamp.strftime('%Y%m%d_%H%M%S_%f')}{ext}"
        disk_path = IMAGES_DIR / fname
        with open(disk_path, "wb") as f:
            shutil.copyfileobj(image.file, f)
        image_path = f"images/{fname}"

    with db() as conn:
        sess_num = _session_num(conn, uid)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO trades (
                user_id, timestamp, session_num, image_path,
                state, setup_type, quality, entry_behavior, mgmt_behavior,
                result, r_multiple, execution_score,
                direction, session, htf_aligned, liq_flow,
                position_size, instrument, physical_state, sleep_quality,
                chart_symbol, chart_tf, chart_price, chart_levels,
                dol_levels, open_price, notes
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            uid, timestamp, sess_num, image_path,
            state, setup_type, quality, entry_behavior, mgmt_behavior,
            result, r_multiple, execution_score,
            direction, session, htf_aligned, liq_flow,
            position_size, instrument, physical_state, sleep_quality,
            chart_symbol, chart_tf, chart_price, chart_levels,
            dol_levels, open_price, notes
        ))
        trade_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO streaks (user_id,type,date) VALUES (%s,'logged',CURRENT_DATE) ON CONFLICT DO NOTHING",
            (uid,)
        )

    return {"success": True, "trade_id": trade_id, "session_num": sess_num}

@app.put("/trade/{tid}/result")
async def update_result(
    tid:        int,
    result:     Optional[str]   = Form(None),
    r_multiple: Optional[float] = Form(None),
    request: Request = None,
):
    uid = current_user(request)
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE trades SET result=%s, r_multiple=%s WHERE id=%s AND user_id=%s",
            (result, r_multiple, tid, uid)
        )
    return {"success": True}

@app.delete("/trade/{tid}")
def delete_trade(tid: int, uid: str = Depends(current_user)):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM trades WHERE id=%s AND user_id=%s", (tid, uid))
    return {"success": True}

# ── Today stats ───────────────────────────────────────
def _streak(conn, uid: str, stype: str) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT date FROM streaks WHERE user_id=%s AND type=%s ORDER BY date DESC",
        (uid, stype)
    )
    results = cur.fetchall()
    if not results:
        return 0
    streak = 0
    check  = date.today()
    for (d,) in results:
        if d == check or (streak > 0 and d == check - timedelta(days=1)):
            streak += 1
            check   = d - timedelta(days=1)
        else:
            break
    return streak

@app.get("/stats/today")
def today_stats(uid: str = Depends(current_user)):
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM trades WHERE user_id=%s AND timestamp::date=CURRENT_DATE ORDER BY timestamp",
            (uid,)
        )
        trades  = _serialize(rows(cur))
        cur.execute("SELECT * FROM sessions WHERE user_id=%s AND date=CURRENT_DATE", (uid,))
        session    = _serialize(row_one(cur))
        log_streak = _streak(conn, uid, "logged")

    r_trades = [t for t in trades if t["r_multiple"] is not None]
    total    = len(trades)
    total_r  = round(sum(t["r_multiple"] for t in r_trades), 2)
    wins     = sum(1 for t in r_trades if t["r_multiple"] > 0)
    losses   = sum(1 for t in r_trades if t["r_multiple"] < 0)
    be       = sum(1 for t in r_trades if t["r_multiple"] == 0)
    win_rate = round(wins/len(r_trades)*100) if r_trades else 0

    q = {"A":0,"B":0,"C":0}
    for t in trades:
        if t.get("quality") in q:
            q[t["quality"]] += 1

    last_result = None
    consecutive_losses = 0
    for t in sorted(trades, key=lambda x: x["timestamp"]):
        r, res = t["r_multiple"], t.get("result")
        if r is not None:
            if r < 0:  consecutive_losses += 1; last_result = "loss"
            else:      consecutive_losses = 0;  last_result = "win" if r > 0 else "be"
        elif res:
            if res == "loss": consecutive_losses += 1; last_result = "loss"
            else:             consecutive_losses = 0;  last_result = res

    am       = sum(1 for t in trades if t.get("session") in ["ny_am","ny_pre"])
    execs    = [t["execution_score"] for t in trades if t.get("execution_score")]
    avg_exec = round(sum(execs)/len(execs), 1) if execs else 0

    return {
        "date": date.today().isoformat(), "total": total,
        "wins": wins, "losses": losses, "be": be,
        "total_r": total_r, "win_rate": win_rate, "avg_exec": avg_exec,
        "quality": q, "trades": trades,
        "last_result": last_result,
        "consecutive_losses": consecutive_losses,
        "log_streak": log_streak, "am_trades": am,
        "session": session,
        "guardrails": {
            "over_2":    total >= 2,
            "over_3":    total >= 3,
            "over_am":   am >= 2,
            "tilt":      any(t["state"] == "tilt" for t in trades),
            "post_loss": last_result == "loss",
            "post_2loss":consecutive_losses >= 2,
            "walk_away": total_r > 0 and losses == 0 and total >= 1,
        }
    }

# ── Session close ─────────────────────────────────────
@app.post("/session/close")
def close_session(uid: str = Depends(current_user)):
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM trades WHERE user_id=%s AND timestamp::date=CURRENT_DATE ORDER BY timestamp",
            (uid,)
        )
        trades = _serialize(rows(cur))

    if not trades:
        return {"error": "No trades today"}

    r_trades = [t for t in trades if t["r_multiple"] is not None]
    total    = len(trades)
    total_r  = round(sum(t["r_multiple"] for t in r_trades), 2)
    wins     = sum(1 for t in r_trades if t["r_multiple"] > 0)
    win_rate = round(wins/len(r_trades)*100) if r_trades else 0

    q = {"A":0,"B":0,"C":0}
    for t in trades:
        if t.get("quality") in q:
            q[t["quality"]] += 1

    q_avg     = sum({"A":100,"B":65,"C":10}.get(t.get("quality","C"),50) for t in trades) / total
    execs     = [t["execution_score"] for t in trades if t.get("execution_score")]
    e_avg     = (sum(execs)/len(execs)*10) if execs else 50
    b_score   = sum(1 for t in trades if t.get("entry_behavior")=="decisive") / total * 100
    craftsman = max(0, min(100, round(q_avg*.45 + e_avg*.35 + b_score*.2 - q["C"]*15)))

    if craftsman >= 75 and q["C"] == 0 and total <= 2: grade = "A"
    elif craftsman >= 55 and q["C"] <= 1:              grade = "B"
    else:                                              grade = "C"

    sc = {}
    for t in trades:
        sc[t.get("state","neutral")] = sc.get(t.get("state","neutral"), 0) + 1
    dominant = max(sc, key=sc.get) if sc else "neutral"

    leaks = {
        "Entries":    sum(1 for t in trades if t.get("entry_behavior") in ["hesitation","late","early","chase"]),
        "Management": sum(1 for t in trades if t.get("mgmt_behavior") in ["cut_early","overheld","moved_stop"]),
        "Psychology": sum(1 for t in trades if t.get("state")=="tilt") + q["C"],
        "Overtrading":1 if total > 2 else 0,
    }
    biggest_leak = max(leaks, key=leaks.get)

    s_r = sorted([t for t in trades if t.get("r_multiple") is not None], key=lambda x: x["timestamp"])
    post_loss = [s_r[i]["r_multiple"] for i in range(1,len(s_r)) if s_r[i-1]["r_multiple"] < 0]

    lines = []
    if q["C"] > 0:
        c_r = [t["r_multiple"] for t in r_trades if t.get("quality")=="C"]
        if c_r: lines.append(f"{q['C']} C-setup{'s' if q['C']>1 else ''}: {sum(c_r):+.1f}R.")
    if post_loss:
        lines.append(f"After loss: averaged {sum(post_loss)/len(post_loss):+.2f}R next trade.")
    if total > 2:
        lines.append(f"{total} trades — above 2-trade structure.")
    if q["C"] == 0 and total <= 2:
        lines.append(f"Clean — {total} trade{'s' if total>1 else ''}, no C-setups.")
    pattern = " ".join(lines) if lines else "Session logged."

    signals = {
        "Entries":    "If hesitation is present before entry, the setup has already passed.",
        "Management": "The plan was made when you were clear-headed. Trust that version.",
        "Psychology": "State before size. Tilt is the variable — not the market.",
        "Overtrading":f"Two trades is the structure. Trade {total} was outside it.",
    }
    signal = (signals.get(biggest_leak, "One trade at a time.") if grade != "A"
              else "That's the standard. Replicate the state, replicate the result.")

    mirror = _gen_mirror(r_trades, grade, biggest_leak, dominant, q)

    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sessions
                (user_id,date,grade,craftsman_score,dominant_state,
                 biggest_leak,pattern_note,one_signal,total_r,win_rate,trade_count,ai_mirror)
            VALUES (%s,CURRENT_DATE,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,date) DO UPDATE SET
                grade=%s,craftsman_score=%s,dominant_state=%s,
                biggest_leak=%s,pattern_note=%s,one_signal=%s,
                total_r=%s,win_rate=%s,trade_count=%s,ai_mirror=%s
        """, (
            uid, grade, craftsman, dominant, biggest_leak,
            pattern, signal, total_r, win_rate, total, mirror,
            grade, craftsman, dominant, biggest_leak,
            pattern, signal, total_r, win_rate, total, mirror,
        ))
        cur.execute(
            "INSERT INTO streaks (user_id,type,date) VALUES (%s,'closed',CURRENT_DATE) ON CONFLICT DO NOTHING",
            (uid,)
        )

    return {
        "grade": grade, "craftsman_score": craftsman, "dominant_state": dominant,
        "biggest_leak": biggest_leak, "pattern_note": pattern, "one_signal": signal,
        "total_r": total_r, "win_rate": win_rate, "trade_count": total,
        "quality": q, "ai_mirror": mirror,
    }

def _gen_mirror(r_trades, grade, biggest_leak, dominant, q):
    a_r   = [t["r_multiple"] for t in r_trades if t.get("quality")=="A"]
    c_r   = [t["r_multiple"] for t in r_trades if t.get("quality")=="C"]
    a_tot = round(sum(a_r), 2) if a_r else None
    c_tot = round(sum(c_r), 2) if c_r else None
    ctx   = []
    if a_tot is not None: ctx.append(f"A-quality: {a_tot:+.1f}R ({len(a_r)} trades)")
    if c_tot is not None: ctx.append(f"C-quality: {c_tot:+.1f}R ({len(c_r)} trades)")
    ctx.append(f"State:{dominant} Leak:{biggest_leak}")
    prompt = (
        f"Session: {', '.join(ctx)}. Grade:{grade}.\n"
        "Write ONE sentence: what the data shows about today's execution.\n"
        "Then ONE sentence: craftsman framing (not advice, not 'you should').\n"
        "Under 40 words total. No preamble:"
    )
    raw = ai(prompt, max_tokens=80)
    if any(p in raw.lower() for p in ["you should","you need","try to","make sure"]):
        parts = []
        if a_tot is not None: parts.append(f"A-quality generated {a_tot:+.1f}R.")
        if c_tot is not None and c_tot < 0: parts.append(f"C-setups cost {c_tot:.1f}R.")
        return " ".join(parts) if parts else f"Session grade: {grade}."
    return raw

# ── Analytics ─────────────────────────────────────────
@app.get("/analytics")
def analytics(days: int = 30, uid: str = Depends(current_user)):
    cutoff = datetime.now() - timedelta(days=days)
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM trades WHERE user_id=%s AND timestamp >= %s ORDER BY timestamp",
            (uid, cutoff)
        )
        trades = _serialize(rows(cur))
        cur.execute(
            "SELECT * FROM sessions WHERE user_id=%s ORDER BY date DESC LIMIT 20",
            (uid,)
        )
        sessions   = _serialize(rows(cur))
        log_streak = _streak(conn, uid, "logged")

    if not trades:
        return {"empty": True, "log_streak": log_streak}

    r_trades = [t for t in trades if t["r_multiple"] is not None]
    total    = len(trades)
    wins     = sum(1 for t in r_trades if t["r_multiple"] > 0)
    losses   = sum(1 for t in r_trades if t["r_multiple"] < 0)
    total_r  = round(sum(t["r_multiple"] for t in r_trades), 2)
    win_rate = round(wins/len(r_trades)*100, 1) if r_trades else 0

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
        p["avg_r"] = round(p["total_r"]/p["total"], 2)  if p["total"] else 0

    a, c = qperf["A"], qperf["C"]
    edge = {"text":"Log more A-quality trades to define your edge.","ready":False} if a["total"] < 3 else {
        "text": (f"A-quality: {a['wr']}% win rate · {'+' if a['total_r']>=0 else ''}{a['total_r']}R total." +
                 (f"  C-setups: {c['total_r']}R cost." if c["total"] > 0 else "")),
        "ready": True
    }

    def perf_by(key):
        d = {}
        for t in r_trades:
            v = t.get(key, "?")
            if v not in d: d[v] = {"total":0,"wins":0,"total_r":0}
            d[v]["total"] += 1
            d[v]["total_r"] = round(d[v]["total_r"] + t["r_multiple"], 2)
            if t["r_multiple"] > 0: d[v]["wins"] += 1
        for v in d:
            p = d[v]
            p["wr"]    = round(p["wins"]/p["total"]*100, 1) if p["total"] else 0
            p["avg_r"] = round(p["total_r"]/p["total"], 2)  if p["total"] else 0
        return d

    def leak(key, bad):
        bg = [t for t in r_trades if t.get(key) in bad]
        gd = [t for t in r_trades if t.get(key) and t.get(key) not in bad]
        return {
            "bad_avg_r":  round(sum(t["r_multiple"] for t in bg)/len(bg), 2) if bg else None,
            "good_avg_r": round(sum(t["r_multiple"] for t in gd)/len(gd), 2) if gd else None,
            "bad_n": len(bg), "good_n": len(gd)
        }

    s_t = sorted(r_trades, key=lambda t: t["timestamp"])
    wal = {"total":0,"wins":0}
    cal = {"total":0,"c":0}
    for i, t in enumerate(s_t):
        if i == 0: continue
        prev = s_t[i-1]
        same = t["timestamp"][:10] == prev["timestamp"][:10]
        if same and prev["r_multiple"] < 0:
            wal["total"] += 1
            if t["r_multiple"] > 0: wal["wins"] += 1
            cal["total"] += 1
            if t.get("quality") == "C": cal["c"] += 1

    daily = {}
    for t in s_t:
        d = t["timestamp"][:10]
        daily[d] = round(daily.get(d, 0) + t["r_multiple"], 2)
    cum = 0
    cum_r = []
    for d, r in sorted(daily.items()):
        cum = round(cum + r, 2)
        cum_r.append({"date":d,"daily":r,"cumulative":cum})

    return {
        "total":total,"wins":wins,"losses":losses,"win_rate":win_rate,"total_r":total_r,
        "quality":qperf,"edge":edge,"state_perf":perf_by("state"),
        "entry_leak":leak("entry_behavior",["hesitation","late","early","chase"]),
        "mgmt_leak": leak("mgmt_behavior", ["cut_early","overheld","moved_stop"]),
        "setup_perf":perf_by("setup_type"),
        "sequence":{
            "win_after_loss": round(wal["wins"]/wal["total"]*100, 1) if wal["total"] else None,
            "c_after_loss":   round(cal["c"]/cal["total"]*100, 1)   if cal["total"] else None,
            "samples": wal["total"]
        },
        "cum_r":cum_r,"sessions":sessions,"log_streak":log_streak,
    }

@app.get("/analyze")
def analyze(days: int = 7, uid: str = Depends(current_user)):
    cutoff = datetime.now() - timedelta(days=days)
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM trades WHERE user_id=%s AND timestamp >= %s ORDER BY timestamp",
            (uid, cutoff)
        )
        trades = _serialize(rows(cur))
    if not trades:
        return {"analysis": "No trades in this period."}
    r_trades = [t for t in trades if t["r_multiple"] is not None]
    total_r  = round(sum(t["r_multiple"] for t in r_trades), 2)
    lines = [
        f"[{t['timestamp'][:10]}] #{t['session_num']} State:{t.get('state','?')} "
        f"Q:{t.get('quality','?')} Setup:{t.get('setup_type','?')} "
        f"Entry:{t.get('entry_behavior','?')} R:{t.get('r_multiple','?')}"
        for t in trades
    ]
    prompt = (
        f"Analyze {days} days. Total R: {total_r:+.1f}.\n\n" + "\n".join(lines) +
        "\n\nYOUR EDGE: [1 sentence]\nYOUR LEAK: [1 sentence with data]\n"
        "TOMORROW: [1 rule under 12 words]\n\nNo praise. Pattern recognition only."
    )
    return {"analysis": ai(prompt, max_tokens=200)}

# ── Trade history ─────────────────────────────────────
@app.get("/trades")
def get_trades(
    limit: int = 100,
    date_filter: Optional[str] = None,
    uid: str = Depends(current_user)
):
    with db() as conn:
        cur = conn.cursor()
        if date_filter:
            cur.execute(
                "SELECT * FROM trades WHERE user_id=%s AND timestamp::date=%s ORDER BY timestamp DESC LIMIT %s",
                (uid, date_filter, limit)
            )
        else:
            cur.execute(
                "SELECT * FROM trades WHERE user_id=%s ORDER BY timestamp DESC LIMIT %s",
                (uid, limit)
            )
        return _serialize(rows(cur))

# ── Chart bridge ──────────────────────────────────────
def _bridge(command: str, extra: str = None, timeout: int = 22) -> dict:
    if not BRIDGE_SCRIPT.exists():
        return {"error": "chart_bridge.js not found — local bridge not set up"}
    args = ["node", str(BRIDGE_SCRIPT), command]
    if extra: args.append(extra)
    try:
        r   = subprocess.run(args, capture_output=True, text=True,
                             timeout=timeout, cwd=str(BRIDGE_SCRIPT.parent))
        out = (r.stdout or "").strip()
        if not out: return {"error": r.stderr.strip()[:200] or "No output"}
        for line in reversed(out.split("\n")):
            line = line.strip()
            if line.startswith("{"): return json.loads(line)
        return json.loads(out)
    except subprocess.TimeoutExpired: return {"error": "Timed out"}
    except Exception as e:            return {"error": str(e)}

@app.get("/chart/status")
def chart_status(uid: str = Depends(current_user)):
    return _bridge("status", timeout=6)

@app.get("/chart/debug")
def chart_debug(uid: str = Depends(current_user)):
    return _bridge("debug", timeout=28)

@app.get("/chart/read")
def chart_read(uid: str = Depends(current_user)):
    return _bridge("read", timeout=22)

@app.post("/chart/screenshot")
async def chart_screenshot(uid: str = Depends(current_user)):
    import time as _t
    fname     = f"tv_{int(_t.time()*1000)}.png"
    save_path = str(IMAGES_DIR / fname)
    result    = _bridge("screenshot", save_path, timeout=20)
    if result.get("success") and Path(save_path).exists():
        return {"success": True, "filename": fname}
    return {"success": False, "error": result.get("error", "Screenshot failed")}

# ── Startup ───────────────────────────────────────────
@app.on_event("startup")
def startup():
    if not DATABASE_URL:
        print("⚠️  DATABASE_URL not set — database features disabled")
        return
    try:
        init_db()
        print("✅ Database initialized")
    except Exception as e:
        print(f"⚠️  DB init error (will retry on first request): {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))