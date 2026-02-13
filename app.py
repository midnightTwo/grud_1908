"""
Discord Nitro Promo Monitor — Railway-ready
- Bulk add/remove promo codes via UI
- Per-code activity log with timestamps
- SHA-256 proof hashes for dispute evidence
- Auto-check every 60s, live dashboard updates every 3s
"""

import hashlib
import json
import os
import re
import time
import threading
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request, send_file
from flask_cors import CORS

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

# ── Config ──────────────────────────────────────────────────────────────────
CHECK_INTERVAL = 60          # seconds between full check cycles
REQUEST_DELAY  = 2.0         # seconds between individual API calls
MAX_RETRIES    = 5           # retries on 429
DATA_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
API_URL        = "https://discord.com/api/v10/entitlements/gift-codes/{code}"

# ── Persistent state ────────────────────────────────────────────────────────
state = {
    "codes": {},    # code -> { status, detail, redeemed_by_username, redeemed_by_id, ... }
    "events": [],   # chronological event log
}
lock = threading.Lock()
last_check_time = "Never"
checking_now = False


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_display() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def load_data():
    global state
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass
    if "codes" not in state:
        state["codes"] = {}
    if "events" not in state:
        state["events"] = []


def save_data():
    with lock:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)


def extract_codes(text: str) -> list:
    """Extract promo codes from any text — supports various URL formats."""
    patterns = [
        r"promos\.discord\.gg/([A-Za-z0-9]+)",
        r"discord\.gift/([A-Za-z0-9]+)",
        r"discord\.com/billing/promotions/([A-Za-z0-9]+)",
    ]
    codes = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        found = False
        for pat in patterns:
            m = re.search(pat, line)
            if m:
                codes.append(m.group(1))
                found = True
                break
        if not found and re.match(r'^[A-Za-z0-9]{16,48}$', line):
            codes.append(line)
    return codes


def add_codes(codes: list) -> dict:
    added = 0
    dupes = 0
    with lock:
        for code in codes:
            if code in state["codes"]:
                dupes += 1
            else:
                state["codes"][code] = {
                    "code": code,
                    "status": "PENDING",
                    "detail": "Awaiting first check",
                    "redeemed_by_username": None,
                    "redeemed_by_id": None,
                    "added_at": _now_iso(),
                    "checked_at": None,
                }
                event = {
                    "timestamp": _now_iso(),
                    "code": code,
                    "url": f"promos.discord.gg/{code}",
                    "type": "ADDED",
                    "old_status": None,
                    "new_status": "PENDING",
                    "redeemed_by_username": None,
                    "redeemed_by_id": None,
                }
                event["proof_hash"] = _sha256(json.dumps(event, sort_keys=True))
                state["events"].append(event)
                added += 1
    save_data()
    return {"added": added, "duplicates": dupes}


def remove_codes(codes: list) -> int:
    removed = 0
    with lock:
        for code in codes:
            if code in state["codes"]:
                event = {
                    "timestamp": _now_iso(),
                    "code": code,
                    "url": f"promos.discord.gg/{code}",
                    "type": "REMOVED",
                    "old_status": state["codes"][code].get("status"),
                    "new_status": "REMOVED",
                    "redeemed_by_username": state["codes"][code].get("redeemed_by_username"),
                    "redeemed_by_id": state["codes"][code].get("redeemed_by_id"),
                }
                event["proof_hash"] = _sha256(json.dumps(event, sort_keys=True))
                state["events"].append(event)
                del state["codes"][code]
                removed += 1
    save_data()
    return removed


def check_single(code: str) -> dict:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(
                API_URL.format(code=code),
                headers={"Accept": "application/json"},
                timeout=10,
            )
            now = _now_iso()

            if r.status_code == 200:
                data = r.json()
                redeemed = data.get("redeemed", False)
                user = data.get("user") or {}
                return {
                    "code": code,
                    "status": "REDEEMED" if redeemed else "VALID",
                    "redeemed_by_username": user.get("username"),
                    "redeemed_by_id": user.get("id"),
                    "checked_at": now,
                    "detail": "Already activated" if redeemed else data.get("store_listing", {}).get("sku", {}).get("name", "Active"),
                }
            elif r.status_code == 404:
                return {"code": code, "status": "INVALID", "detail": "Not found / expired",
                        "checked_at": now, "redeemed_by_username": None, "redeemed_by_id": None}
            elif r.status_code == 429:
                wait = float(r.headers.get("Retry-After", "5")) + 0.5
                time.sleep(wait)
                continue
            else:
                return {"code": code, "status": "ERROR", "detail": f"HTTP {r.status_code}",
                        "checked_at": now, "redeemed_by_username": None, "redeemed_by_id": None}
        except Exception as e:
            return {"code": code, "status": "ERROR", "detail": str(e),
                    "checked_at": _now_iso(), "redeemed_by_username": None, "redeemed_by_id": None}

    return {"code": code, "status": "RATE_LIMITED", "detail": f"Still limited after {MAX_RETRIES} retries",
            "checked_at": _now_iso(), "redeemed_by_username": None, "redeemed_by_id": None}


def run_full_check():
    global last_check_time, checking_now
    if checking_now:
        return
    checking_now = True
    codes_list = list(state["codes"].keys())
    last_check_time = _now_display()

    for code in codes_list:
        if code not in state["codes"]:
            continue
        result = check_single(code)
        with lock:
            if code not in state["codes"]:
                continue
            prev = state["codes"][code]
            old_status = prev.get("status")

            if old_status != result["status"]:
                event = {
                    "timestamp": result.get("checked_at", _now_iso()),
                    "code": code,
                    "url": f"promos.discord.gg/{code}",
                    "type": "STATUS_CHANGE",
                    "old_status": old_status,
                    "new_status": result["status"],
                    "redeemed_by_username": result.get("redeemed_by_username"),
                    "redeemed_by_id": result.get("redeemed_by_id"),
                    "detail": result.get("detail", ""),
                }
                event["proof_hash"] = _sha256(json.dumps(event, sort_keys=True))
                state["events"].append(event)

            state["codes"][code].update({
                "status": result["status"],
                "detail": result.get("detail", ""),
                "redeemed_by_username": result.get("redeemed_by_username"),
                "redeemed_by_id": result.get("redeemed_by_id"),
                "checked_at": result.get("checked_at"),
            })

        time.sleep(REQUEST_DELAY)

    save_data()
    checking_now = False


def background_checker():
    while True:
        try:
            if state["codes"]:
                run_full_check()
        except Exception as e:
            print(f"[checker error] {e}")
        time.sleep(CHECK_INTERVAL)


# ── Routes ──────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    codes_list = []
    with lock:
        for code, info in state["codes"].items():
            codes_list.append(info)
    stats = {
        "valid": sum(1 for c in codes_list if c.get("status") == "VALID"),
        "redeemed": sum(1 for c in codes_list if c.get("status") == "REDEEMED"),
        "invalid": sum(1 for c in codes_list if c.get("status") == "INVALID"),
        "pending": sum(1 for c in codes_list if c.get("status") == "PENDING"),
        "error": sum(1 for c in codes_list if c.get("status") in ("ERROR", "RATE_LIMITED")),
    }
    return jsonify({
        "last_check": last_check_time,
        "checking_now": checking_now,
        "total_codes": len(codes_list),
        "stats": stats,
        "codes": codes_list,
        "events": list(reversed(state["events"][-200:])),
    })


@app.route("/api/codes", methods=["POST"])
def api_add_codes():
    text = request.json.get("text", "")
    codes = extract_codes(text)
    if not codes:
        return jsonify({"error": "No valid codes found in input"}), 400
    result = add_codes(codes)
    threading.Thread(target=run_full_check, daemon=True).start()
    return jsonify(result)


@app.route("/api/codes", methods=["DELETE"])
def api_delete_codes():
    codes = request.json.get("codes", [])
    removed = remove_codes(codes)
    return jsonify({"removed": removed})


@app.route("/api/codes/clear", methods=["POST"])
def api_clear_all():
    with lock:
        all_codes = list(state["codes"].keys())
    removed = remove_codes(all_codes)
    return jsonify({"removed": removed})


@app.route("/api/check-now", methods=["POST"])
def api_check_now():
    if checking_now:
        return jsonify({"ok": False, "message": "Already checking"})
    threading.Thread(target=run_full_check, daemon=True).start()
    return jsonify({"ok": True, "message": "Check started"})


@app.route("/api/events/<code>")
def api_code_events(code):
    with lock:
        events = [e for e in state["events"] if e.get("code") == code]
    return jsonify(list(reversed(events)))


@app.route("/api/proof")
def api_proof():
    if os.path.exists(DATA_FILE):
        return send_file(DATA_FILE, as_attachment=True, download_name="nitro_proof_log.json")
    return jsonify({"error": "No log yet"}), 404


# Start background checker on import (works with gunicorn --preload)
load_data()
_checker_thread = threading.Thread(target=background_checker, daemon=True)
_checker_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Monitor → http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
