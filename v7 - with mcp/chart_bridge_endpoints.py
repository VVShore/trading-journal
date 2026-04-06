"""
Chart Bridge Endpoints — add these to your existing v7 main.py

Add this block anywhere after your existing imports and before app.mount("/images"...)

Requirements added to requirements.txt: none (uses subprocess)
New system requirement: Node.js 18+, tradingview-mcp installed
"""

# ── Add these imports to the top of main.py ──────────
# (already imported in v7: subprocess doesn't need adding if it's standard)
import subprocess
import sys
import os

# ── Add this config near the top of main.py ──────────
BRIDGE_SCRIPT = os.path.join(os.path.dirname(__file__), "chart_bridge.js")
BRIDGE_TIMEOUT = 20  # seconds — reading all indicator data can take up to 15s


# ── Add these endpoints to main.py ───────────────────

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
